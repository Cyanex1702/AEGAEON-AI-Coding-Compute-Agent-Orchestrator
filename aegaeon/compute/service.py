from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from aegaeon.compute.planner import AdaptiveComputePlanner
from aegaeon.compute.schemas import (
    ComputeIncident,
    ComputeObservation,
    ComputeOverview,
    ComputePolicy,
    ContextSection,
    IncidentState,
    ModelExecutionPlan,
    RecoveryAction,
)
from aegaeon.database.models import (
    ComputeIncidentRecord,
    ComputeObservationRecord,
    ComputePolicyRecord,
    ModelExecutionPlanRecord,
)
from aegaeon.database.session import Database


class ComputeIntelligence:
    """Persistent facade for planning, observations, recovery incidents, and UI state."""

    def __init__(self, database: Database, planner: AdaptiveComputePlanner | None = None) -> None:
        self.database = database
        self.planner = planner or AdaptiveComputePlanner()

    def policy(
        self, project_id: str | None, options: dict[str, Any] | None = None
    ) -> ComputePolicy:
        if project_id:
            with self.database.session() as session:
                record = session.get(ComputePolicyRecord, project_id)
                if record:
                    return ComputePolicy.model_validate(record.policy)
        return self.planner.policy_from_options(options)

    def save_policy(self, project_id: str, policy: ComputePolicy) -> ComputePolicy:
        with self.database.session() as session:
            record = session.get(ComputePolicyRecord, project_id)
            if record is None:
                record = ComputePolicyRecord(project_id=project_id)
                session.add(record)
            record.policy = policy.model_dump(mode="json")
            record.updated_at = datetime.now(UTC)
        return policy

    def create_plan(
        self,
        *,
        project_id: str | None,
        task_id: str | None,
        task_description: str,
        model_id: str,
        runtime: str,
        quantization: str,
        workers: list[Any],
        model_catalog: list[Any],
        options: dict[str, Any] | None,
        context_sections: list[ContextSection],
        requested_output_tokens: int | None,
        attempt: int,
        previous_failure: str | None,
    ) -> ModelExecutionPlan:
        plan = self.planner.plan(
            project_id=project_id,
            task_id=task_id,
            task_description=task_description,
            model_id=model_id,
            runtime=runtime,
            quantization=quantization,
            workers=workers,
            model_catalog=model_catalog,
            policy=self.policy(project_id, options),
            context_sections=context_sections,
            requested_output_tokens=requested_output_tokens,
            attempt=attempt,
            previous_failure=previous_failure,
            observations=self.observations(model_id=model_id, limit=100),
        )
        with self.database.session() as session:
            session.add(
                ModelExecutionPlanRecord(
                    id=plan.id,
                    project_id=project_id,
                    task_id=task_id,
                    worker_id=plan.worker_id,
                    status="PLANNED",
                    plan=plan.model_dump(mode="json"),
                )
            )
        return plan

    def bind_job(self, plan_id: str, job_id: str, worker_id: str | None = None) -> None:
        with self.database.session() as session:
            record = session.get(ModelExecutionPlanRecord, plan_id)
            if not record:
                return
            record.job_id = job_id
            record.worker_id = worker_id or record.worker_id
            record.status = "DISPATCHED"
            plan = dict(record.plan)
            plan["status"] = "DISPATCHED"
            plan["actual_worker_id"] = worker_id
            record.plan = plan
            record.updated_at = datetime.now(UTC)

    def plan(self, plan_id: str) -> ModelExecutionPlan:
        with self.database.session() as session:
            record = session.get(ModelExecutionPlanRecord, plan_id)
            if not record:
                raise KeyError(plan_id)
            return ModelExecutionPlan.model_validate(record.plan)

    def plans(
        self,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[ModelExecutionPlan]:
        with self.database.session() as session:
            statement = select(ModelExecutionPlanRecord)
            if project_id:
                statement = statement.where(ModelExecutionPlanRecord.project_id == project_id)
            if task_id:
                statement = statement.where(ModelExecutionPlanRecord.task_id == task_id)
            rows = session.scalars(
                statement.order_by(ModelExecutionPlanRecord.created_at.desc()).limit(limit)
            ).all()
            return [ModelExecutionPlan.model_validate(row.plan) for row in rows]

    def observations(
        self,
        *,
        project_id: str | None = None,
        model_id: str | None = None,
        limit: int = 100,
    ) -> list[ComputeObservation]:
        with self.database.session() as session:
            statement = select(ComputeObservationRecord)
            if project_id:
                statement = statement.where(ComputeObservationRecord.project_id == project_id)
            if model_id:
                statement = statement.where(ComputeObservationRecord.model_id == model_id)
            rows = session.scalars(
                statement.order_by(ComputeObservationRecord.created_at.desc()).limit(limit)
            ).all()
            return [self._observation(row) for row in rows]

    def incidents(
        self,
        *,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[ComputeIncident]:
        with self.database.session() as session:
            statement = select(ComputeIncidentRecord)
            if project_id:
                statement = statement.where(ComputeIncidentRecord.project_id == project_id)
            rows = session.scalars(
                statement.order_by(ComputeIncidentRecord.updated_at.desc()).limit(limit)
            ).all()
            return [self._incident(row) for row in rows]

    def record_success(
        self,
        plan_id: str,
        *,
        worker_id: str | None,
        metrics: dict[str, Any],
    ) -> ComputeObservation:
        plan = self.plan(plan_id)
        gpu = dict(metrics.get("gpu") or {})
        observation = ComputeObservation(
            id=f"compute-observation-{uuid4().hex}",
            project_id=plan.project_id,
            task_id=plan.task_id,
            plan_id=plan.id,
            worker_id=worker_id or plan.worker_id,
            model_id=plan.model_id,
            runtime=plan.runtime,
            quantization=plan.quantization,
            gpu_name=str(gpu.get("device") or gpu.get("name") or "Unknown GPU"),
            vram_total_mb=int(gpu.get("total_mb") or plan.evidence.get("total_vram_mb") or 0),
            prompt_tokens=int(metrics.get("prompt_tokens") or 0),
            output_limit=plan.maximum_new_tokens,
            generated_tokens=int(metrics.get("generated_tokens") or 0),
            peak_vram_mb=int(gpu.get("peak_allocated_mb") or metrics.get("peak_vram_mb") or 0),
            elapsed_seconds=float(metrics.get("elapsed_seconds") or 0),
            result="SUCCESS",
        )
        self._store_observation(observation)
        now = datetime.now(UTC)
        with self.database.session() as session:
            plan_record = session.get(ModelExecutionPlanRecord, plan_id)
            if plan_record:
                plan_record.status = "SUCCEEDED"
                stored = dict(plan_record.plan)
                stored["status"] = "SUCCEEDED"
                stored["actual_worker_id"] = worker_id or stored.get("worker_id")
                plan_record.plan = stored
                plan_record.updated_at = now
            open_incidents = session.scalars(
                select(ComputeIncidentRecord).where(
                    ComputeIncidentRecord.project_id == (plan.project_id or ""),
                    ComputeIncidentRecord.task_id == (plan.task_id or ""),
                    ComputeIncidentRecord.state.in_(
                        [IncidentState.RECOVERING.value, IncidentState.RETRYING.value]
                    ),
                )
            ).all()
            for incident in open_incidents:
                incident.state = IncidentState.RESOLVED.value
                incident.resolved_at = now
                incident.updated_at = now
                incident.message = "Automatically recovered and completed successfully."
        return observation

    def record_failure(
        self,
        plan_id: str,
        *,
        worker_id: str | None,
        classification: str,
        message: str,
        diagnostics: dict[str, Any],
        attempt: int,
        final: bool,
    ) -> tuple[ComputeObservation, ComputeIncident | None]:
        plan = self.plan(plan_id)
        gpu = dict(diagnostics.get("gpu") or {})
        generation = dict(diagnostics.get("generation") or {})
        observation = ComputeObservation(
            id=f"compute-observation-{uuid4().hex}",
            project_id=plan.project_id,
            task_id=plan.task_id,
            plan_id=plan.id,
            worker_id=worker_id or plan.worker_id,
            model_id=plan.model_id,
            runtime=plan.runtime,
            quantization=plan.quantization,
            gpu_name=str(gpu.get("device") or gpu.get("name") or "Unknown GPU"),
            vram_total_mb=int(gpu.get("total_mb") or plan.evidence.get("total_vram_mb") or 0),
            prompt_tokens=int(
                diagnostics.get("prompt_tokens") or generation.get("prompt_tokens") or 0
            ),
            output_limit=int(diagnostics.get("requested_output_tokens") or plan.maximum_new_tokens),
            generated_tokens=int(generation.get("generated_tokens") or 0),
            peak_vram_mb=int(gpu.get("peak_allocated_mb") or 0),
            elapsed_seconds=float(diagnostics.get("elapsed_seconds") or 0),
            result=(
                "OOM"
                if classification.endswith("CUDA_OOM") or classification == "CONTEXT_TOO_LARGE"
                else "FAILED"
            ),
            failure_classification=classification,
        )
        self._store_observation(observation)
        with self.database.session() as session:
            plan_record = session.get(ModelExecutionPlanRecord, plan_id)
            if plan_record:
                plan_record.status = "FAILED"
                stored = dict(plan_record.plan)
                stored["status"] = "FAILED"
                stored["actual_worker_id"] = worker_id or stored.get("actual_worker_id")
                plan_record.plan = stored
                plan_record.updated_at = datetime.now(UTC)
        if not (classification.endswith("CUDA_OOM") or classification == "CONTEXT_TOO_LARGE"):
            return observation, None
        incident = self._upsert_oom_incident(
            plan,
            worker_id=worker_id,
            message=message,
            diagnostics=diagnostics,
            attempt=attempt,
            final=final,
        )
        return observation, incident

    def overview(
        self,
        *,
        workers: list[Any],
        project_id: str | None = None,
        options: dict[str, Any] | None = None,
    ) -> ComputeOverview:
        states = [self.planner.worker_state(worker) for worker in workers]
        plans = self.plans(project_id=project_id, limit=20)
        observations = self.observations(project_id=project_id, limit=50)
        incidents = self.incidents(project_id=project_id, limit=30)
        total_vram = sum(worker.total_vram_mb for worker in states)
        free_vram = sum(worker.free_vram_mb for worker in states)
        return ComputeOverview(
            policy=self.policy(project_id, options),
            workers=states,
            active_plans=plans,
            observations=observations,
            incidents=incidents,
            summary={
                "online_workers": sum(
                    worker.status in {"online", "idle", "ready", "busy"} for worker in states
                ),
                "gpu_count": sum(len(worker.gpus) for worker in states),
                "total_vram_mb": total_vram,
                "free_vram_mb": free_vram,
                "active_incidents": sum(
                    item.state in {IncidentState.RECOVERING, IncidentState.RETRYING}
                    for item in incidents
                ),
                "historical_ooms": sum(
                    item.failure_classification is not None
                    and (
                        item.failure_classification.endswith("CUDA_OOM")
                        or item.failure_classification == "CONTEXT_TOO_LARGE"
                    )
                    for item in observations
                ),
            },
        )

    def _store_observation(self, item: ComputeObservation) -> None:
        with self.database.session() as session:
            session.add(ComputeObservationRecord(**item.model_dump(mode="python")))

    def _upsert_oom_incident(
        self,
        plan: ModelExecutionPlan,
        *,
        worker_id: str | None,
        message: str,
        diagnostics: dict[str, Any],
        attempt: int,
        final: bool,
    ) -> ComputeIncident:
        now = datetime.now(UTC)
        project_id = plan.project_id or "unknown-project"
        task_id = plan.task_id or "unknown-task"
        with self.database.session() as session:
            record = session.scalar(
                select(ComputeIncidentRecord)
                .where(
                    ComputeIncidentRecord.project_id == project_id,
                    ComputeIncidentRecord.task_id == task_id,
                    ComputeIncidentRecord.classification.in_(
                        [
                            "CUDA_OOM",
                            "GENERATION_CUDA_OOM",
                            "MODEL_LOAD_CUDA_OOM",
                            "CONTEXT_TOO_LARGE",
                        ]
                    ),
                    ComputeIncidentRecord.state.in_(
                        [IncidentState.RECOVERING.value, IncidentState.RETRYING.value]
                    ),
                )
                .order_by(ComputeIncidentRecord.updated_at.desc())
            )
            root_failure_id = (
                record.root_failure_id
                if record
                else str(diagnostics.get("root_failure_id") or f"{project_id}:{task_id}:oom")
            )
            action = (
                RecoveryAction.ESCALATE_USER
                if final
                else plan.next_recovery_action or RecoveryAction.RETRY_REDUCED_OUTPUT
            )
            attempt_entry = {
                "attempt": attempt,
                "plan_id": plan.id,
                "worker_id": worker_id or plan.worker_id,
                "model_id": plan.model_id,
                "prompt_tokens": diagnostics.get("prompt_tokens"),
                "output_limit": diagnostics.get("requested_output_tokens")
                or plan.maximum_new_tokens,
                "gpu": diagnostics.get("gpu") or {},
                "result": "CUDA_OOM",
                "recovery_action": action.value,
                "created_at": now.isoformat(),
            }
            if record is None:
                record = ComputeIncidentRecord(
                    id=f"compute-incident-{uuid4().hex}",
                    root_failure_id=root_failure_id,
                    project_id=project_id,
                    task_id=task_id,
                    worker_id=worker_id or plan.worker_id,
                    model_id=plan.model_id,
                    gpu_name=str((diagnostics.get("gpu") or {}).get("device") or "Unknown GPU"),
                    classification=str(diagnostics.get("classification") or "GENERATION_CUDA_OOM"),
                    state=(
                        IncidentState.FAILED_FINAL.value
                        if final
                        else IncidentState.RECOVERING.value
                    ),
                    attempts=[attempt_entry],
                    recovery_action=action.value,
                    message=message,
                    created_at=now,
                    updated_at=now,
                )
                session.add(record)
            else:
                record.attempts = [*list(record.attempts or []), attempt_entry]
                record.worker_id = worker_id or plan.worker_id
                record.state = (
                    IncidentState.FAILED_FINAL.value if final else IncidentState.RETRYING.value
                )
                record.recovery_action = action.value
                record.message = message
                record.updated_at = now
            session.flush()
            return self._incident(record)

    @staticmethod
    def _observation(record: ComputeObservationRecord) -> ComputeObservation:
        return ComputeObservation(
            id=record.id,
            project_id=record.project_id,
            task_id=record.task_id,
            plan_id=record.plan_id,
            worker_id=record.worker_id,
            model_id=record.model_id,
            runtime=record.runtime,
            quantization=record.quantization,
            gpu_name=record.gpu_name,
            vram_total_mb=record.vram_total_mb,
            prompt_tokens=record.prompt_tokens,
            output_limit=record.output_limit,
            generated_tokens=record.generated_tokens,
            peak_vram_mb=record.peak_vram_mb,
            elapsed_seconds=record.elapsed_seconds,
            result=record.result,
            failure_classification=record.failure_classification,
            created_at=record.created_at,
        )

    @staticmethod
    def _incident(record: ComputeIncidentRecord) -> ComputeIncident:
        return ComputeIncident(
            id=record.id,
            root_failure_id=record.root_failure_id,
            project_id=record.project_id,
            task_id=record.task_id,
            worker_id=record.worker_id,
            model_id=record.model_id,
            gpu_name=record.gpu_name,
            classification=record.classification,
            state=record.state,
            attempts=list(record.attempts or []),
            recovery_action=record.recovery_action,
            message=record.message,
            created_at=record.created_at,
            updated_at=record.updated_at,
            resolved_at=record.resolved_at,
        )
