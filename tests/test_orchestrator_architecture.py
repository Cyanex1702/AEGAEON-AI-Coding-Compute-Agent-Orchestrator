from __future__ import annotations

import pytest
from sqlalchemy import select

from aegaeon.agents.deterministic import BrokeBoyPlanner
from aegaeon.database.models import ContractRevisionRecord, TaskRecord
from aegaeon.database.session import Database
from aegaeon.models.results import ChangeOperation, ContractProposal
from aegaeon.orchestration import OrchestrationArchitectureService
from aegaeon.projects.manager import ProjectManager
from aegaeon.protocol.schemas import ProjectCreate


def test_small_and_large_projects_receive_scaled_architecture(client, project_payload) -> None:
    small = client.post("/projects", json=project_payload)
    assert small.status_code == 201
    small_state = client.get(f"/projects/{small.json()['id']}/orchestration").json()
    assert small_state["analysis"]["complexity"] == "LOW"
    assert len(small_state["milestones"]) == 2
    assert small_state["contract"]["version"] == 1
    assert small_state["compute_plan"]["recommended_worker_count"] == 1

    large_prompt = """Build an advanced scheduler with:
calendar
recurring events
alarms
persistence
authentication
responsive mobile UI
themes
notifications
security roles
analytics dashboard
cloud deployment
"""
    large = client.post(
        "/projects",
        json={**project_payload, "name": "Advanced scheduler", "prompt": large_prompt},
    )
    assert large.status_code == 201
    large_state = client.get(f"/projects/{large.json()['id']}/orchestration").json()
    assert large_state["analysis"]["complexity"] == "HIGH"
    assert len(large_state["milestones"]) == 6
    assert len(large_state["requirements"]) >= 6
    assert large_state["compute_plan"]["recommended_worker_count"] >= 2


def test_user_can_edit_pending_milestone_and_compute_plan(client, project_payload) -> None:
    project = client.post("/projects", json=project_payload).json()
    state = client.get(f"/projects/{project['id']}/orchestration").json()
    milestone = state["milestones"][0]

    edited = client.patch(
        f"/projects/{project['id']}/milestones/{milestone['id']}",
        json={"title": "Foundation checkpoint"},
    )
    assert edited.status_code == 200
    assert edited.json()["milestones"][0]["title"] == "Foundation checkpoint"

    compute = client.put(
        f"/projects/{project['id']}/compute-plan",
        json={"strategy": "fast", "worker_count": 4},
    )
    assert compute.status_code == 200
    assert compute.json()["compute_plan"]["strategy"] == "fast"
    assert compute.json()["compute_plan"]["selected_worker_count"] == 4


def test_contract_revision_stale_rejection_and_milestone_gates(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'architecture.db'}")
    database.create_all()
    projects = ProjectManager(database, tmp_path / "projects")
    request = ProjectCreate(
        prompt="Build a calculator with add, subtract, multiply, divide, and tests."
    )
    project = projects.create(request)
    plan = BrokeBoyPlanner().plan(request.prompt, 1)
    architecture = OrchestrationArchitectureService(database)
    architecture.prepare(
        project.id,
        project.prompt,
        plan,
        project.options,
        projects.current_revision(project.id),
    )

    context = architecture.task_contract_context(project.id, plan.tasks[1])
    task = TaskRecord(
        id="task-contract-test",
        project_id=project.id,
        key=plan.tasks[1].id,
        title=plan.tasks[1].title,
        description=plan.tasks[1].description,
        milestone_id=context["milestone_id"],
        contract_version=1,
        allowed_files=["**/*"],
    )
    additive = ContractProposal(
        type="add_field",
        target="Calculator.precision",
        change={"type": "integer"},
        reason="Precision is required by the accepted calculator behavior",
    )
    outcomes = architecture.review_proposals(project.id, task.id, "worker-one", [additive])
    assert outcomes[0]["approved"] is True
    assert architecture.contract(project.id).version == 2

    breaking = ContractProposal(
        type="rename",
        target="Calculator.id",
        change={"to": "calculator_id"},
        reason="Worker preference",
    )
    outcomes = architecture.review_proposals(project.id, task.id, "worker-two", [breaking])
    assert outcomes[0]["approved"] is False
    assert architecture.contract(project.id).version == 2
    with database.session() as session:
        assert len(session.scalars(select(ContractRevisionRecord)).all()) == 2

    with pytest.raises(RuntimeError, match="stale contract result"):
        architecture.validate_worker_result(
            task,
            [ChangeOperation(operation="update", path="calculator.py", content="")],
            [],
        )

    state = architecture.summary(project.id)
    with database.session() as session:
        for index, milestone in enumerate(state["milestones"]):
            session.add(
                TaskRecord(
                    id=f"gate-task-{index}",
                    project_id=project.id,
                    key=f"gate-{index}",
                    title=f"Gate {index}",
                    description="Acceptance gate fixture",
                    milestone_id=milestone["id"],
                    status="COMPLETED",
                    contract_version=2,
                )
            )
    commit = projects.current_revision(project.id)
    completed = architecture.gate_ready_milestones(project.id, commit)
    assert len(completed) == len(state["milestones"])
    acceptance = architecture.final_acceptance(project.id, commit)
    assert acceptance["passed"] is True
    assert acceptance["contract_version"] == 2
    database.close()
