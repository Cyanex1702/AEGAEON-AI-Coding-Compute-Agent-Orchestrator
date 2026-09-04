from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from aegaeon.agents.deterministic import BrokeBoyPlanner
from aegaeon.database.models import ContractRevisionRecord, TaskRecord
from aegaeon.database.session import Database
from aegaeon.failures import ClassifiedFailure, FailureCode
from aegaeon.models.results import ChangeOperation, ContractProposal
from aegaeon.orchestration import OrchestrationArchitectureService
from aegaeon.orchestrator.orchestrator import Orchestrator
from aegaeon.projects.manager import ProjectManager
from aegaeon.protocol.schemas import PlanTask, ProjectCreate


def prepared_project(tmp_path, prompt: str, worker_count: int = 1):
    database = Database(f"sqlite:///{tmp_path / 'governance.db'}")
    database.create_all()
    projects = ProjectManager(database, tmp_path / "projects")
    request = ProjectCreate(prompt=prompt, worker_count=worker_count)
    project = projects.create(request)
    architecture = OrchestrationArchitectureService(database, projects)
    plan = architecture.prepare(
        project.id,
        project.prompt,
        BrokeBoyPlanner().plan(prompt, worker_count),
        project.options,
        projects.current_revision(project.id),
    )
    return database, projects, project, architecture, plan


def test_core_creates_and_materializes_rich_contract_v1(tmp_path) -> None:
    database, projects, project, architecture, plan = prepared_project(
        tmp_path,
        "Build a calculator with add, subtract, multiply, divide, and tests.",
    )
    contract = architecture.contract(project.id)
    materialized = projects.repo_path(project.id) / "contracts" / "CanonicalContract.json"
    content = json.loads(materialized.read_text(encoding="utf-8"))

    assert contract.version == 1
    assert content["contract_version"] == 1
    assert content["project"] == "Calculator"
    assert content["services"]["CalculatorService"]["methods"] == [
        "add",
        "subtract",
        "multiply",
        "divide",
    ]
    assert len(architecture.summary(project.id)["milestones"]) == 2
    assert [task.id for task in plan.tasks] == [
        "plan",
        "foundation",
        "implementation",
        "tests",
        "verification",
        "review",
    ]
    assert all(task.milestone_key for task in plan.tasks)
    database.close()


def test_proposal_approval_is_exact_versioned_and_core_materialized(tmp_path) -> None:
    database, projects, project, architecture, plan = prepared_project(
        tmp_path,
        "Build a scheduling app with recurring events and timezone-aware reminders.",
    )
    foundation = next(task for task in plan.tasks if task.id == "foundation")
    context = architecture.task_contract_context(project.id, foundation)
    with database.session() as session:
        session.add(
            TaskRecord(
                id="task-foundation",
                project_id=project.id,
                key="foundation",
                title="Foundation",
                description="fixture",
                milestone_id=context["milestone_id"],
                contract_version=1,
                allowed_files=["**/*"],
                status="RUNNING",
            )
        )
        session.add(
            TaskRecord(
                id="task-future",
                project_id=project.id,
                key="future",
                title="Future",
                description="fixture",
                milestone_id=context["milestone_id"],
                contract_version=1,
                allowed_files=["**/*"],
                status="PENDING",
            )
        )
    proposal = ContractProposal(
        proposal_id="proposal-schedule-timezone",
        type="add_field",
        target="shared_types.Schedule.timezone",
        current_value=None,
        proposed_value={"type": "string"},
        expected_revision=1,
        reason="Recurring schedules require timezone awareness.",
    )
    outcome = architecture.review_proposals(
        project.id, "task-foundation", "worker-one", [proposal]
    )[0]

    assert outcome["approved"] is True
    assert outcome["old_version"] == 1
    assert outcome["new_version"] == 2
    contract = architecture.contract(project.id)
    assert contract.content["shared_types"]["Schedule"]["timezone"] == {"type": "string"}
    assert "AuthContract" not in contract.content
    materialized = json.loads(
        (projects.repo_path(project.id) / "contracts" / "CanonicalContract.json").read_text(
            encoding="utf-8"
        )
    )
    assert materialized["contract_version"] == 2
    with database.session() as session:
        assert session.get(TaskRecord, "task-future").contract_version == 2
        revisions = session.scalars(
            select(ContractRevisionRecord).order_by(ContractRevisionRecord.version)
        ).all()
        assert [item.version for item in revisions] == [1, 2]
        assert revisions[-1].previous_version == 1
        assert "task-future" in revisions[-1].affected_tasks

    current_task = TaskRecord(
        id="detached-current",
        project_id=project.id,
        key="foundation",
        title="Foundation",
        description="fixture",
        contract_version=2,
        allowed_files=["**/*"],
    )
    with pytest.raises(ClassifiedFailure) as failure:
        architecture.validate_worker_result(
            current_task,
            [
                ChangeOperation(
                    operation="update",
                    path="contracts/AuthContract.json",
                    content="{}",
                )
            ],
            [proposal],
        )
    assert failure.value.code == FailureCode.CONTRACT_PROPOSAL_REQUIRED
    assert failure.value.diagnostics["failure_stage"] == "CONTRACT_VALIDATION"
    assert failure.value.diagnostics["actual_path"] == "contracts/AuthContract.json"

    rejected = ContractProposal(
        type="remove",
        target="shared_types.Schedule.timezone",
        current_value={"type": "string"},
        proposed_value={"removed": True},
        expected_revision=2,
        reason="Worker preference",
    )
    rejected_outcome = architecture.review_proposals(
        project.id, "task-foundation", "worker-two", [rejected]
    )[0]
    assert rejected_outcome["approved"] is False
    assert architecture.contract(project.id).version == 2
    database.close()


def test_milestones_generate_scaled_dags_independent_of_worker_count(tmp_path) -> None:
    small_db, _, small_project, small_architecture, small_plan = prepared_project(
        tmp_path / "small", "Build a calculator with add and subtract plus tests.", worker_count=1
    )
    large_prompt = """Build a multi-user analytics dashboard with:
authentication
security roles
calendar scheduling
recurring notifications
offline sync
mobile responsive UI
persistence
external integration
cloud deployment
"""
    large_db, _, large_project, large_architecture, large_plan = prepared_project(
        tmp_path / "large", large_prompt, worker_count=1
    )

    assert len(small_architecture.summary(small_project.id)["milestones"]) == 2
    assert len(small_plan.tasks) == 6
    large_state = large_architecture.summary(large_project.id)
    assert len(large_state["milestones"]) == 6
    assert len(large_plan.tasks) > len(small_plan.tasks)
    assert any(task.parallelizable for task in large_plan.tasks)
    assert all(task.milestone_key for task in large_plan.tasks)
    one_worker_plan = large_architecture.project_plan(large_project.id, large_prompt)
    assert [task.id for task in one_worker_plan.tasks] == [task.id for task in large_plan.tasks]
    small_db.close()
    large_db.close()


class RecordingEvents:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def publish(self, _event_type: str, _message: str, **kwargs: Any) -> None:
        self.items.append(kwargs)


def test_governance_failure_does_not_regenerate_successful_model_output() -> None:
    orchestrator = object.__new__(Orchestrator)
    calls = 0
    record = SimpleNamespace(
        id="project:run:foundation",
        retry_count=0,
        max_retries=3,
        result={"state": "WORKER_RESULT_RECEIVED"},
        logs=[],
        job_id=None,
        worker_id="worker-one",
    )

    async def fail_after_generation(*_args: Any, **_kwargs: Any) -> None:
        nonlocal calls
        calls += 1
        raise ClassifiedFailure(
            FailureCode.CONTRACT_PROPOSAL_REQUIRED,
            "Core-owned contract path cannot be mutated by a worker",
            {
                "failure_stage": "CONTRACT_VALIDATION",
                "retry_strategy": "REVIEW_CONTRACT_PROPOSAL",
                "worker_generation": "SUCCEEDED",
            },
        )

    orchestrator.projects = SimpleNamespace(
        get=lambda _project_id: SimpleNamespace(options={"retry_failed_tasks": True})
    )
    orchestrator._task_record = lambda _project_id, _key: record
    orchestrator._run_generation_task = fail_after_generation
    orchestrator._set_task = lambda *_args, **_kwargs: None
    orchestrator.events = RecordingEvents()
    orchestrator.architecture = SimpleNamespace(record_repair=lambda *_args: None)
    task = PlanTask(
        id="foundation",
        title="Foundation",
        description="Use canonical contracts",
        dependencies=["plan"],
        suggested_agent="coder",
    )

    with pytest.raises(ClassifiedFailure) as failure:
        asyncio.run(orchestrator._run_generation_with_retries("project", task, "prompt"))

    assert failure.value.code == FailureCode.CONTRACT_PROPOSAL_REQUIRED
    assert calls == 1
