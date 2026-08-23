from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import delete

from aegaeon.agents.demo import DemoAgentRuntime
from aegaeon.agents.deterministic import BrokeBoyPlanner
from aegaeon.agents.llm import LLMAgentRuntime
from aegaeon.artifacts.manager import ArtifactManager
from aegaeon.config import Settings
from aegaeon.database.models import ProjectRecord, TaskRecord
from aegaeon.database.session import Database
from aegaeon.events import EventBus
from aegaeon.execution.runner import SubprocessRunner
from aegaeon.execution.tests import TestCommandDetector
from aegaeon.jobs.leases import JobLeaseManager
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
        model_runtime: LLMAgentRuntime | None = None,
        test_detector: TestCommandDetector | None = None,
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
        self.model_runtime = model_runtime
        self.test_detector = test_detector or TestCommandDetector()
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._cancelled: set[str] = set()
        self._integration_locks: dict[str, asyncio.Lock] = {}

    def start(self, project_id: str) -> None:
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
        self._cancelled.discard(project_id)
        self._runs[project_id] = asyncio.create_task(
            self._run(project_id, recovered=False), name=f"project-run:{project_id}"
        )

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
            self._runs[project_id] = asyncio.create_task(
                self._run(project_id, recovered=True),
                name=f"project-recovery:{project_id}",
            )

    async def cancel(self, project_id: str) -> None:
        self.projects.get(project_id)
        self._cancelled.add(project_id)
        task = self._runs.get(project_id)
        if task and not task.done():
            task.cancel()
        self.leases.cancel_project(project_id)
        self._set_project(project_id, status=ProjectStatus.CANCELLED.value)
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
                payload={"runtime": self._runtime_name},
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
                self._create_tasks(project_id, plan)
                self._write_control_files(project_id, plan)
                await self._complete_planner(project_id, plan)

            self._set_project(project_id, status=ProjectStatus.RUNNING.value)
            await self._execute_plan(project_id, project.prompt, plan)

            self._set_project(
                project_id,
                status=ProjectStatus.COMPLETED.value,
                progress=100,
                summary=plan.project_summary,
            )
            await self.events.publish(
                "project.completed",
                "Project completed with passing verification and review",
                project_id=project_id,
                payload={
                    "workspace": str(self.projects.repo_path(project_id)),
                    "runtime": self._runtime_name,
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_project(project_id, status=ProjectStatus.FAILED.value)
            await self.events.publish(
                "project.failed", str(exc), project_id=project_id, payload={"error": str(exc)}
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
                    self._set_task(f"{project_id}:{item.id}", status=TaskState.BLOCKED.value)
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
        if plan_task.id == "verification":
            if not await self._run_verification(project_id, prompt):
                raise RuntimeError(
                    "generated project verification did not pass within the retry limit"
                )
        elif plan_task.id == "review":
            await self._complete_review(project_id, prompt)
        else:
            await self._run_generation_with_retries(project_id, plan_task, prompt)

    async def _run_generation_with_retries(
        self, project_id: str, plan_task: PlanTask, prompt: str
    ) -> None:
        record = self._task_record(project_id, plan_task.id)
        retry_enabled = bool(self.projects.get(project_id).options.get("retry_failed_tasks", True))
        start_attempt = record.retry_count
        for attempt in range(start_attempt, record.max_retries + 1):
            try:
                await self._run_generation_task(project_id, plan_task, prompt, attempt)
                return
            except Exception as exc:
                if attempt >= record.max_retries or not retry_enabled:
                    current = self._task_record(project_id, plan_task.id)
                    self._set_task(
                        current.id,
                        status=TaskState.FAILED.value,
                        completed_at=datetime.now(UTC),
                        logs=[*current.logs, str(exc)],
                    )
                    raise
                current = self._task_record(project_id, plan_task.id)
                next_attempt = attempt + 1
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
                    payload={"attempt": next_attempt + 1, "error": str(exc)},
                )
                await asyncio.sleep(0)

    async def _plan(self, prompt: str, project_id: str) -> ProjectPlan:
        if self._is_broke(project_id):
            return self.broke_planner.plan(prompt)
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
            payload={"runtime": self._runtime_name},
        )
        self._set_task(
            task.id,
            status=TaskState.COMPLETED.value,
            completed_at=datetime.now(UTC),
            result={
                "project_summary": plan.project_summary,
                "task_count": len(plan.tasks),
                "runtime": self._runtime_name,
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

    async def _run_generation_task(
        self, project_id: str, plan_task: PlanTask, prompt: str, attempt: int
    ) -> None:
        task = self._task_record(project_id, plan_task.id)
        started = time.perf_counter()
        controller_id = self._runtime_name
        base_revision = self.projects.current_revision(project_id)
        self._set_task(
            task.id,
            status=TaskState.RUNNING.value,
            worker_id=controller_id,
            started_at=datetime.now(UTC),
        )
        await self.events.publish(
            "task.started",
            f"{task.agent_role.title()} agent started {task.title.lower()}",
            project_id=project_id,
            task_id=task.id,
            worker_id=controller_id,
            payload={"runtime": self._runtime_name},
        )

        try:
            broke_mode = self._is_broke(project_id)
            if broke_mode:
                context = RepositoryContext(self.projects.repo_path(project_id)).render(
                    f"{plan_task.title}\n{plan_task.description}",
                    self.settings.llm_context_characters,
                )
                payload = ModelGeneratePayload(
                    role=task.agent_role,
                    instructions=task.description,
                    context=ModelGenerationContext(
                        project_summary=prompt,
                        repository=context,
                        interfaces=self.projects.get(project_id).options.get("interfaces", {}),
                        acceptance_criteria=[prompt, plan_task.description],
                        permitted_file_scopes=["**/*"],
                    ),
                    constraints=ModelGenerationConstraints(
                        maximum_output_bytes=min(
                            self.settings.maximum_output_bytes * 10, 2_000_000
                        ),
                        maximum_files=200,
                        maximum_new_tokens=4_096,
                    ),
                ).model_dump(mode="json")
                summary = f"Worker-hosted model generated {plan_task.title.lower()}"
                job_type = "model.generate"
                requirements = self._worker_model_requirements(
                    project_id, task_category=task.agent_role
                )
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
                change_set = await runtime.generate(prompt, plan_task, context)
                files = change_set.as_file_map()
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
            job = JobAssign(
                job_id=f"job-{uuid4().hex[:12]}",
                job_type=job_type,
                project_id=project_id,
                task_id=task.id,
                agent_role=task.agent_role,
                requirements=requirements,
                instructions=task.description,
                payload=payload,
            )
            self.leases.queue(job, attempt)
            decision = await self.workers.reserve_worker(job)
            if broke_mode and decision is None:
                raise RuntimeError(
                    "No compatible model worker is ready for this task. Check model, runtime, "
                    "quantization, capability, and VRAM requirements."
                )
            worker_id = decision.worker_id if decision else f"controller:{self._runtime_name}"
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
                        )
                    ],
                    result={"runtime": self._runtime_name, "materializer": "controller"},
                )
                self.leases.complete_locally(job.job_id, result.result)
            if result.type == "job.failed":
                raise RuntimeError(result.error or "worker job failed")
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
                    "worker": worker_id,
                    "files": modified,
                    "runtime": self._runtime_name,
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
                payload={"files": modified, "runtime": self._runtime_name},
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
        all_files: dict[str, str] = {}
        for artifact in result.artifacts:
            content: str | dict[str, str] = artifact.files or (artifact.content or "")
            record = self.artifacts.create(
                project_id, task_id, artifact.type, artifact.filename, content
            )
            await self.events.publish(
                "artifact.created",
                f"Stored {record.filename}",
                project_id=project_id,
                task_id=task_id,
                payload={"artifact_id": record.id, "type": record.type},
            )
            if artifact.type == "source_bundle":
                all_files.update(artifact.files)
        if not all_files:
            raise RuntimeError("worker returned no source files")
        lock = self._integration_locks.setdefault(project_id, asyncio.Lock())
        async with lock:
            modified, patch = self.projects.integrate_files(
                project_id,
                task_key,
                title,
                all_files,
                base_revision=base_revision,
            )
        patch_record = self.artifacts.create(
            project_id, task_id, "git_patch", f"{task_key}.patch", patch
        )
        await self.events.publish(
            "artifact.created",
            f"Created canonical Git patch for {title.lower()}",
            project_id=project_id,
            task_id=task_id,
            payload={"artifact_id": patch_record.id, "type": "git_patch"},
        )
        return modified

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
            commands, notes = self.test_detector.detect(repo)
            await self.events.publish(
                "tests.started",
                f"Running {len(commands)} detected verification command(s), attempt {attempt + 1}",
                project_id=project_id,
                task_id=task.id,
                payload={"attempt": attempt + 1, "commands": commands, "notes": notes},
            )
            executions = []
            for command in commands:
                execution = await self.runner.run(command, repo)
                executions.append(execution.model_dump(mode="json"))
                if execution.exit_code != 0:
                    break
            passed = bool(commands) and all(item["exit_code"] == 0 for item in executions)
            report: dict[str, Any] = {
                "attempt": attempt + 1,
                "exit_code": (
                    0 if passed else int(executions[-1]["exit_code"]) if executions else -1
                ),
                "passed": passed,
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
            payload={"files": modified, "runtime": self._runtime_name},
        )

    async def _apply_worker_fix(
        self,
        project_id: str,
        task_id: str,
        prompt: str,
        report: dict[str, Any],
        retry_count: int,
    ) -> None:
        base_revision = self.projects.current_revision(project_id)
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
        job = JobAssign(
            job_id=f"job-{uuid4().hex[:12]}",
            job_type="model.repair",
            project_id=project_id,
            task_id=task_id,
            agent_role="repair",
            requirements=self._worker_model_requirements(project_id, "repair"),
            instructions=payload.instructions,
            payload=payload.model_dump(mode="json"),
        )
        self.leases.queue(job, retry_count)
        decision = await self.workers.reserve_worker(job)
        if decision is None:
            raise RuntimeError("No compatible model worker is ready for the repair job")
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
            payload={"files": modified, "runtime": self._runtime_name},
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
            payload={"runtime": self._runtime_name},
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
            payload={"severity": result["severity"], "runtime": self._runtime_name},
        )

    def _create_tasks(self, project_id: str, plan: ProjectPlan) -> None:
        maximum = int(self.projects.get(project_id).options.get("maximum_retries", 3))
        id_map = {task.id: f"{project_id}:{task.id}" for task in plan.tasks}
        with self.database.session() as session:
            session.execute(delete(TaskRecord).where(TaskRecord.project_id == project_id))
            for sequence, task in enumerate(plan.tasks):
                session.add(
                    TaskRecord(
                        id=id_map[task.id],
                        project_id=project_id,
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

    def _task_record(self, project_id: str, key: str) -> TaskRecord:
        with self.database.session() as session:
            task = session.get(TaskRecord, f"{project_id}:{key}")
            if not task:
                raise RuntimeError(f"task not found: {key}")
            session.expunge(task)
            return task

    def _is_broke(self, project_id: str) -> bool:
        return self.projects.get(project_id).options.get("execution_mode") == "broke_boy"

    def _worker_model_requirements(self, project_id: str, task_category: str) -> JobRequirements:
        options = self.projects.get(project_id).options
        return JobRequirements(
            gpu=True,
            minimum_vram_mb=int(options.get("selected_model_vram_mb", 3_072)),
            minimum_ram_mb=4_096,
            capabilities=["model.generate"],
            model=str(options.get("selected_model") or "Qwen/Qwen2.5-Coder-1.5B-Instruct"),
            runtime="transformers",
            quantization=str(options.get("quantization", "4bit")),
            task_category=task_category,
        )

    @property
    def _runtime_name(self) -> str:
        if self.settings.demo_mode:
            return "demo"
        return self.model_runtime.name if self.model_runtime else "model:unconfigured"

    def _require_model_runtime(self) -> LLMAgentRuntime:
        if self.model_runtime is None:
            raise RuntimeError("model runtime is not configured")
        return self.model_runtime

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
