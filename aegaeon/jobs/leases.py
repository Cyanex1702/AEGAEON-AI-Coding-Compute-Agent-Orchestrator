from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from aegaeon.database.models import JobRecord, ProjectRecord, TaskRecord
from aegaeon.database.session import Database
from aegaeon.protocol.schemas import JobAssign, JobRead, JobState, TaskState

ACTIVE_JOB_STATES = {JobState.ASSIGNED.value, JobState.RUNNING.value}
ACTIVE_TASK_STATES = {
    TaskState.ASSIGNED.value,
    TaskState.RUNNING.value,
    TaskState.RETRYING.value,
}
ACTIVE_PROJECT_STATES = {"planning", "running", "testing", "reviewing"}


class JobLeaseManager:
    """Persists assignment leases so interrupted work can be recovered safely."""

    def __init__(self, database: Database, lease_seconds: int = 30) -> None:
        self.database = database
        self.lease_seconds = lease_seconds

    def queue(self, job: JobAssign, attempt: int) -> JobRead:
        now = datetime.now(UTC)
        with self.database.session() as session:
            record = JobRecord(
                id=job.job_id,
                project_id=job.project_id,
                task_id=job.task_id,
                job_type=job.job_type,
                agent_role=job.agent_role,
                status=JobState.QUEUED.value,
                attempt=attempt,
                requirements=job.requirements.model_dump(mode="json"),
                instructions=job.instructions,
                payload_summary={
                    "label": job.payload.get("label"),
                    "stage": job.payload.get("stage"),
                    "file_count": len(job.payload.get("files", {})),
                },
                created_at=now,
                updated_at=now,
            )
            session.add(record)
        return self.get(job.job_id)

    def assign(self, job_id: str, worker_id: str) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            job = self._get(session, job_id)
            job.worker_id = worker_id
            job.status = JobState.ASSIGNED.value
            job.assigned_at = now
            job.updated_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)

    def start(self, job_id: str) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            job = self._get(session, job_id)
            job.status = JobState.RUNNING.value
            job.started_at = now
            job.updated_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        self._finish(job_id, JobState.COMPLETED, result=result)

    def fail(self, job_id: str, error: str, result: dict[str, Any] | None = None) -> None:
        self._finish(job_id, JobState.FAILED, error=error, result=result or {})

    def complete_locally(self, job_id: str, result: dict[str, Any]) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            job = self._get(session, job_id)
            job.worker_id = "controller"
            job.status = JobState.COMPLETED.value
            job.assigned_at = now
            job.started_at = now
            job.completed_at = now
            job.updated_at = now
            job.result = result

    def interrupt(self, job_id: str, reason: str) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            job = self._get(session, job_id)
            if job.status not in ACTIVE_JOB_STATES:
                return
            self._interrupt_record(session, job, reason, now)

    def extend_worker(self, worker_id: str) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            jobs = session.scalars(
                select(JobRecord).where(
                    JobRecord.worker_id == worker_id,
                    JobRecord.status.in_(ACTIVE_JOB_STATES),
                )
            ).all()
            for job in jobs:
                job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                job.updated_at = now

    def interrupt_worker(self, worker_id: str, reason: str) -> list[str]:
        now = datetime.now(UTC)
        interrupted: list[str] = []
        with self.database.session() as session:
            jobs = session.scalars(
                select(JobRecord).where(
                    JobRecord.worker_id == worker_id,
                    JobRecord.status.in_(ACTIVE_JOB_STATES),
                )
            ).all()
            for job in jobs:
                self._interrupt_record(session, job, reason, now)
                interrupted.append(job.id)
        return interrupted

    def expire_leases(self) -> dict[str, list[str]]:
        now = datetime.now(UTC)
        expired: dict[str, list[str]] = {}
        with self.database.session() as session:
            jobs = session.scalars(
                select(JobRecord).where(JobRecord.status.in_(ACTIVE_JOB_STATES))
            ).all()
            for job in jobs:
                expires = self._aware(job.lease_expires_at)
                if expires is not None and expires <= now:
                    worker_id = job.worker_id or "unknown"
                    expired.setdefault(worker_id, []).append(job.id)
                    self._interrupt_record(session, job, "job lease expired", now)
        return expired

    def recover_orphans(self) -> set[str]:
        """Interrupt persisted in-flight jobs and return projects that can resume."""

        now = datetime.now(UTC)
        project_ids: set[str] = set()
        with self.database.session() as session:
            jobs = session.scalars(
                select(JobRecord).where(JobRecord.status.in_(ACTIVE_JOB_STATES))
            ).all()
            for job in jobs:
                project_ids.add(job.project_id)
                self._interrupt_record(
                    session, job, "controller restarted while the job was in flight", now
                )
            tasks = session.scalars(
                select(TaskRecord).where(TaskRecord.status.in_(ACTIVE_TASK_STATES))
            ).all()
            for task in tasks:
                project = session.get(ProjectRecord, task.project_id)
                if project and project.status in ACTIVE_PROJECT_STATES:
                    project_ids.add(task.project_id)
                    task.status = TaskState.RETRYING.value
                    task.worker_id = None
                    task.job_id = None
                    task.logs = [*task.logs, "Recovered after controller restart."]
        return project_ids

    def cancel_project(self, project_id: str) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            jobs = session.scalars(
                select(JobRecord).where(
                    JobRecord.project_id == project_id,
                    JobRecord.status.in_(
                        [JobState.QUEUED.value, JobState.ASSIGNED.value, JobState.RUNNING.value]
                    ),
                )
            ).all()
            for job in jobs:
                job.status = JobState.CANCELLED.value
                job.completed_at = now
                job.updated_at = now
                job.lease_expires_at = None

    def list(self, project_id: str | None = None, limit: int = 500) -> list[JobRead]:
        with self.database.session() as session:
            statement = select(JobRecord).order_by(JobRecord.created_at.desc()).limit(limit)
            if project_id:
                statement = statement.where(JobRecord.project_id == project_id)
            return [JobRead.model_validate(item) for item in session.scalars(statement).all()]

    def get(self, job_id: str) -> JobRead:
        with self.database.session() as session:
            return JobRead.model_validate(self._get(session, job_id))

    def _finish(
        self,
        job_id: str,
        status: JobState,
        *,
        result: dict[str, Any],
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        with self.database.session() as session:
            job = self._get(session, job_id)
            job.status = status.value
            job.result = result
            job.error = error
            job.completed_at = now
            job.updated_at = now
            job.lease_expires_at = None

    @staticmethod
    def _interrupt_record(session: Any, job: JobRecord, reason: str, now: datetime) -> None:
        job.status = JobState.INTERRUPTED.value
        job.error = reason
        job.completed_at = now
        job.updated_at = now
        job.lease_expires_at = None
        task = session.get(TaskRecord, job.task_id)
        if task and task.status in ACTIVE_TASK_STATES:
            task.status = TaskState.RETRYING.value
            task.worker_id = None
            task.job_id = None
            task.logs = [*task.logs, reason]

    @staticmethod
    def _get(session: Any, job_id: str) -> JobRecord:
        job = session.get(JobRecord, job_id)
        if not job:
            raise KeyError(job_id)
        return job

    @staticmethod
    def _aware(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)
