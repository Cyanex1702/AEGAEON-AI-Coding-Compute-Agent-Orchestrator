from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import WebSocket
from sqlalchemy import select

from aegaeon.database.models import WorkerRecord
from aegaeon.database.session import Database
from aegaeon.events import EventBus
from aegaeon.jobs.leases import JobLeaseManager
from aegaeon.protocol.schemas import (
    JobAssign,
    JobResult,
    JobState,
    WorkerHeartbeat,
    WorkerRead,
    WorkerRegister,
)
from aegaeon.workers.scheduler import CapabilityScheduler, ScheduleDecision


class WorkerDisconnectedError(RuntimeError):
    pass


class WorkerRegistry:
    """Owns live worker connections, reservations, and durable job leases."""

    def __init__(
        self,
        database: Database,
        events: EventBus,
        scheduler: CapabilityScheduler,
        heartbeat_timeout_seconds: int,
        leases: JobLeaseManager,
        assignment_timeout_seconds: int = 180,
    ) -> None:
        self.database = database
        self.events = events
        self.scheduler = scheduler
        self.heartbeat_timeout_seconds = heartbeat_timeout_seconds
        self.assignment_timeout_seconds = assignment_timeout_seconds
        self.leases = leases
        self._sockets: dict[str, WebSocket] = {}
        self._job_futures: dict[str, asyncio.Future[JobResult]] = {}
        self._job_workers: dict[str, str] = {}
        self._reserved_workers: set[str] = set()
        self._socket_lock = asyncio.Lock()
        self._dispatch_lock = asyncio.Lock()

    async def register(self, message: WorkerRegister, socket: WebSocket) -> WorkerRead:
        now = datetime.now(UTC)
        with self.database.session() as session:
            worker = session.get(WorkerRecord, message.worker_id)
            if worker is None:
                worker = WorkerRecord(id=message.worker_id, hostname=message.hostname)
                session.add(worker)
            worker.hostname = message.hostname
            worker.status = "online"
            worker.hardware = message.hardware.model_dump()
            worker.capabilities = message.capabilities
            worker.models = [model.model_dump() for model in message.models]
            worker.connected_at = now
            worker.last_heartbeat = now
            worker.current_task = None
        async with self._socket_lock:
            old_socket = self._sockets.get(message.worker_id)
            self._sockets[message.worker_id] = socket
        if old_socket and old_socket is not socket:
            await old_socket.close(code=1012, reason="Worker reconnected")
        await self.events.publish(
            "worker.connected", f"{message.hostname} connected", worker_id=message.worker_id
        )
        return self.get(message.worker_id)

    async def heartbeat(self, message: WorkerHeartbeat) -> None:
        with self.database.session() as session:
            worker = session.get(WorkerRecord, message.worker_id)
            if not worker:
                return
            reserved = message.worker_id in self._reserved_workers
            if reserved or message.status == "busy":
                worker.status = "busy"
            elif message.status in {"idle", "ready"}:
                worker.status = "online"
            else:
                worker.status = message.status
            worker.cpu_percent = message.cpu_percent
            worker.ram_percent = message.ram_percent
            worker.gpu_percent = message.gpu_percent
            worker.vram_used_mb = message.vram_used_mb
            worker.last_heartbeat = datetime.now(UTC)
        self.leases.extend_worker(message.worker_id)

    async def unregister(self, worker_id: str, socket: WebSocket) -> None:
        await self._disconnect_worker(worker_id, socket, f"{worker_id} disconnected")

    async def revoke(self, worker_id: str) -> None:
        socket = self._sockets.get(worker_id)
        if socket is not None:
            await socket.close(code=1008, reason="Worker credential revoked")
        await self._disconnect_worker(worker_id, socket, f"{worker_id} credential revoked")

    def list(self) -> list[WorkerRead]:
        with self.database.session() as session:
            items = session.scalars(
                select(WorkerRecord).order_by(WorkerRecord.last_heartbeat.desc())
            ).all()
            return [WorkerRead.model_validate(item) for item in items]

    def get(self, worker_id: str) -> WorkerRead:
        with self.database.session() as session:
            worker = session.get(WorkerRecord, worker_id)
            if not worker:
                raise KeyError(worker_id)
            return WorkerRead.model_validate(worker)

    def select_worker(self, job: JobAssign) -> ScheduleDecision | None:
        connected = set(self._sockets) - self._reserved_workers
        worker_data = [item.model_dump() for item in self.list() if item.id in connected]
        return self.scheduler.select(worker_data, job.requirements)

    async def reserve_worker(self, job: JobAssign) -> ScheduleDecision | None:
        async with self._dispatch_lock:
            decision = self.select_worker(job)
            if decision is None:
                return None
            self._reserved_workers.add(decision.worker_id)
            self.leases.assign(job.job_id, decision.worker_id)
            with self.database.session() as session:
                worker = session.get(WorkerRecord, decision.worker_id)
                if worker:
                    worker.status = "busy"
                    worker.current_task = job.task_id
            return decision

    async def assign(self, worker_id: str, job: JobAssign) -> JobResult:
        socket = self._sockets.get(worker_id)
        if socket is None:
            self.leases.interrupt(job.job_id, f"worker {worker_id} is not connected")
            self._reserved_workers.discard(worker_id)
            raise WorkerDisconnectedError(f"worker {worker_id} is not connected")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[JobResult] = loop.create_future()
        self._job_futures[job.job_id] = future
        self._job_workers[job.job_id] = worker_id
        try:
            await socket.send_json(job.model_dump(mode="json"))
            return await asyncio.wait_for(future, timeout=self.assignment_timeout_seconds)
        except TimeoutError as exc:
            reason = f"job {job.job_id} timed out on worker {worker_id}"
            self.leases.interrupt(job.job_id, reason)
            raise WorkerDisconnectedError(reason) from exc
        except asyncio.CancelledError:
            raise
        except WorkerDisconnectedError:
            raise
        except Exception as exc:
            self.leases.interrupt(job.job_id, str(exc))
            raise
        finally:
            self._job_futures.pop(job.job_id, None)
            self._job_workers.pop(job.job_id, None)
            self._reserved_workers.discard(worker_id)
            with self.database.session() as session:
                worker = session.get(WorkerRecord, worker_id)
                if worker:
                    worker.current_task = None
                    worker.status = "online" if worker_id in self._sockets else "offline"

    async def handle_job_message(self, worker_id: str, data: dict[str, Any]) -> None:
        event_type = data.get("type", "")
        job_id = str(data.get("job_id", ""))
        assigned_worker = self._job_workers.get(job_id)
        if not job_id or assigned_worker != worker_id:
            raise PermissionError("worker does not own this active job")
        lease = self.leases.get(job_id)
        if lease.worker_id != worker_id:
            raise PermissionError("worker result does not match durable lease ownership")
        if event_type == "job.started":
            if lease.status != JobState.ASSIGNED.value:
                raise RuntimeError("stale or duplicate job.started message rejected")
            self.leases.start(job_id)
            return
        if event_type not in {"job.completed", "job.failed"}:
            return
        if lease.status not in {JobState.ASSIGNED.value, JobState.RUNNING.value}:
            raise RuntimeError("stale or duplicate job result rejected")
        future = self._job_futures.get(job_id)
        if future is None or future.done():
            raise RuntimeError("job is no longer awaiting a result")
        result = JobResult.model_validate(data)
        if event_type == "job.completed":
            self.leases.complete(job_id, result.result)
        else:
            self.leases.fail(job_id, result.error or "worker job failed", result.result)
        future.set_result(result)

    async def monitor(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(min(5, self.heartbeat_timeout_seconds / 2))
            cutoff = datetime.now(UTC) - timedelta(seconds=self.heartbeat_timeout_seconds)
            timed_out: list[str] = []
            with self.database.session() as session:
                workers = session.scalars(
                    select(WorkerRecord).where(WorkerRecord.status != "offline")
                ).all()
                for worker in workers:
                    heartbeat = worker.last_heartbeat
                    if heartbeat.tzinfo is None:
                        heartbeat = heartbeat.replace(tzinfo=UTC)
                    if heartbeat < cutoff:
                        timed_out.append(worker.id)
            for worker_id in timed_out:
                await self._disconnect_worker(
                    worker_id,
                    None,
                    f"{worker_id} heartbeat timed out; assigned work will be retried",
                )

            expired = self.leases.expire_leases()
            for worker_id, job_ids in expired.items():
                self._interrupt_futures(
                    worker_id, job_ids, f"job lease expired on worker {worker_id}"
                )
                if worker_id in self._sockets:
                    await self._disconnect_worker(
                        worker_id, None, f"{worker_id} lost an active job lease"
                    )

    async def _disconnect_worker(
        self,
        worker_id: str,
        socket: WebSocket | None,
        reason: str,
    ) -> None:
        async with self._socket_lock:
            current = self._sockets.get(worker_id)
            if socket is not None and current is not socket:
                return
            self._sockets.pop(worker_id, None)
        self._reserved_workers.discard(worker_id)
        job_ids = self.leases.interrupt_worker(worker_id, reason)
        self._interrupt_futures(worker_id, job_ids, reason)
        with self.database.session() as session:
            worker = session.get(WorkerRecord, worker_id)
            if worker:
                worker.status = "offline"
                worker.current_task = None
        await self.events.publish(
            "worker.disconnected",
            reason,
            worker_id=worker_id,
            payload={"interrupted_jobs": job_ids},
        )

    def _interrupt_futures(self, worker_id: str, job_ids: list[str], reason: str) -> None:
        selected = set(job_ids) | {
            job_id
            for job_id, assigned_worker in self._job_workers.items()
            if assigned_worker == worker_id
        }
        for job_id in selected:
            future = self._job_futures.get(job_id)
            if future and not future.done():
                future.set_exception(WorkerDisconnectedError(reason))
