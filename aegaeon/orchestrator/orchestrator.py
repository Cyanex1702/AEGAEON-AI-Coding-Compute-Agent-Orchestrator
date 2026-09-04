from __future__ import annotations

import asyncio
import json
import random
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from aegaeon.agents.demo import DemoAgentRuntime
from aegaeon.agents.deterministic import BrokeBoyPlanner
from aegaeon.agents.llm import LLMAgentRuntime
from aegaeon.artifacts.manager import ArtifactManager, ArtifactStorageError
from aegaeon.compute import ComputeIntelligence
from aegaeon.compute.schemas import ContextSection
from aegaeon.config import Settings
from aegaeon.database.models import ProjectRecord, RunRecord, TaskRecord, WorkerResultRecord
from aegaeon.database.session import Database
from aegaeon.events import EventBus
from aegaeon.execution.runner import SubprocessRunner
from aegaeon.execution.sandbox import DisposableVerificationSandbox
from aegaeon.execution.tests import TestCommandDetector
from aegaeon.failures import (
    NON_RETRYABLE_FAILURES,
    ClassifiedFailure,
    FailureCode,
    normalize_failure_code,
    retry_strategy_for_failure,
)
from aegaeon.jobs.leases import JobLeaseManager
from aegaeon.models.results import ChangeOperation, ContractProposal
from aegaeon.models.scout import ModelScout
from aegaeon.orchestration import OrchestrationArchitectureService
from aegaeon.portable import PortableFilenameError
from aegaeon.projects.context import RepositoryContext
from aegaeon.projects.integration import IntegrationConflict
from aegaeon.projects.manager import ProjectManager
from aegaeon.protocol.schemas import (
    ArtifactPayload,
    JobAssign,
    JobRequirements,
    JobResult,
    ModelGeneratePayload,
    ModelGenerationConstraints,
    ModelGenerationContext,
    PlanTask,
    ProjectPlan,
    ProjectStatus,
    StrategyName,
    TaskState,
)
from aegaeon.strategy.router import StrategyRouter
from aegaeon.tasks.dag import TaskDAG
from aegaeon.workers.registry import WorkerDisconnectedError, WorkerRegistry
from aegaeon.workers.scheduler import ScheduleDecision, WorkerCapacityStatus


class Orchestrator:
    """Central owner of planning, task state, worker jobs, integration, and verification."""

    def __init__(
        self,
        settings: Settings,
        database: Database,
        projects: ProjectManager,
        artifacts: ArtifactManager,
        workers: WorkerRegistry,
        events: EventBus,
        strategies: StrategyRouter,
        runner: SubprocessRunner,
        demo_runtime: DemoAgentRuntime,
        leases: JobLeaseManager,
        architecture: OrchestrationArchitectureService,
        model_runtime: LLMAgentRuntime | None = None,
        test_detector: TestCommandDetector | None = None,
        compute: ComputeIntelligence | None = None,
        scout: ModelScout | None = None,
    ) -> None:
        self.settings = settings
        self.database = database
        self.projects = projects
        self.artifacts = artifacts
        self.workers = workers
        self.events = events
        self.strategies = strategies
        self.runner = runner
        self.demo_runtime = demo_runtime
        self.broke_planner = BrokeBoyPlanner()
        self.leases = leases
        self.architecture = architecture
        self.model_runtime = model_runtime
        self.test_detector = test_detector or TestCommandDetector()
        self.compute = compute or ComputeIntelligence(database)
        self.scout = scout or ModelScout(database)
        self.sandbox = DisposableVerificationSandbox(settings.projects_dir)
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()
        self._integration_locks: dict[str, asyncio.Lock] = {}
        self._integration_lock_users: dict[str, int] = {}

    def _track_run(self, project_id: str, task: asyncio.Task[None]) -> None:
        self._runs[project_id] = task

        def discard_finished(finished: asyncio.Task[None]) -> None:
            if self._runs.get(project_id) is finished:
                self._runs.pop(project_id, None)

        task.add_done_callback(discard_finished)

    def start(self, project_id: str) -> str:
        project = self.projects.get(project_id)
        active = {
            ProjectStatus.RUNNING,
            ProjectStatus.PLANNING,
            ProjectStatus.TESTING,
            ProjectStatus.REVIEWING,
        }
        if project.status in active:
            raise RuntimeError("project is already running")
        current = self._runs.get(project_id)
        if current and not current.done():
            raise RuntimeError("project is already running")
        execution_mode = str(project.options.get("execution_mode", "rich_boy"))
        if (
            execution_mode == "rich_boy"
            and not self.settings.demo_mode
            and self.model_runtime is None
        ):
            raise RuntimeError("Rich Boy Mode needs a connected model provider")
        if execution_mode == "broke_boy" and not any(
            "model.generate" in worker.capabilities and worker.status in {"online", "idle", "ready"}
            for worker in self.workers.list()
        ):
            raise RuntimeError(
                "Broke Boy Mode is waiting for a model worker. Generate a notebook, "
                "open it in Colab, and run all cells first."
            )
        run_id = f"run-{uuid4().hex[:12]}"
        with self.database.session() as session:
            record = session.get(ProjectRecord, project_id)
            if not record:
                raise KeyError(project_id)
            record.active_run_id = run_id
            record.progress = 0
            session.add(
                RunRecord(
                    id=run_id,
                    project_id=project_id,
                    status="queued",
                    recovered=False,
                )
            )
        self._cancelled.discard(project_id)
        self._track_run(
            project_id,
            asyncio.create_task(
                self._run(project_id, recovered=False),
                name=f"project-run:{project_id}:{run_id}",
            ),
        )
        return run_id

    def recover(self, project_ids: set[str]) -> None:
        for project_id in project_ids:
            current = self._runs.get(project_id)
            if current and not current.done():
                continue
            plan_path = self.projects.projects_dir / project_id / "AEGAEON" / "plan.json"
            if not plan_path.is_file():
                self._set_project(project_id, status=ProjectStatus.FAILED.value)
                continue
            self._cancelled.discard(project_id)
            self._track_run(
                project_id,
                asyncio.create_task(
                    self._run(project_id, recovered=True),
                    name=f"project-recovery:{project_id}",
                ),
            )

    async def cancel(self, project_id: str) -> None:
        self.projects.get(project_id)
        self._cancelled.add(project_id)
        task = self._runs.get(project_id)
        if task and not task.done():
            task.cancel()
        await self.workers.cancel_project(project_id)
        self.leases.cancel_project(project_id)
        self._set_project(project_id, status=ProjectStatus.CANCELLED.value)
        self._set_active_run(project_id, "cancelled")
        self._cancel_open_tasks(project_id)
        await self.events.publish(
            "project.cancelled", "Project run cancelled", project_id=project_id
        )

    async def shutdown(self) -> None:
        tasks = [task for task in self._runs.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, project_id: str, recovered: bool) -> None:
        project = self.projects.get(project_id)
        try:
            self._set_active_run(project_id, "running", started_at=datetime.now(UTC))
            self._set_project(
                project_id,
                status=(ProjectStatus.RUNNING.value if recovered else ProjectStatus.PLANNING.value),
                progress=project.progress if recovered else 2,
            )
            await self.events.publish(
                "project.recovered" if recovered else "project.started",
                (
                    "Interrupted orchestration recovered from durable task and job state"
                    if recovered
                    else f"Orchestration started in {self.settings.model_mode} mode"
                ),
                project_id=project_id,
                payload={"runtime": self._project_runtime(project_id)},
            )
            if recovered:
                plan_path = self.projects.projects_dir / project_id / "AEGAEON" / "plan.json"
                plan = ProjectPlan.model_validate_json(plan_path.read_text(encoding="utf-8"))
            else:
                decision = self.strategies.choose(
                    StrategyName(project.strategy),
                    [worker.model_dump() for worker in self.workers.list()],
                )
                self._set_project(project_id, strategy=decision.strategy.value)
                await self.events.publish(
                    "strategy.selected",
                    decision.reason,
                    project_id=project_id,
                    payload={"strategy": decision.strategy.value},
                )
                plan = await self._plan(project.prompt, project_id)

            plan = self.architecture.prepare(
                project_id,
                project.prompt,
                plan,
                self.projects.get(project_id).options,
                await asyncio.to_thread(self.projects.current_revision, project_id),
            )
            if not recovered:
                self._create_tasks(project_id, plan)
                self._write_control_files(project_id, plan)
                await self._complete_planner(project_id, plan)
            else:
                self.architecture.attach_existing_tasks(
                    project_id,
                    plan,
                    await asyncio.to_thread(self.projects.current_revision, project_id),
                )

            self._set_project(project_id, status=ProjectStatus.RUNNING.value)
            await self._execute_plan(project_id, project.prompt, plan)
            final_commit = await asyncio.to_thread(self.projects.current_revision, project_id)
            acceptance = self.architecture.final_acceptance(project_id, final_commit)
            if not acceptance["passed"]:
                raise RuntimeError(
                    "final acceptance rejected incomplete milestone or requirement coverage"
                )

            self._set_project(
                project_id,
                status=ProjectStatus.COMPLETED.value,
                progress=100,
                summary=plan.project_summary,
            )
            self._set_active_run(
                project_id,
                "completed",
                completed_at=datetime.now(UTC),
                summary=plan.project_summary,
            )
            await self.events.publish(
                "project.completed",
                "Project completed with passing verification and review",
                project_id=project_id,
                payload={
                    "workspace": str(self.projects.repo_path(project_id)),
                    "runtime": self._project_runtime(project_id),
                    "acceptance": acceptance,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_project(project_id, status=ProjectStatus.FAILED.value)
            self._set_active_run(
                project_id, "failed", completed_at=datetime.now(UTC), summary=str(exc)
            )
            failure_diagnostics = (
                dict(exc.diagnostics) if isinstance(exc, ClassifiedFailure) else {}
            )
            failure_stage = (
                failure_diagnostics.get("failure_stage")
                or failure_diagnostics.get("stage")
                or ("VERIFICATION" if not isinstance(exc, ClassifiedFailure) else "GENERATION")
            )
            await self.events.publish(
                "project.failed",
                str(exc),
                project_id=project_id,
                task_id=failure_diagnostics.get("task_id"),
                job_id=failure_diagnostics.get("job_id"),
                worker_id=failure_diagnostics.get("worker_id"),
                payload={
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "failure_stage": failure_stage,
                    "failure_classification": (
                        exc.code.value if isinstance(exc, ClassifiedFailure) else "UNKNOWN"
                    ),
                    "retry_strategy": (
                        failure_diagnostics.get("retry_strategy")
                        if isinstance(exc, ClassifiedFailure)
                        else "MANUAL_REVIEW"
                    ),
                    "diagnostics": failure_diagnostics,
                },
            )

    async def _execute_plan(self, project_id: str, prompt: str, plan: ProjectPlan) -> None:
        dag = TaskDAG(plan.tasks)
        while True:
            self._ensure_active(project_id)
            states = {task.key: task.status for task in self.projects.tasks(project_id)}
            if states and all(state == TaskState.COMPLETED.value for state in states.values()):
                return
            blocked = dag.blocked(states)
            if blocked:
                for item in blocked:
                    record = self._task_record(project_id, item.id)
                    self._set_task(record.id, status=TaskState.BLOCKED.value)
                raise RuntimeError(
                    "task graph blocked by failed dependencies: "
                    + ", ".join(item.id for item in blocked)
                )
            ready = [item for item in dag.ready(states) if item.id != "plan"]
            incomplete_generation = {
                task.id
                for task in plan.tasks
                if task.id not in {"plan", "verification", "review"}
                and states.get(task.id) != TaskState.COMPLETED.value
            }
            if incomplete_generation:
                ready = [item for item in ready if item.id not in {"verification", "review"}]
            if not ready:
                raise RuntimeError("task graph has no ready work and is not complete")
            for item in ready:
                record = self._task_record(project_id, item.id)
                if record.status == TaskState.PENDING.value:
                    self._set_task(record.id, status=TaskState.READY.value)

            lifecycle = [item for item in ready if item.id in {"verification", "review"}]
            if lifecycle:
                await self._run_plan_task(project_id, lifecycle[0], prompt)
                continue
            parallel = [item for item in ready if item.parallelizable]
            batch = parallel or [ready[0]]
            if len(batch) > 1:
                await self.events.publish(
                    "tasks.parallel",
                    f"Dispatching {len(batch)} independent tasks concurrently",
                    project_id=project_id,
                    payload={"tasks": [item.id for item in batch]},
                )
            results = await asyncio.gather(
                *(self._run_plan_task(project_id, item, prompt) for item in batch),
                return_exceptions=True,
            )
            failures = [result for result in results if isinstance(result, BaseException)]
            if failures:
                raise failures[0]

    async def _run_plan_task(self, project_id: str, plan_task: PlanTask, prompt: str) -> None:
        options = self.projects.get(project_id).options
        if plan_task.id == "verification":
            if not bool(options.get("run_tests", True)):
                await self._skip_optional_task(project_id, plan_task.id, "Test execution disabled")
            elif not await self._run_verification(project_id, prompt):
                raise RuntimeError(
                    "generated project verification did not pass within the retry limit"
                )
        elif plan_task.id == "review":
            if not bool(options.get("review_code", True)):
                await self._skip_optional_task(project_id, plan_task.id, "Code review disabled")
            else:
                await self._complete_review(project_id, prompt)
        else:
            await self._run_generation_with_retries(project_id, plan_task, prompt)
        await self._gate_milestones(project_id)

    async def _gate_milestones(self, project_id: str) -> None:
        commit = await asyncio.to_thread(self.projects.current_revision, project_id)
        milestone_ids = self.architecture.gate_ready_milestones(project_id, commit)
        for milestone_id in milestone_ids:
            await self.events.publish(
                "milestone.completed",
                "Milestone gate passed and canonical checkpoint recorded",
                project_id=project_id,
                payload={"milestone_id": milestone_id, "commit": commit},
            )

    async def _skip_optional_task(self, project_id: str, key: str, reason: str) -> None:
        task = self._task_record(project_id, key)
        self._set_task(
            task.id,
            status=TaskState.COMPLETED.value,
            started_at=datetime.now(UTC),
            completed_at=datetime.now(UTC),
            logs=[reason],
            result={"skipped": True, "reason": reason},
        )
        self._update_progress(project_id)
        await self.events.publish(
            "task.skipped",
            reason,
            project_id=project_id,
            task_id=task.id,
            payload={"option": "run_tests" if key == "verification" else "review_code"},
        )

    async def _run_generation_with_retries(
        self, project_id: str, plan_task: PlanTask, prompt: str
    ) -> None:
        record = self._task_record(project_id, plan_task.id)
        retry_enabled = bool(self.projects.get(project_id).options.get("retry_failed_tasks", True))
        start_attempt = record.retry_count
        failure_evidence: dict[str, Any] = {}
        for attempt in range(start_attempt, record.max_retries + 1):
            try:
                await self._run_generation_task(
                    project_id, plan_task, prompt, attempt, failure_evidence
                )
                return
            except Exception as exc:
                failure_code = (
                    exc.code
                    if isinstance(exc, ClassifiedFailure)
                    else (
                        FailureCode.WORKER_DISCONNECTED
                        if isinstance(exc, WorkerDisconnectedError)
                        else FailureCode.INTEGRATION_CONFLICT
                        if isinstance(exc, IntegrationConflict)
                        else normalize_failure_code(type(exc).__name__, str(exc))
                    )
                )
                current = self._task_record(project_id, plan_task.id)
                diagnostics = dict(exc.diagnostics) if isinstance(exc, ClassifiedFailure) else {}
                stop_retrying = (
                    attempt >= record.max_retries
                    or not retry_enabled
                    or failure_code in NON_RETRYABLE_FAILURES
                )
                retry_strategy = retry_strategy_for_failure(failure_code)
                failure_evidence = {
                    "attempt": attempt + 1,
                    "error_type": diagnostics.get("error_type", type(exc).__name__),
                    "error_message": str(exc).strip() or repr(exc),
                    "failure_code": failure_code.value,
                    "failure_classification": failure_code.value,
                    "failure_stage": diagnostics.get("failure_stage")
                    or diagnostics.get("stage")
                    or ("INTEGRATION" if isinstance(exc, IntegrationConflict) else "GENERATION"),
                    "retry_strategy": retry_strategy,
                    "diagnostics": diagnostics,
                }
                if current.job_id:
                    try:
                        failed_job = self.leases.get(current.job_id)
                    except KeyError:
                        pass
                    else:
                        failure_evidence.update(
                            {
                                "job_id": failed_job.id,
                                "worker_id": failed_job.worker_id,
                                "worker_logs": failed_job.logs[-20:],
                            }
                        )
                oom_codes = {
                    FailureCode.CUDA_OOM,
                    FailureCode.GENERATION_CUDA_OOM,
                    FailureCode.MODEL_LOAD_CUDA_OOM,
                    FailureCode.CONTEXT_TOO_LARGE,
                }
                if failure_code in oom_codes:
                    recent_plans = self.compute.plans(task_id=current.id, limit=1)
                    if recent_plans:
                        _, incident = self.compute.record_failure(
                            recent_plans[0].id,
                            worker_id=failure_evidence.get("worker_id") or current.worker_id,
                            classification=failure_code.value,
                            message=failure_evidence["error_message"],
                            diagnostics=diagnostics,
                            attempt=attempt + 1,
                            final=stop_retrying,
                        )
                        if incident:
                            failure_evidence.update(
                                {
                                    "incident_id": incident.id,
                                    "root_failure_id": incident.root_failure_id,
                                    "incident_state": incident.state.value,
                                    "retry_strategy": (
                                        incident.recovery_action.value
                                        if incident.recovery_action
                                        else retry_strategy
                                    ),
                                }
                            )
                            await self.events.publish(
                                "compute.incident.updated",
                                (
                                    "GPU memory incident is being recovered"
                                    if not stop_retrying
                                    else "GPU memory recovery was exhausted"
                                ),
                                project_id=project_id,
                                task_id=current.id,
                                worker_id=failure_evidence.get("worker_id"),
                                payload=failure_evidence,
                                severity="WARNING" if not stop_retrying else "INFO",
                            )
                if stop_retrying:
                    self._set_task(
                        current.id,
                        status=TaskState.FAILED.value,
                        completed_at=datetime.now(UTC),
                        logs=[*current.logs, str(exc)],
                        result={**dict(current.result), "failure": failure_evidence},
                    )
                    await self.events.publish(
                        "task.failed",
                        f"{plan_task.title} failed at {failure_evidence['failure_stage']}",
                        project_id=project_id,
                        task_id=current.id,
                        worker_id=current.worker_id,
                        payload=failure_evidence,
                    )
                    raise
                next_attempt = attempt + 1
                self.architecture.record_repair(project_id, current, failure_evidence)
                self._set_task(
                    current.id,
                    status=TaskState.RETRYING.value,
                    retry_count=next_attempt,
                    worker_id=None,
                    job_id=None,
                    completed_at=None,
                    logs=[*current.logs, f"Attempt {attempt + 1} interrupted: {exc}"],
                )
                if isinstance(exc, IntegrationConflict):
                    event_type = "integration.conflict"
                elif isinstance(exc, WorkerDisconnectedError):
                    event_type = "job.reassigned"
                else:
                    event_type = "task.requeued"
                await self.events.publish(
                    event_type,
                    f"{plan_task.title} requeued for attempt {next_attempt + 1}",
                    project_id=project_id,
                    task_id=current.id,
                    payload={**failure_evidence, "attempt": next_attempt + 1},
                )
                delay = self._retry_delay(failure_code, next_attempt)
                failure_evidence["retry_delay_seconds"] = round(delay, 2)
                await asyncio.sleep(delay)

    async def _plan(self, prompt: str, project_id: str) -> ProjectPlan:
        if self._is_broke(project_id):
            return self.architecture.project_plan(project_id, prompt)
        if self.settings.demo_mode:
            return self.demo_runtime.plan(prompt)
        runtime = self._require_model_runtime()
        context = RepositoryContext(self.projects.repo_path(project_id)).render(
            prompt, self.settings.llm_context_characters
        )
        return await runtime.plan(prompt, context)

    async def _complete_planner(self, project_id: str, plan: ProjectPlan) -> None:
        task = self._task_record(project_id, "plan")
        self._set_task(task.id, status=TaskState.RUNNING.value, started_at=datetime.now(UTC))
        await self.events.publish(
            "task.started",
            "Planner is producing a validated task DAG",
            project_id=project_id,
            task_id=task.id,
            payload={"runtime": self._project_runtime(project_id)},
        )
        self._set_task(
            task.id,
            status=TaskState.COMPLETED.value,
            completed_at=datetime.now(UTC),
            result={
                "project_summary": plan.project_summary,
                "task_count": len(plan.tasks),
                "runtime": self._project_runtime(project_id),
            },
            logs=[
                "Structured planner output validated with Pydantic.",
                "Task DAG is dependency ordered and acyclic.",
            ],
        )
        self._update_progress(project_id)
        await self.events.publish(
            "task.completed",
            f"Planner created {len(plan.tasks)} tasks",
            project_id=project_id,
            task_id=task.id,
        )

    async def _reserve_model_worker(
        self, project_id: str, task_id: str, title: str, job: JobAssign
    ) -> ScheduleDecision:
        last_status: WorkerCapacityStatus | None = None
        deadline = time.monotonic() + self.settings.job_assignment_timeout_seconds
        while True:
            self._ensure_active(project_id)
            assessment = await self.workers.reserve_or_assess(job)
            decision = assessment.decision
            if decision is not None:
                if last_status == WorkerCapacityStatus.COMPATIBLE_WORKER_BUSY:
                    await self.events.publish(
                        "task.worker_available",
                        f"Capacity is now available for {title.lower()}",
                        project_id=project_id,
                        task_id=task_id,
                        worker_id=decision.worker_id,
                        payload={"capacity_status": assessment.status.value},
                    )
                await self.events.publish(
                    "task.dispatched",
                    f"{title} dispatched to {decision.worker_id}",
                    project_id=project_id,
                    task_id=task_id,
                    worker_id=decision.worker_id,
                    payload={"job_id": job.job_id, "capacity_status": assessment.status.value},
                )
                return decision
            if assessment.status == WorkerCapacityStatus.COMPATIBLE_WORKER_BUSY:
                if time.monotonic() >= deadline:
                    raise ClassifiedFailure(
                        FailureCode.WORKER_TIMEOUT,
                        f"Timed out waiting for compatible capacity for {title}",
                        {"capacity_status": assessment.status.value},
                    )
                self._set_task(
                    task_id,
                    status=TaskState.WAITING_FOR_WORKER.value,
                    worker_id=None,
                    job_id=job.job_id,
                )
                if last_status != assessment.status:
                    await self.events.publish(
                        "task.waiting_for_worker",
                        f"{title} is queued until a compatible worker is free",
                        project_id=project_id,
                        task_id=task_id,
                        payload={
                            "capacity_status": assessment.status.value,
                            "busy_workers": assessment.busy_count,
                            "attempt_consumed": False,
                        },
                    )
                last_status = assessment.status
                await self.workers.wait_for_capacity_change()
                continue
            code = FailureCode.NO_COMPATIBLE_WORKER
            detail = (
                "Compatible workers are registered but offline"
                if assessment.status == WorkerCapacityStatus.WORKER_OFFLINE
                else "No registered worker satisfies this job's model and hardware requirements"
            )
            raise ClassifiedFailure(
                code,
                f"{detail} ({assessment.status.value})",
                {"capacity_status": assessment.status.value},
            )

    @staticmethod
    def _retry_delay(failure_code: FailureCode, attempt: int) -> float:
        if failure_code in {
            FailureCode.NETWORK_INTERRUPTION,
            FailureCode.WORKER_DISCONNECTED,
            FailureCode.WORKER_TIMEOUT,
        }:
            ceiling = min(30.0, 3.0 * 2 ** max(0, attempt - 1))
        elif failure_code in {
            FailureCode.CUDA_OOM,
            FailureCode.GENERATION_CUDA_OOM,
            FailureCode.MODEL_LOAD_CUDA_OOM,
        }:
            ceiling = min(10.0, 1.0 * 2 ** max(0, attempt - 1))
        else:
            ceiling = min(5.0, 0.5 * 2 ** max(0, attempt - 1))
        return random.uniform(max(0.25, ceiling / 2), ceiling)

    async def _run_generation_task(
        self,
        project_id: str,
        plan_task: PlanTask,
        prompt: str,
        attempt: int,
        failure_evidence: dict[str, Any],
    ) -> None:
        task = self._task_record(project_id, plan_task.id)
        started = time.perf_counter()
        controller_id = self._project_runtime(project_id)
        base_revision = await asyncio.to_thread(self.projects.current_revision, project_id)
        contract_context = self.architecture.task_contract_context(project_id, plan_task)
        instructions = self._worker_instructions(task, plan_task, base_revision, contract_context)
        self._set_task(
            task.id,
            status=TaskState.RUNNING.value,
            worker_id=controller_id,
            started_at=datetime.now(UTC),
            milestone_id=contract_context["milestone_id"],
            base_commit=base_revision,
            contract_version=contract_context["contract_version"],
            allowed_files=contract_context["allowed_files"],
            acceptance_criteria=contract_context["acceptance_criteria"],
        )
        self.architecture.start_milestone(contract_context["milestone_id"])
        await self.events.publish(
            "task.started",
            f"{task.agent_role.title()} agent started {task.title.lower()}",
            project_id=project_id,
            task_id=task.id,
            worker_id=controller_id,
            payload={"runtime": self._project_runtime(project_id)},
        )

        try:
            broke_mode = self._is_broke(project_id)
            controller_changes: list[ChangeOperation] = []
            controller_result: dict[str, Any] = {}
            compute_plan = None
            if broke_mode:
                payload, requirements, compute_plan = self._adaptive_compute_job(
                    project_id=project_id,
                    task=task,
                    plan_task=plan_task,
                    prompt=prompt,
                    instructions=instructions,
                    contract_context=contract_context,
                    attempt=attempt,
                    failure_evidence=failure_evidence,
                )
                await self.events.publish(
                    "compute.plan.created",
                    (
                        f"Compute plan selected {compute_plan.model_id} at "
                        f"{compute_plan.estimated_oom_risk.value} OOM risk"
                    ),
                    project_id=project_id,
                    task_id=task.id,
                    worker_id=compute_plan.worker_id,
                    payload={
                        "compute_plan_id": compute_plan.id,
                        "fit_score": compute_plan.fit_score,
                        "risk": compute_plan.estimated_oom_risk.value,
                        "output_tokens": compute_plan.maximum_new_tokens,
                        "prompt_tokens": compute_plan.prompt_token_budget,
                        "device_strategy": compute_plan.device_strategy.value,
                    },
                )
                summary = f"Worker-hosted model generated {plan_task.title.lower()}"
                job_type = "model.generate"
            elif self.settings.demo_mode:
                files = self.demo_runtime.generate(prompt, plan_task.id)
                summary = f"Demo runtime generated {plan_task.title.lower()}"
                job_type = "demo.generate_project"
                payload = {"prompt": prompt, "stage": plan_task.id}
                requirements = JobRequirements(
                    minimum_ram_mb=128,
                    capabilities=plan_task.required_capabilities,
                )
            else:
                runtime = self._require_model_runtime()
                context = RepositoryContext(self.projects.repo_path(project_id)).render(
                    f"{plan_task.title}\n{plan_task.description}",
                    self.settings.llm_context_characters,
                )
                scoped_task = plan_task.model_copy(update={"description": instructions})
                change_set = await runtime.generate(prompt, scoped_task, context)
                files = {item.path: item.content for item in change_set.files}
                controller_changes = change_set.changes
                controller_result = {
                    "summary": change_set.summary,
                    "tests": change_set.test_commands,
                    "notes": change_set.notes,
                    "contract_proposals": [
                        item.model_dump(mode="json") for item in change_set.contract_proposals
                    ],
                    "risks": change_set.risks,
                    "blocked_by": change_set.blocked_by,
                    "acceptance_evidence": change_set.acceptance_evidence,
                }
                summary = change_set.summary
                job_type = "source.materialize"
                payload = {
                    "files": files,
                    "label": plan_task.id,
                    "summary": change_set.summary,
                }
                requirements = JobRequirements(
                    minimum_ram_mb=128,
                    capabilities=plan_task.required_capabilities,
                )
            run_id = self.projects.get(project_id).active_run_id
            job = JobAssign(
                job_id=f"job-{uuid4().hex[:12]}",
                run_id=run_id,
                attempt_id=f"{run_id or 'legacy'}:{plan_task.id}:{attempt + 1}",
                job_type=job_type,
                project_id=project_id,
                task_id=task.id,
                agent_role=task.agent_role,
                requirements=requirements,
                instructions=instructions,
                payload=payload,
                runtime_spec=(
                    compute_plan.model_dump(mode="json", exclude={"context_plan"})
                    if compute_plan is not None
                    else {}
                ),
            )
            self.leases.queue(job, attempt)
            decision = (
                await self._reserve_model_worker(project_id, task.id, task.title, job)
                if broke_mode
                else await self.workers.reserve_worker(job)
            )
            worker_id = (
                decision.worker_id
                if decision
                else f"controller:{self._project_runtime(project_id)}"
            )
            if compute_plan is not None:
                self.compute.bind_job(compute_plan.id, job.job_id, worker_id)
            self._set_task(
                task.id,
                status=TaskState.ASSIGNED.value,
                worker_id=worker_id,
                job_id=job.job_id,
            )
            await self.events.publish(
                "task.assigned",
                (
                    f"Source bundle assigned to {worker_id} ({', '.join(decision.reason)})"
                    if decision
                    else "Source bundle materialized by the controller fallback"
                ),
                project_id=project_id,
                task_id=task.id,
                worker_id=worker_id,
            )
            self._set_task(task.id, status=TaskState.RUNNING.value)
            if broke_mode:
                await self.events.publish(
                    "model.job.assigned",
                    f"Local model job assigned to {worker_id}",
                    project_id=project_id,
                    task_id=task.id,
                    worker_id=worker_id,
                    payload={"job_id": job.job_id, "job_type": job.job_type},
                )
            if decision:
                result = await self.workers.assign(decision.worker_id, job)
            else:
                result = JobResult(
                    type="job.completed",
                    job_id=job.job_id,
                    artifacts=[
                        ArtifactPayload(
                            type="source_bundle",
                            filename=f"{plan_task.id}-files.json",
                            files=files,
                            changes=controller_changes,
                        )
                    ],
                    result={
                        "runtime": self._project_runtime(project_id),
                        "materializer": "controller",
                        **controller_result,
                    },
                )
                self.leases.complete_locally(job.job_id, result.result)
            if result.type == "job.failed":
                diagnostics = (
                    result.diagnostics.model_dump(mode="json") if result.diagnostics else {}
                )
                diagnostics.setdefault("project_id", project_id)
                diagnostics.setdefault("task_id", task.id)
                diagnostics.setdefault("job_id", job.job_id)
                diagnostics.setdefault("worker_id", worker_id)
                diagnostics.setdefault("failure_stage", diagnostics.get("stage") or "GENERATION")
                code = normalize_failure_code(
                    diagnostics.get("classification"), result.error or "worker job failed"
                )
                raise ClassifiedFailure(code, result.error or "worker job failed", diagnostics)
            if broke_mode and compute_plan is not None:
                metrics = dict(result.result.get("telemetry") or {})
                self.compute.record_success(
                    compute_plan.id,
                    worker_id=worker_id,
                    metrics=metrics,
                )
            if broke_mode:
                await self.events.publish(
                    "model.inference.completed",
                    f"{result.result.get('model', 'Local model')} returned structured changes",
                    project_id=project_id,
                    task_id=task.id,
                    worker_id=worker_id,
                    payload={"job_id": job.job_id, **result.result},
                )
            modified = await self._integrate_result(
                project_id, task.id, plan_task.id, task.title, result, base_revision
            )
            self._set_task(
                task.id,
                status=TaskState.COMPLETED.value,
                completed_at=datetime.now(UTC),
                duration_seconds=round(time.perf_counter() - started, 3),
                files_modified=modified,
                logs=[summary, f"Artifact returned by {worker_id}.", "Patch committed to Git."],
                result={
                    **dict(self._task_record(project_id, plan_task.id).result),
                    "worker": worker_id,
                    "files": modified,
                    "runtime": self._project_runtime(project_id),
                    "attempt": attempt + 1,
                    "base_revision": base_revision,
                },
            )
            self._update_progress(project_id)
            await self.events.publish(
                "task.completed",
                f"{task.title} completed; {len(modified)} files integrated",
                project_id=project_id,
                task_id=task.id,
                worker_id=worker_id,
                payload={"files": modified, "runtime": self._project_runtime(project_id)},
            )

        except Exception:
            raise

    async def _integrate_result(
        self,
        project_id: str,
        task_id: str,
        task_key: str,
        title: str,
        result: JobResult,
        base_revision: str,
    ) -> list[str]:
        all_changes: list[ChangeOperation] = []
        stored_artifacts: list[dict[str, Any]] = []
        for artifact in result.artifacts:
            content: str | dict[str, Any] = (
                {
                    "files": artifact.files,
                    "changes": [item.model_dump() for item in artifact.changes],
                }
                if artifact.files or artifact.changes
                else (artifact.content or "")
            )
            record = await self._store_artifact_with_retry(
                project_id, task_id, artifact.type, artifact.filename, content
            )
            stored_artifacts.append(
                {"id": record.id, "filename": record.filename, "type": record.type}
            )
            await self.events.publish(
                "artifact.created",
                f"Stored {record.filename}",
                project_id=project_id,
                task_id=task_id,
                payload={"artifact_id": record.id, "type": record.type},
            )
            if artifact.type == "source_bundle":
                all_changes.extend(
                    ChangeOperation(operation="update", path=path, content=value)
                    for path, value in artifact.files.items()
                )
                all_changes.extend(artifact.changes)
        transported_changes = [
            ChangeOperation.model_validate(item) for item in result.result.get("changes", [])
        ]
        existing_targets = {(item.operation, item.path) for item in all_changes}
        all_changes.extend(
            item
            for item in transported_changes
            if (item.operation, item.path) not in existing_targets
        )
        try:
            proposals = [
                ContractProposal.model_validate(item)
                for item in result.result.get("contract_proposals", [])
            ]
        except ValidationError as exc:
            raise ClassifiedFailure(
                FailureCode.CONTRACT_PROPOSAL_INVALID,
                f"worker returned an invalid contract proposal: {exc}",
                {
                    "failure_stage": "CONTRACT_REVIEW",
                    "failure_classification": FailureCode.CONTRACT_PROPOSAL_INVALID.value,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "retry_strategy": "REQUEST_CONTRACT_PROPOSAL_CORRECTION",
                },
            ) from exc
        if not all_changes and not proposals:
            raise ClassifiedFailure(
                FailureCode.CONTRACT_VIOLATION,
                "worker returned neither source changes nor contract proposals",
                {
                    "failure_stage": "WORKER_OUTPUT_VALIDATION",
                    "failure_classification": FailureCode.CONTRACT_VIOLATION.value,
                    "retry_strategy": "REPAIR_AGAINST_CANONICAL_CONTRACT",
                },
            )
        task = self._task_record(project_id, task_key)
        worker_result_id = f"worker-result-{result.job_id}"
        durable_result = {
            "state": "WORKER_RESULT_RECEIVED",
            "job_id": result.job_id,
            "worker_id": task.worker_id,
            "contract_version": task.contract_version,
            "worker_result": result.result,
            "artifacts": stored_artifacts,
            "change_count": len(all_changes),
            "proposal_count": len(proposals),
        }
        with self.database.session() as session:
            existing = session.get(WorkerResultRecord, worker_result_id)
            if existing is None:
                session.add(
                    WorkerResultRecord(
                        id=worker_result_id,
                        project_id=project_id,
                        task_id=task_id,
                        job_id=result.job_id,
                        worker_id=task.worker_id,
                        contract_version=task.contract_version,
                        result=result.result,
                        artifacts=stored_artifacts,
                    )
                )
            task_record = session.get(TaskRecord, task_id)
            if task_record is not None:
                task_record.result = durable_result
        await self.events.publish(
            "worker.result.persisted",
            "Valid worker output persisted before contract governance",
            project_id=project_id,
            task_id=task_id,
            worker_id=task.worker_id,
            payload={
                "worker_result_id": worker_result_id,
                "stage": "WORKER_RESULT_RECEIVED",
                "change_count": len(all_changes),
                "proposal_count": len(proposals),
            },
        )

        proposal_outcomes = self.architecture.review_proposals(
            project_id, task_id, task.worker_id, proposals
        )
        for outcome in proposal_outcomes:
            await self.events.publish(
                "contract.proposal.reviewed",
                outcome["reason"],
                project_id=project_id,
                task_id=task_id,
                worker_id=task.worker_id,
                payload=outcome,
            )
        task = self._task_record(project_id, task_key)
        self.architecture.validate_worker_result(task, all_changes, proposals)
        if not all_changes:
            with self.database.session() as session:
                persisted = session.get(WorkerResultRecord, worker_result_id)
                if persisted is not None:
                    persisted.state = "CONTRACT_PROPOSAL_APPLIED"
                    persisted.integrated_at = datetime.now(UTC)
            return []
        lock = self._integration_locks.setdefault(project_id, asyncio.Lock())
        self._integration_lock_users[project_id] = (
            self._integration_lock_users.get(project_id, 0) + 1
        )
        try:
            async with lock:
                modified, patch = await asyncio.to_thread(
                    self.projects.integrate_changes,
                    project_id,
                    task_key,
                    title,
                    all_changes,
                    base_revision,
                )
        finally:
            remaining = self._integration_lock_users.get(project_id, 1) - 1
            if remaining <= 0:
                self._integration_lock_users.pop(project_id, None)
                if self._integration_locks.get(project_id) is lock:
                    self._integration_locks.pop(project_id, None)
            else:
                self._integration_lock_users[project_id] = remaining
        patch_record = await self._store_artifact_with_retry(
            project_id, task_id, "git_patch", f"{task_key}.patch", patch
        )
        await self.events.publish(
            "artifact.created",
            f"Created canonical Git patch for {title.lower()}",
            project_id=project_id,
            task_id=task_id,
            payload={"artifact_id": patch_record.id, "type": "git_patch"},
        )
        with self.database.session() as session:
            persisted = session.get(WorkerResultRecord, worker_result_id)
            if persisted is not None:
                persisted.state = "INTEGRATED"
                persisted.integrated_at = datetime.now(UTC)
            task_record = session.get(TaskRecord, task_id)
            if task_record is not None:
                task_record.result = {
                    **durable_result,
                    "state": "INTEGRATED",
                    "proposal_outcomes": proposal_outcomes,
                    "files": modified,
                }
        return modified

    async def _store_artifact_with_retry(
        self,
        project_id: str,
        task_id: str,
        artifact_type: str,
        filename: str,
        content: str | bytes | dict[str, Any],
    ) -> Any:
        """Retry controller persistence with the same worker result; never regenerate it."""
        for storage_attempt in range(1, 4):
            try:
                return self.artifacts.create(project_id, task_id, artifact_type, filename, content)
            except PortableFilenameError as exc:
                diagnostics = {
                    "failure_stage": "ARTIFACT_STORAGE",
                    "failure_classification": FailureCode.INVALID_ARTIFACT_FILENAME.value,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "retry_strategy": "DO_NOT_REGENERATE",
                    "filename": filename,
                }
                await self.events.publish(
                    "artifact.storage.failed",
                    "Artifact rejected before filesystem persistence",
                    project_id=project_id,
                    task_id=task_id,
                    payload=diagnostics,
                )
                raise ClassifiedFailure(
                    FailureCode.INVALID_ARTIFACT_FILENAME, str(exc), diagnostics
                ) from exc
            except ArtifactStorageError as exc:
                diagnostics = {
                    "failure_stage": "ARTIFACT_STORAGE",
                    "failure_classification": FailureCode.ARTIFACT_STORAGE_FAILURE.value,
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                    "retry_strategy": "RETRY_CONTROLLER_STORAGE_ONLY",
                    "storage_attempt": storage_attempt,
                    "filename": filename,
                }
                if storage_attempt < 3:
                    await self.events.publish(
                        "artifact.storage.retrying",
                        f"Retrying artifact persistence ({storage_attempt + 1}/3)",
                        project_id=project_id,
                        task_id=task_id,
                        payload=diagnostics,
                    )
                    await asyncio.sleep(0)
                    continue
                await self.events.publish(
                    "artifact.storage.failed",
                    "Artifact persistence failed after deterministic retries",
                    project_id=project_id,
                    task_id=task_id,
                    payload=diagnostics,
                )
                raise ClassifiedFailure(
                    FailureCode.ARTIFACT_STORAGE_FAILURE, str(exc), diagnostics
                ) from exc
        raise AssertionError("artifact storage retry loop exhausted unexpectedly")

    async def _run_verification(self, project_id: str, prompt: str) -> bool:
        task = self._task_record(project_id, "verification")
        maximum = task.max_retries
        repo = self.projects.repo_path(project_id)
        self._set_project(project_id, status=ProjectStatus.TESTING.value)
        for attempt in range(maximum + 1):
            self._ensure_active(project_id)
            state = TaskState.RUNNING if attempt == 0 else TaskState.RETRYING
            self._set_task(
                task.id,
                status=state.value,
                retry_count=attempt,
                started_at=datetime.now(UTC),
            )
            notes: list[str] = []
            executions: list[dict[str, Any]] = []
            with self.sandbox.session(repo) as sandbox:
                if (
                    not sandbox.policy.enforced
                    and not self.settings.demo_mode
                    and not self.settings.allow_unisolated_verification
                ):
                    raise ClassifiedFailure(
                        FailureCode.TEST_FAILURE,
                        "Container verification is required in model mode, but the "
                        "aegaeon-runner image is unavailable. Build apps/runner/Dockerfile "
                        "or explicitly enable AEGAEON_ALLOW_UNISOLATED_VERIFICATION only "
                        "for a disposable development machine.",
                        {
                            "failure_stage": "VERIFICATION_SANDBOX",
                            "failure_classification": FailureCode.TEST_FAILURE.value,
                            "sandbox_backend": sandbox.backend,
                        },
                    )
                if sandbox.warning:
                    notes.append(sandbox.warning)
                dependencies = (
                    []
                    if self.settings.demo_mode and not self._is_broke(project_id)
                    else self.test_detector.dependency_commands(
                        sandbox.workspace, container=sandbox.policy.enforced
                    )
                )
                if not dependencies and self.settings.demo_mode:
                    notes.append(
                        "Deterministic demo verification reuses the controller test environment; "
                        "model-generated projects use clean dependency bootstrap."
                    )
                checks = self.test_detector.checks(
                    sandbox.workspace, container=sandbox.policy.enforced
                )
                plan = [*dependencies, *checks]
                commands = [item.command for item in plan]
                await self.events.publish(
                    "tests.started",
                    f"Running {len(plan)} isolated command(s), attempt {attempt + 1}",
                    project_id=project_id,
                    task_id=task.id,
                    payload={
                        "attempt": attempt + 1,
                        "sandbox_backend": sandbox.backend,
                        "sandbox_policy": {
                            "network_for_verification": sandbox.policy.network_for_verification,
                            "secrets_available": sandbox.policy.secrets_available,
                            "memory_limit_mb": sandbox.policy.memory_limit_mb,
                            "cpu_limit": sandbox.policy.cpu_limit,
                            "process_limit": sandbox.policy.process_limit,
                            "enforced": sandbox.policy.enforced,
                        },
                        "commands": commands,
                    },
                )
                for item in dependencies:
                    execution = await self.runner.run_isolated(item.command, sandbox, network=True)
                    value = execution.model_dump(mode="json")
                    value.update(
                        {
                            "name": item.name,
                            "category": item.category,
                            "status": "passed" if execution.exit_code == 0 else "failed",
                            "failure_code": (
                                None
                                if execution.exit_code == 0
                                else FailureCode.DEPENDENCY_FAILURE.value
                            ),
                        }
                    )
                    executions.append(value)
                    if execution.exit_code != 0:
                        break
                dependencies_passed = all(item["exit_code"] == 0 for item in executions)
                if dependencies_passed:
                    checks = self.test_detector.checks(
                        sandbox.workspace, container=sandbox.policy.enforced
                    )
                    for item in checks:
                        execution = await self.runner.run_isolated(
                            item.command, sandbox, network=False
                        )
                        code = {
                            "test": FailureCode.TEST_FAILURE,
                            "typecheck": FailureCode.TYPECHECK_FAILURE,
                            "build": FailureCode.BUILD_FAILURE,
                            "lint": FailureCode.TEST_FAILURE,
                        }[item.category]
                        value = execution.model_dump(mode="json")
                        value.update(
                            {
                                "name": item.name,
                                "category": item.category,
                                "status": "passed" if execution.exit_code == 0 else "failed",
                                "failure_code": None if execution.exit_code == 0 else code.value,
                            }
                        )
                        executions.append(value)
                        if execution.exit_code != 0:
                            break
                check_count = sum(item["category"] != "dependency" for item in executions)
                passed = (
                    check_count > 0
                    and bool(executions)
                    and all(item["exit_code"] == 0 for item in executions)
                )
                report: dict[str, Any] = {
                    "attempt": attempt + 1,
                    "exit_code": (
                        0 if passed else int(executions[-1]["exit_code"]) if executions else -1
                    ),
                    "passed": passed,
                    "sandbox": {
                        "backend": sandbox.backend,
                        "warning": sandbox.warning,
                        "disposable": True,
                        "network_for_verification": sandbox.policy.network_for_verification,
                        "secrets_available": sandbox.policy.secrets_available,
                        "enforced": sandbox.policy.enforced,
                    },
                    "checks": executions,
                    "commands": executions,
                    "notes": notes,
                }
            artifact = self.artifacts.create(
                project_id,
                task.id,
                "test_report",
                f"verification-attempt-{attempt + 1}.json",
                json.dumps(report, indent=2),
            )
            if passed:
                duration = sum(float(item["duration_seconds"]) for item in executions)
                logs = [str(item["stdout"]).strip() for item in executions if item["stdout"]]
                self._set_task(
                    task.id,
                    status=TaskState.COMPLETED.value,
                    completed_at=datetime.now(UTC),
                    duration_seconds=round(duration, 3),
                    logs=[*notes, *(logs or ["Verification passed."])],
                    result={**report, "artifact_id": artifact.id},
                )
                self._update_progress(project_id)
                await self.events.publish(
                    "tests.completed",
                    "All detected verification commands passed",
                    project_id=project_id,
                    task_id=task.id,
                    payload={"attempt": attempt + 1, "commands": len(commands)},
                )
                return True

            self._set_project(project_id, status=ProjectStatus.REVIEWING.value)
            message = (
                "No repository-native test or build command was detected"
                if not commands
                else "Verification failed; reviewer is analyzing concrete output"
            )
            await self.events.publish(
                "tests.failed",
                message,
                project_id=project_id,
                task_id=task.id,
                payload=report,
            )
            if attempt >= maximum:
                self._set_task(
                    task.id,
                    status=TaskState.FAILED.value,
                    completed_at=datetime.now(UTC),
                    logs=[message, *notes],
                    result=report,
                )
                return False
            if self.settings.demo_mode and not self._is_broke(project_id):
                await self._apply_demo_fix(project_id, task.id, prompt, attempt + 1)
            elif self._is_broke(project_id):
                await self._apply_worker_fix(project_id, task.id, prompt, report, attempt + 1)
            else:
                await self._apply_model_fix(project_id, task.id, prompt, report, attempt + 1)
            self._set_project(project_id, status=ProjectStatus.TESTING.value)
        return False

    async def _apply_demo_fix(
        self, project_id: str, task_id: str, prompt: str, retry_count: int
    ) -> None:
        await self.events.publish(
            "review.completed",
            "Reviewer produced a focused repair request for the coder",
            project_id=project_id,
            task_id=task_id,
        )
        files = self.demo_runtime.generate(prompt, "fix")
        modified, patch = self.projects.integrate_files(
            project_id, f"fix-{retry_count}", "repair failing generated tests", files
        )
        self.artifacts.create(project_id, task_id, "git_patch", f"fix-{retry_count}.patch", patch)
        await self.events.publish(
            "task.retrying",
            f"Coder applied repair attempt {retry_count}",
            project_id=project_id,
            task_id=task_id,
            payload={"files": modified, "runtime": self._project_runtime(project_id)},
        )

    async def _apply_worker_fix(
        self,
        project_id: str,
        task_id: str,
        prompt: str,
        report: dict[str, Any],
        retry_count: int,
    ) -> None:
        base_revision = await asyncio.to_thread(self.projects.current_revision, project_id)
        context = RepositoryContext(self.projects.repo_path(project_id)).render(
            json.dumps(report), self.settings.llm_context_characters
        )
        payload = ModelGeneratePayload(
            role="repair",
            instructions=(
                "Repair the repository using only the concrete verification evidence. "
                "Return complete corrected files."
            ),
            context=ModelGenerationContext(
                project_summary=prompt,
                repository=context,
                acceptance_criteria=["All detected verification commands must pass"],
            ),
            failure_evidence=report,
        )
        run_id = self.projects.get(project_id).active_run_id
        job = JobAssign(
            job_id=f"job-{uuid4().hex[:12]}",
            run_id=run_id,
            attempt_id=f"{run_id or 'legacy'}:repair:{retry_count}",
            job_type="model.repair",
            project_id=project_id,
            task_id=task_id,
            agent_role="repair",
            requirements=self._worker_model_requirements(project_id, "repair"),
            instructions=payload.instructions,
            payload=payload.model_dump(mode="json"),
        )
        self.leases.queue(job, retry_count)
        decision = await self._reserve_model_worker(project_id, task_id, "Verification repair", job)
        await self.events.publish(
            "repair.assigned",
            f"Repair attempt {retry_count} assigned to {decision.worker_id}",
            project_id=project_id,
            task_id=task_id,
            worker_id=decision.worker_id,
            payload={"job_id": job.job_id},
        )
        result = await self.workers.assign(decision.worker_id, job)
        if result.type == "job.failed":
            raise RuntimeError(result.error or "worker repair failed")
        modified = await self._integrate_result(
            project_id,
            task_id,
            f"repair-{retry_count}",
            "repair verification failure",
            result,
            base_revision,
        )
        await self.events.publish(
            "task.retrying",
            f"Local model applied repair attempt {retry_count}",
            project_id=project_id,
            task_id=task_id,
            worker_id=decision.worker_id,
            payload={"files": modified, "model": result.result.get("model")},
        )

    async def _apply_model_fix(
        self,
        project_id: str,
        task_id: str,
        prompt: str,
        report: dict[str, Any],
        retry_count: int,
    ) -> None:
        runtime = self._require_model_runtime()
        repo = self.projects.repo_path(project_id)
        context_builder = RepositoryContext(repo)
        context = context_builder.render(json.dumps(report), self.settings.llm_context_characters)
        review = await runtime.review_failure(prompt, report, context)
        review_record = self.artifacts.create(
            project_id,
            task_id,
            "review_report",
            f"review-attempt-{retry_count}.json",
            review.model_dump_json(indent=2),
        )
        await self.events.publish(
            "review.completed",
            review.summary,
            project_id=project_id,
            task_id=task_id,
            payload={
                "artifact_id": review_record.id,
                "severity": review.severity,
                "affected_files": review.affected_files,
            },
        )
        repair_context = context_builder.render(
            "\n".join([review.root_cause, *review.recommended_changes]),
            self.settings.llm_context_characters,
        )
        change_set = await runtime.repair(prompt, review, repair_context)
        files = change_set.as_file_map()
        modified, patch = self.projects.integrate_files(
            project_id,
            f"repair-{retry_count}",
            "repair verification failure",
            files,
        )
        self.artifacts.create(
            project_id, task_id, "git_patch", f"repair-{retry_count}.patch", patch
        )
        await self.events.publish(
            "task.retrying",
            f"Model coder applied repair attempt {retry_count}",
            project_id=project_id,
            task_id=task_id,
            payload={"files": modified, "runtime": self._project_runtime(project_id)},
        )

    async def _complete_review(self, project_id: str, prompt: str) -> None:
        task = self._task_record(project_id, "review")
        self._set_project(project_id, status=ProjectStatus.REVIEWING.value)
        self._set_task(task.id, status=TaskState.RUNNING.value, started_at=datetime.now(UTC))
        await self.events.publish(
            "task.started",
            "Reviewer is checking diffs, artifacts, and verification evidence",
            project_id=project_id,
            task_id=task.id,
            payload={"runtime": self._project_runtime(project_id)},
        )
        if self.settings.demo_mode or self._is_broke(project_id):
            file_count = len(self.projects.file_tree(project_id))
            result = {
                "approved": True,
                "severity": "none",
                "summary": (
                    "Objective verification passed; no model self-approval used."
                    if self._is_broke(project_id)
                    else "No blocking findings."
                ),
                "findings": [],
                "files_reviewed": file_count,
            }
        else:
            runtime = self._require_model_runtime()
            task_summary = [
                {
                    "key": item.key,
                    "status": item.status,
                    "files_modified": item.files_modified,
                    "result": item.result,
                }
                for item in self.projects.tasks(project_id)
                if item.key != "review"
            ]
            context = RepositoryContext(self.projects.repo_path(project_id)).render(
                prompt, self.settings.llm_context_characters
            )
            review = await runtime.final_review(prompt, context, task_summary)
            result = review.model_dump(mode="json")
            record = self.artifacts.create(
                project_id,
                task.id,
                "review_report",
                "final-review.json",
                review.model_dump_json(indent=2),
            )
            result["artifact_id"] = record.id

        if not result["approved"]:
            self._set_task(
                task.id,
                status=TaskState.FAILED.value,
                completed_at=datetime.now(UTC),
                logs=[str(result["summary"]), *[str(item) for item in result["findings"]]],
                result=result,
            )
            raise RuntimeError(f"final review rejected the result: {result['summary']}")
        self._set_task(
            task.id,
            status=TaskState.COMPLETED.value,
            completed_at=datetime.now(UTC),
            logs=[str(result["summary"]), *[str(item) for item in result["findings"]]],
            result=result,
        )
        self._update_progress(project_id)
        await self.events.publish(
            "review.completed",
            str(result["summary"]),
            project_id=project_id,
            task_id=task.id,
            payload={"severity": result["severity"], "runtime": self._project_runtime(project_id)},
        )

    def _create_tasks(self, project_id: str, plan: ProjectPlan) -> None:
        project = self.projects.get(project_id)
        run_id = project.active_run_id
        if not run_id:
            raise RuntimeError("project has no active run")
        maximum = int(project.options.get("maximum_retries", 3))
        id_map = {task.id: f"{project_id}:{run_id}:{task.id}" for task in plan.tasks}
        base_commit = self.projects.current_revision(project_id)
        contexts = {
            task.id: self.architecture.task_contract_context(project_id, task)
            for task in plan.tasks
        }
        with self.database.session() as session:
            for sequence, task in enumerate(plan.tasks):
                context = contexts[task.id]
                session.add(
                    TaskRecord(
                        id=id_map[task.id],
                        project_id=project_id,
                        run_id=run_id,
                        milestone_id=context["milestone_id"],
                        base_commit=base_commit,
                        contract_version=context["contract_version"],
                        allowed_files=context["allowed_files"],
                        acceptance_criteria=context["acceptance_criteria"],
                        key=task.id,
                        title=task.title,
                        description=task.description,
                        agent_role=task.suggested_agent,
                        status=(
                            TaskState.READY if not task.dependencies else TaskState.PENDING
                        ).value,
                        dependencies=[id_map[item] for item in task.dependencies],
                        required_capabilities=task.required_capabilities,
                        sequence=sequence,
                        parallelizable=task.parallelizable,
                        max_retries=maximum,
                    )
                )

    def _write_control_files(self, project_id: str, plan: ProjectPlan) -> None:
        root = self.projects.repo_path(project_id).parent
        control = root / "AEGAEON"
        plan_json = plan.model_dump(mode="json")
        (control / "plan.json").write_text(json.dumps(plan_json, indent=2), encoding="utf-8")
        (control / "tasks.json").write_text(
            json.dumps(plan_json["tasks"], indent=2), encoding="utf-8"
        )

    def _assert_dependencies_complete(self, project_id: str, plan_task: PlanTask) -> None:
        for dependency in plan_task.dependencies:
            task = self._task_record(project_id, dependency)
            if task.status != TaskState.COMPLETED.value:
                raise RuntimeError(
                    f"task {plan_task.id} is blocked by incomplete dependency {dependency}"
                )
        current = self._task_record(project_id, plan_task.id)
        if current.status == TaskState.PENDING.value:
            self._set_task(current.id, status=TaskState.READY.value)

    @staticmethod
    def _worker_instructions(
        task: TaskRecord,
        plan_task: PlanTask,
        base_commit: str,
        contract_context: dict[str, Any],
    ) -> str:
        return (
            "TASK\n"
            f"{plan_task.title}\n{plan_task.description}\n\n"
            "ROLE\n"
            f"{task.agent_role}\n\n"
            "MILESTONE\n"
            f"{contract_context['milestone']}\n\n"
            "BASE COMMIT\n"
            f"{base_commit}\n\n"
            "CONTRACT VERSION\n"
            f"{contract_context['contract_version']}\n\n"
            "CANONICAL INPUTS\n"
            "The contract, permitted paths, and acceptance criteria are supplied separately.\n\n"
            "If the canonical contract is insufficient, return a semantic "
            "contract_proposal. AEGAEON Core alone writes protected contract files.\n"
            + "\n\nDO NOT\n"
            "- rename shared models, fields, services, or API paths silently\n"
            "- create, update, delete, or rename a protected contract file\n"
            "- edit files outside the allowed scopes\n"
            "- treat your worker copy as canonical\n\n"
            "RETURN\n"
            "Complete repository-relative source files. The worker transport will wrap raw "
            "file contents in AEGAEON_RESPONSE_V1 and serialize the validated result."
        )

    def _task_record(self, project_id: str, key: str) -> TaskRecord:
        active_run_id = self.projects.get(project_id).active_run_id
        with self.database.session() as session:
            statement = (
                select(TaskRecord)
                .where(TaskRecord.project_id == project_id, TaskRecord.key == key)
                .order_by(TaskRecord.created_at.desc())
            )
            if active_run_id:
                statement = statement.where(TaskRecord.run_id == active_run_id)
            task = session.scalar(statement)
            if not task:
                raise RuntimeError(f"task not found: {key}")
            session.expunge(task)
            return task

    def _is_broke(self, project_id: str) -> bool:
        return self.projects.get(project_id).options.get("execution_mode") == "broke_boy"

    def _adaptive_compute_job(
        self,
        *,
        project_id: str,
        task: TaskRecord,
        plan_task: PlanTask,
        prompt: str,
        instructions: str,
        contract_context: dict[str, Any],
        attempt: int,
        failure_evidence: dict[str, Any],
    ) -> tuple[dict[str, Any], JobRequirements, Any]:
        options = self.projects.get(project_id).options
        previous_code = str(failure_evidence.get("failure_code", ""))
        diagnostics = dict(failure_evidence.get("diagnostics") or {})
        diagnostic_gpu = dict(diagnostics.get("gpu") or {})
        compact_failure = {
            key: failure_evidence.get(key)
            for key in (
                "attempt",
                "error_type",
                "error_message",
                "failure_code",
                "failure_classification",
                "failure_stage",
                "retry_strategy",
            )
            if failure_evidence.get(key) is not None
        }
        if diagnostic_gpu:
            compact_failure["gpu"] = {
                key: diagnostic_gpu.get(key)
                for key in ("device", "total_mb", "free_mb", "peak_allocated_mb")
                if diagnostic_gpu.get(key) is not None
            }
        if "error_message" in compact_failure:
            compact_failure["error_message"] = str(compact_failure["error_message"])[:1_000]
        memory_failure = previous_code in {
            FailureCode.CUDA_OOM.value,
            FailureCode.GENERATION_CUDA_OOM.value,
            FailureCode.MODEL_LOAD_CUDA_OOM.value,
            FailureCode.CONTEXT_TOO_LARGE.value,
        }
        structured_failure = previous_code in {
            FailureCode.INVALID_JSON.value,
            FailureCode.INVALID_MODEL_OUTPUT.value,
            FailureCode.OUTPUT_TRUNCATED.value,
        }
        if previous_code == FailureCode.OUTPUT_TRUNCATED.value:
            instructions += (
                "\n\nMANDATORY TRUNCATION RECOVERY SHARD\n"
                "The previous response exhausted its output budget. Return exactly one smallest "
                "coherent file needed by downstream tasks. Do not attempt the entire task, do not "
                "include a second file, and stop immediately after <<<END_RESPONSE>>>. Use raw "
                "file contents in AEGAEON_RESPONSE_V1; never serialize source code as JSON."
            )
        elif structured_failure:
            instructions += (
                "\n\nOUTPUT RECOVERY SHARD\n"
                "The previous response was malformed or exceeded its output budget. Produce "
                "a fresh, smallest coherent slice for this task using exactly 1 file. Return "
                "raw file contents in AEGAEON_RESPONSE_V1; do not serialize source code as JSON."
            )
        if memory_failure and attempt >= 2:
            instructions += (
                "\n\nMEMORY RECOVERY SHARD\n"
                "Implement only the smallest coherent foundation slice needed by downstream "
                "tasks. Prefer interfaces and essential entry points; limit the response to "
                "at most 12 files. Later tasks will complete the remaining scope."
            )

        intelligence = int(options.get("intelligence", 70))
        requested_output = 2_048 + (intelligence // 25) * 1_024
        requirements = self._worker_model_requirements(project_id, task_category=task.agent_role)
        repository_context = RepositoryContext(self.projects.repo_path(project_id)).render(
            f"{plan_task.title}\n{plan_task.description}",
            self.settings.llm_context_characters,
        )
        sections = [
            ContextSection(
                name="task",
                content=f"{plan_task.title}\n{plan_task.description}\n{instructions}",
                priority=100,
                protected=True,
            ),
            ContextSection(
                name="canonical_contract",
                content=json.dumps(contract_context["contract"], sort_keys=True),
                priority=100,
                protected=True,
            ),
            ContextSection(
                name="acceptance_criteria",
                content=json.dumps(contract_context["acceptance_criteria"]),
                priority=100,
                protected=True,
            ),
            ContextSection(name="repository", content=repository_context, priority=85),
            ContextSection(
                name="failure_evidence",
                content=json.dumps(compact_failure, sort_keys=True, separators=(",", ":")),
                priority=95,
            ),
            ContextSection(name="project_summary", content=prompt, priority=70),
        ]
        plan = self.compute.create_plan(
            project_id=project_id,
            task_id=task.id,
            task_description=f"{plan_task.title}\n{plan_task.description}",
            model_id=str(requirements.model),
            runtime=str(requirements.runtime or "transformers"),
            quantization=str(requirements.quantization or "4bit"),
            workers=self.workers.list(),
            model_catalog=self.scout.list(),
            options=options,
            context_sections=sections,
            requested_output_tokens=requested_output,
            attempt=attempt,
            previous_failure=previous_code or None,
        )
        packed = {
            section.name: section.content
            for section in (plan.context_plan.sections if plan.context_plan else [])
            if section.included
        }
        plan_payload = plan.model_dump(mode="json", exclude={"context_plan"})
        if plan.context_plan:
            plan_payload["context_plan"] = {
                "token_budget": plan.context_plan.token_budget,
                "packed_tokens": plan.context_plan.packed_tokens,
                "dropped_sections": plan.context_plan.dropped_sections,
                "sections": [
                    {
                        "name": section.name,
                        "estimated_tokens": section.estimated_tokens,
                        "included_tokens": section.included_tokens,
                        "included": section.included,
                        "protected": section.protected,
                    }
                    for section in plan.context_plan.sections
                ],
            }
        free_vram = int(plan.evidence.get("free_vram_mb", 0))
        requirements = requirements.model_copy(
            update={
                "minimum_free_vram_mb": min(
                    plan.estimated_vram_required_mb,
                    max(1024, int(free_vram * 0.8)),
                ),
                "minimum_available_ram_mb": (
                    plan.estimated_ram_required_mb if plan.offload_strategy != "NONE" else 0
                ),
                "compute_plan_id": plan.id,
                "preferred_worker_id": plan.worker_id,
            }
        )
        constrained = previous_code in {
            FailureCode.INVALID_JSON.value,
            FailureCode.INVALID_MODEL_OUTPUT.value,
            FailureCode.OUTPUT_TRUNCATED.value,
            FailureCode.OUTPUT_TOO_LARGE.value,
            FailureCode.CUDA_OOM.value,
            FailureCode.GENERATION_CUDA_OOM.value,
            FailureCode.MODEL_LOAD_CUDA_OOM.value,
            FailureCode.CONTEXT_TOO_LARGE.value,
        }
        file_limit = self._generation_file_limit(
            previous_code=previous_code,
            memory_failure=memory_failure,
            attempt=attempt,
            intelligence=intelligence,
            maximum_new_tokens=plan.maximum_new_tokens,
        )
        output_byte_limit = min(
            self.settings.maximum_output_bytes * (5 if constrained else 10),
            2_000_000,
            max(16_000, plan.maximum_new_tokens * 8),
        )
        payload = ModelGeneratePayload(
            role=task.agent_role,
            instructions=instructions,
            context=ModelGenerationContext(
                project_summary=packed.get("project_summary", prompt),
                repository=packed.get("repository", ""),
                interfaces=contract_context["contract"],
                acceptance_criteria=contract_context["acceptance_criteria"],
                permitted_file_scopes=contract_context["allowed_files"],
            ),
            failure_evidence=compact_failure,
            execution_plan=plan_payload,
            constraints=ModelGenerationConstraints(
                maximum_output_bytes=output_byte_limit,
                maximum_files=file_limit,
                maximum_new_tokens=plan.maximum_new_tokens,
            ),
        ).model_dump(mode="json")
        return payload, requirements, plan

    @staticmethod
    def _generation_file_limit(
        *,
        previous_code: str,
        memory_failure: bool,
        attempt: int,
        intelligence: int,
        maximum_new_tokens: int,
    ) -> int:
        """Translate recovery advice into a hard per-assignment file bound."""
        structured_failures = {
            FailureCode.INVALID_JSON.value,
            FailureCode.INVALID_MODEL_OUTPUT.value,
            FailureCode.OUTPUT_TRUNCATED.value,
        }
        if previous_code in structured_failures:
            return 1
        token_file_limit = max(1, min(6, maximum_new_tokens // 500))
        base_file_limit = 50 + (intelligence // 10) * 15
        requested = 6 if memory_failure and attempt >= 2 else min(200, base_file_limit)
        return min(requested, token_file_limit)

    def _worker_model_requirements(self, project_id: str, task_category: str) -> JobRequirements:
        options = self.projects.get(project_id).options
        return JobRequirements(
            gpu=True,
            minimum_vram_mb=int(options.get("selected_model_vram_mb") or 3_072),
            minimum_ram_mb=4_096,
            capabilities=["model.generate"],
            model=str(options.get("selected_model") or "Qwen/Qwen2.5-Coder-1.5B-Instruct"),
            runtime="transformers",
            quantization=str(options.get("quantization", "4bit")),
            task_category=task_category,
        )

    def _project_runtime(self, project_id: str) -> str:
        if self._is_broke(project_id):
            return "worker:transformers"
        if self.settings.demo_mode:
            return "controller:demo"
        return self.model_runtime.name if self.model_runtime else "model:unconfigured"

    def _require_model_runtime(self) -> LLMAgentRuntime:
        if self.model_runtime is None:
            raise RuntimeError("model runtime is not configured")
        return self.model_runtime

    def _set_active_run(
        self,
        project_id: str,
        status: str,
        *,
        started_at: datetime | None = None,
        completed_at: datetime | None = None,
        summary: str | None = None,
    ) -> None:
        with self.database.session() as session:
            project = session.get(ProjectRecord, project_id)
            run = session.get(RunRecord, project.active_run_id) if project else None
            if not run:
                return
            run.status = status
            if started_at is not None:
                run.started_at = started_at
            if completed_at is not None:
                run.completed_at = completed_at
            if summary is not None:
                run.summary = summary

    def _set_project(self, project_id: str, **values: Any) -> None:
        with self.database.session() as session:
            project = session.get(ProjectRecord, project_id)
            if not project:
                raise KeyError(project_id)
            for name, value in values.items():
                setattr(project, name, value)
            project.updated_at = datetime.now(UTC)
        root = self.projects.projects_dir / project_id
        metadata_path = root / "project.json"
        if metadata_path.exists():
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
            data.update(values)
            metadata_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _set_task(self, task_id: str, **values: Any) -> None:
        with self.database.session() as session:
            task = session.get(TaskRecord, task_id)
            if not task:
                raise KeyError(task_id)
            for name, value in values.items():
                setattr(task, name, value)

    def _update_progress(self, project_id: str) -> None:
        tasks = self.projects.tasks(project_id)
        complete = sum(task.status == TaskState.COMPLETED for task in tasks)
        progress = round(100 * complete / len(tasks)) if tasks else 0
        self._set_project(project_id, progress=progress)

    def _cancel_open_tasks(self, project_id: str) -> None:
        with self.database.session() as session:
            for task in session.query(TaskRecord).filter(TaskRecord.project_id == project_id):
                if task.status not in {TaskState.COMPLETED.value, TaskState.FAILED.value}:
                    task.status = TaskState.CANCELLED.value

    def _ensure_active(self, project_id: str) -> None:
        if project_id in self._cancelled:
            raise asyncio.CancelledError
