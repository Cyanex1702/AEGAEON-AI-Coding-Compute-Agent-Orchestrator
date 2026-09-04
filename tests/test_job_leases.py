from __future__ import annotations

import asyncio

import pytest

from aegaeon.database.models import ProjectRecord, TaskRecord
from aegaeon.database.session import Database
from aegaeon.events import EventBus
from aegaeon.jobs.leases import JobLeaseManager
from aegaeon.protocol.schemas import (
    HardwareInfo,
    JobAssign,
    JobRequirements,
    JobState,
    TaskState,
    WorkerRegister,
)
from aegaeon.workers.registry import WorkerDisconnectedError, WorkerRegistry
from aegaeon.workers.scheduler import CapabilityScheduler


class FakeSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.closed = False
        self.close_code: int | None = None
        self.close_reason: str | None = None

    async def send_json(self, data: dict[str, object]) -> None:
        self.messages.append(data)

    async def close(self, code: int, reason: str) -> None:
        self.closed = True
        self.close_code = code
        self.close_reason = reason


def seeded_database(tmp_path) -> tuple[Database, str, str]:
    database = Database(f"sqlite:///{tmp_path / 'leases.db'}")
    database.create_all()
    project_id = "project-leases"
    task_id = f"{project_id}:implementation"
    with database.session() as session:
        session.add(
            ProjectRecord(
                id=project_id,
                name="Lease test",
                prompt="Build a tested service",
                status="running",
                strategy="single_model_multi_agent",
                workspace_path=str(tmp_path / "repo"),
                options={},
            )
        )
        session.add(
            TaskRecord(
                id=task_id,
                project_id=project_id,
                key="implementation",
                title="Implementation",
                description="Implement",
                status=TaskState.READY.value,
            )
        )
    return database, project_id, task_id


def make_job(job_id: str, project_id: str, task_id: str) -> JobAssign:
    return JobAssign(
        job_id=job_id,
        job_type="source.materialize",
        project_id=project_id,
        task_id=task_id,
        agent_role="coder",
        requirements=JobRequirements(capabilities=["python"]),
        instructions="Materialize source",
        payload={"files": {"app.py": "VALUE = 1\n"}},
    )


def registration(worker_id: str) -> WorkerRegister:
    return WorkerRegister(
        worker_id=worker_id,
        hostname=worker_id,
        hardware=HardwareInfo(cpu_cores=4, ram_mb=8192),
        capabilities=["python", "git"],
        models=[],
    )


def resumable_registration(worker_id: str) -> WorkerRegister:
    return registration(worker_id).model_copy(
        update={"protocol_version": 2, "session_id": f"session-{worker_id}"}
    )


def test_restart_recovery_interrupts_durable_job_and_requeues_task(tmp_path) -> None:
    database, project_id, task_id = seeded_database(tmp_path)
    leases = JobLeaseManager(database, lease_seconds=30)
    job = make_job("job-restart", project_id, task_id)
    leases.queue(job, attempt=0)
    leases.assign(job.job_id, "worker-a")
    leases.start(job.job_id)
    with database.session() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.status = TaskState.RUNNING.value
        task.worker_id = "worker-a"
        task.job_id = job.job_id

    recovered = leases.recover_orphans()

    assert recovered == {project_id}
    assert leases.get(job.job_id).status == JobState.INTERRUPTED.value
    with database.session() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        assert task.status == TaskState.RETRYING.value
        assert task.worker_id is None
        assert task.job_id is None
    database.close()


@pytest.mark.asyncio
async def test_atomic_reservations_use_distinct_workers(tmp_path) -> None:
    database, project_id, task_id = seeded_database(tmp_path)
    leases = JobLeaseManager(database, lease_seconds=30)
    registry = WorkerRegistry(
        database,
        EventBus(database),
        CapabilityScheduler(),
        heartbeat_timeout_seconds=20,
        leases=leases,
        assignment_timeout_seconds=10,
    )
    await registry.register(registration("worker-a"), FakeSocket())
    await registry.register(registration("worker-b"), FakeSocket())
    first = make_job("job-one", project_id, task_id)
    second = make_job("job-two", project_id, task_id)
    leases.queue(first, attempt=0)
    leases.queue(second, attempt=0)

    first_decision = await registry.reserve_worker(first)
    second_decision = await registry.reserve_worker(second)

    assert first_decision is not None
    assert second_decision is not None
    assert first_decision.worker_id != second_decision.worker_id
    database.close()


@pytest.mark.asyncio
async def test_disconnect_interrupts_lease_and_waiting_assignment(tmp_path) -> None:
    database, project_id, task_id = seeded_database(tmp_path)
    leases = JobLeaseManager(database, lease_seconds=30)
    registry = WorkerRegistry(
        database,
        EventBus(database),
        CapabilityScheduler(),
        heartbeat_timeout_seconds=20,
        leases=leases,
        assignment_timeout_seconds=10,
    )
    socket = FakeSocket()
    await registry.register(registration("worker-a"), socket)
    job = make_job("job-disconnect", project_id, task_id)
    leases.queue(job, attempt=0)
    decision = await registry.reserve_worker(job)
    assert decision is not None
    with database.session() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        task.status = TaskState.ASSIGNED.value
        task.worker_id = decision.worker_id
        task.job_id = job.job_id

    assignment = asyncio.create_task(registry.assign(decision.worker_id, job))
    await asyncio.sleep(0)
    await registry.unregister(decision.worker_id, socket)

    with pytest.raises(WorkerDisconnectedError):
        await assignment
    assert leases.get(job.job_id).status == JobState.INTERRUPTED.value
    with database.session() as session:
        task = session.get(TaskRecord, task_id)
        assert task is not None
        assert task.status == TaskState.RETRYING.value
    database.close()


@pytest.mark.asyncio
async def test_controller_timeout_closes_the_current_worker_socket(tmp_path) -> None:
    database, _, _ = seeded_database(tmp_path)
    registry = WorkerRegistry(
        database,
        EventBus(database),
        CapabilityScheduler(),
        heartbeat_timeout_seconds=20,
        leases=JobLeaseManager(database, lease_seconds=30),
        assignment_timeout_seconds=10,
    )
    socket = FakeSocket()
    await registry.register(registration("worker-timeout"), socket)

    await registry._disconnect_worker("worker-timeout", None, "worker-timeout heartbeat timed out")

    assert socket.closed is True
    assert socket.close_code == 1011
    assert socket.close_reason == "worker-timeout heartbeat timed out"
    assert registry.get("worker-timeout").status == "offline"
    database.close()


@pytest.mark.asyncio
async def test_v2_worker_result_digest_is_verified_before_completion(tmp_path) -> None:
    database, project_id, task_id = seeded_database(tmp_path)
    leases = JobLeaseManager(database, lease_seconds=30)
    registry = WorkerRegistry(
        database,
        EventBus(database),
        CapabilityScheduler(),
        heartbeat_timeout_seconds=20,
        leases=leases,
        assignment_timeout_seconds=10,
    )
    socket = FakeSocket()
    await registry.register(resumable_registration("worker-v2"), socket)
    job = make_job("job-integrity", project_id, task_id)
    leases.queue(job, attempt=0)
    decision = await registry.reserve_worker(job)
    assert decision is not None

    assignment = asyncio.create_task(registry.assign(decision.worker_id, job))
    await asyncio.sleep(0)
    offer = socket.messages[-1]
    identity = {
        "job_id": job.job_id,
        "attempt_id": offer["attempt_id"],
        "lease_token": offer["lease_token"],
    }
    await registry.handle_job_message(
        decision.worker_id,
        {"type": "job.ack", **identity, "sequence": 1},
    )
    await registry.handle_job_message(
        decision.worker_id,
        {"type": "job.started", **identity, "sequence": 2},
    )
    await registry.handle_job_message(
        decision.worker_id,
        {
            "type": "job.completed",
            **identity,
            "sequence": 3,
            "result_hash": "0" * 64,
            "result": {"files": {"app.py": "VALUE = 2\n"}},
            "artifacts": [],
        },
    )

    result = await assignment
    assert result.type == "job.failed"
    assert "RESULT_INTEGRITY_FAILURE" in (result.error or "")
    assert leases.get(job.job_id).status == JobState.FAILED.value
    result_ack = socket.messages[-1]
    assert result_ack["type"] == "job.result.ack"
    assert result_ack["accepted"] is False
    database.close()
