from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import WebSocket
from pydantic import ValidationError
from sqlalchemy import select

from aegaeon.database.models import WorkerRecord
from aegaeon.database.session import Database
from aegaeon.events import EventBus
from aegaeon.failures import FailureCode, normalize_failure_code
from aegaeon.jobs.leases import JobLeaseManager
from aegaeon.protocol.schemas import (
    JobAssign,
    JobLog,
    JobResult,
    JobState,
    WorkerFailureDiagnostics,
    WorkerHeartbeat,
    WorkerRead,
    WorkerRegister,
)
from aegaeon.workers.scheduler import (
    CapabilityScheduler,
    ScheduleAssessment,
    ScheduleDecision,
)

WORKER_SUPERSEDED_CLOSE_CODE = 4009


class WorkerDisconnectedError(RuntimeError):
    pass


class WorkerSessionConflict(RuntimeError):
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
        self._sessions: dict[str, str] = {}
        self._protocol_versions: dict[str, int] = {}
        self._job_futures: dict[str, asyncio.Future[JobResult]] = {}
        self._ack_futures: dict[str, asyncio.Future[None]] = {}
        self._job_workers: dict[str, str] = {}
        self._reserved_workers: set[str] = set()
        self._socket_lock = asyncio.Lock()
        self._dispatch_lock = asyncio.Lock()
        self._capacity_changed = asyncio.Condition()

    async def register(self, message: WorkerRegister, socket: WebSocket) -> WorkerRead:
        now = datetime.now(UTC)
        async with self._socket_lock:
            old_socket = self._sockets.get(message.worker_id)
            old_session = self._sessions.get(message.worker_id, "")
            if (
                old_socket
                and old_socket is not socket
                and (not message.session_id or not old_session)
            ):
                raise WorkerSessionConflict("A legacy session for this worker is already active")
            self._sockets[message.worker_id] = socket
            self._sessions[message.worker_id] = message.session_id
            self._protocol_versions[message.worker_id] = message.protocol_version
        with self.database.session() as session:
            worker = session.get(WorkerRecord, message.worker_id)
            if worker is None:
                worker = WorkerRecord(id=message.worker_id, hostname=message.hostname)
                session.add(worker)
            generation = int(worker.connection_generation or 0) + 1
            worker.hostname = message.hostname
            worker.status = "online"
            worker.hardware = message.hardware.model_dump()
            worker.capabilities = message.capabilities
            worker.models = [model.model_dump() for model in message.models]
            worker.connected_at = now
            worker.last_heartbeat = now
            worker.telemetry_at = now
            worker.session_id = message.session_id
            worker.connection_generation = generation
            active = self.leases.active_for_worker(message.worker_id)
            current = active[0] if active else None
            worker.current_task = current.task_id if current else None
            worker.current_job_id = current.id if current else None
            worker.stage = "recovering" if current else "online"
            worker.progress_percent = worker.progress_percent if current else 0
            if current:
                self._reserved_workers.add(message.worker_id)
        if old_socket and old_socket is not socket:
            await old_socket.close(
                code=WORKER_SUPERSEDED_CLOSE_CODE,
                reason="Worker session superseded",
            )
        await self.events.publish(
            "worker.connected", f"{message.hostname} connected", worker_id=message.worker_id
        )
        await self._notify_capacity()
        return self.get(message.worker_id)

    def is_current(self, worker_id: str, socket: WebSocket) -> bool:
        return self._sockets.get(worker_id) is socket

    async def heartbeat(self, message: WorkerHeartbeat, socket: WebSocket | None = None) -> None:
        if socket is not None and not self.is_current(message.worker_id, socket):
            return
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
            hardware = dict(worker.hardware or {})
            memory = dict(hardware.get("memory") or {})
            memory.update(
                {
                    "ram_available_mb": message.ram_available_mb,
                    "vram_allocated_mb": message.vram_allocated_mb or message.vram_used_mb,
                    "vram_reserved_mb": message.vram_reserved_mb,
                    "vram_free_mb": message.vram_free_mb,
                }
            )
            hardware["memory"] = memory
            gpu = dict(hardware.get("gpu") or {})
            gpu.update(
                {
                    "allocated_mb": memory["vram_allocated_mb"],
                    "reserved_mb": memory["vram_reserved_mb"],
                    "free_mb": memory["vram_free_mb"],
                    "utilization_percent": message.gpu_percent,
                }
            )
            hardware["gpu"] = gpu
            gpus = [dict(item) for item in hardware.get("gpus") or []]
            if gpus:
                gpus[0].update(gpu)
                hardware["gpus"] = gpus
            hardware["ram_available_mb"] = message.ram_available_mb
            worker.hardware = hardware
            worker.current_job_id = message.current_job_id
            worker.stage = message.stage or worker.stage
            worker.progress_percent = message.progress_percent
            worker.progress_sequence = max(worker.progress_sequence, message.progress_sequence)
            worker.last_heartbeat = datetime.now(UTC)
            worker.telemetry_at = worker.last_heartbeat
        if message.current_job_id:
            self.leases.extend_job(
                message.current_job_id,
                message.worker_id,
                message.current_attempt_id,
                progress_sequence=message.progress_sequence,
            )
        await self._notify_capacity()

    async def unregister(self, worker_id: str, socket: WebSocket) -> None:
        await self._disconnect_worker(worker_id, socket, f"{worker_id} disconnected")

    async def revoke(self, worker_id: str) -> bool:
        socket = self._sockets.get(worker_id)
        try:
            self.get(worker_id)
            known = True
        except KeyError:
            known = False
        if socket is not None:
            await socket.close(code=1008, reason="Worker credential revoked")
        if known or socket is not None:
            await self._disconnect_worker(worker_id, socket, f"{worker_id} credential revoked")
            return True
        return False

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

    def _schedulable_workers(self) -> list[dict[str, Any]]:
        connected = set(self._sockets)
        result: list[dict[str, Any]] = []
        for item in self.list():
            worker = item.model_dump()
            if item.id in self._reserved_workers:
                worker["status"] = "busy"
            elif item.id in connected and worker.get("status") == "offline":
                worker["status"] = "online"
            elif item.id not in connected:
                worker["status"] = "offline"
            result.append(worker)
        return result

    def assess_worker(self, job: JobAssign) -> ScheduleAssessment:
        return self.scheduler.assess(self._schedulable_workers(), job.requirements)

    def select_worker(self, job: JobAssign) -> ScheduleDecision | None:
        return self.assess_worker(job).decision

    async def reserve_or_assess(self, job: JobAssign) -> ScheduleAssessment:
        async with self._dispatch_lock:
            assessment = self.assess_worker(job)
            decision = assessment.decision
            if decision is None:
                return assessment
            self._reserved_workers.add(decision.worker_id)
            worker = self.get(decision.worker_id)
            if self._protocol_versions.get(decision.worker_id, 1) >= 2:
                self.leases.assign(
                    job.job_id,
                    decision.worker_id,
                    lease_token=uuid4().hex,
                    connection_generation=worker.connection_generation,
                    offer_timeout_seconds=self.assignment_timeout_seconds,
                )
            else:
                self.leases.assign(job.job_id, decision.worker_id)
            with self.database.session() as session:
                worker = session.get(WorkerRecord, decision.worker_id)
                if worker:
                    worker.status = "busy"
                    worker.current_task = job.task_id
                    worker.current_job_id = job.job_id
                    worker.stage = "assigned"
                    worker.progress_percent = 0
            return assessment

    async def reserve_worker(self, job: JobAssign) -> ScheduleDecision | None:
        return (await self.reserve_or_assess(job)).decision

    async def wait_for_capacity_change(self, timeout_seconds: float = 1.0) -> None:
        async with self._capacity_changed:
            try:
                await asyncio.wait_for(self._capacity_changed.wait(), timeout_seconds)
            except TimeoutError:
                pass

    async def _notify_capacity(self) -> None:
        async with self._capacity_changed:
            self._capacity_changed.notify_all()

    async def assign(self, worker_id: str, job: JobAssign) -> JobResult:
        socket = self._sockets.get(worker_id)
        if socket is None:
            self.leases.interrupt(job.job_id, f"worker {worker_id} is not connected")
            self._reserved_workers.discard(worker_id)
            raise WorkerDisconnectedError(f"worker {worker_id} is not connected")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[JobResult] = loop.create_future()
        ack_future: asyncio.Future[None] = loop.create_future()
        self._job_futures[job.job_id] = future
        self._ack_futures[job.job_id] = ack_future
        self._job_workers[job.job_id] = worker_id
        try:
            lease = self.leases.get(job.job_id)
            fenced_job = job.model_copy(
                update={
                    "attempt_id": lease.attempt_id,
                    "lease_token": lease.lease_token,
                    "connection_generation": lease.connection_generation,
                    "assignment_expires_at": lease.offer_expires_at,
                }
            )
            await socket.send_json(fenced_job.model_dump(mode="json"))
            if self._protocol_versions.get(worker_id, 1) >= 2:
                try:
                    await asyncio.wait_for(
                        asyncio.shield(ack_future),
                        timeout=self.assignment_timeout_seconds,
                    )
                except TimeoutError as exc:
                    self.leases.interrupt(job.job_id, "worker did not acknowledge job offer")
                    raise WorkerDisconnectedError(
                        f"worker {worker_id} did not acknowledge job {job.job_id}"
                    ) from exc
            return await future
        except asyncio.CancelledError:
            raise
        except WorkerDisconnectedError:
            raise
        except Exception as exc:
            self.leases.interrupt(job.job_id, str(exc))
            raise
        finally:
            self._job_futures.pop(job.job_id, None)
            self._ack_futures.pop(job.job_id, None)
            self._job_workers.pop(job.job_id, None)
            self._reserved_workers.discard(worker_id)
            with self.database.session() as session:
                worker = session.get(WorkerRecord, worker_id)
                if worker:
                    worker.current_task = None
                    worker.current_job_id = None
                    worker.stage = "online" if worker_id in self._sockets else "offline"
                    worker.progress_percent = 0
                    worker.status = "online" if worker_id in self._sockets else "offline"
            await self._notify_capacity()

    async def handle_job_message(self, worker_id: str, data: dict[str, Any]) -> None:
        event_type = data.get("type", "")
        job_id = str(data.get("job_id", ""))
        if not job_id:
            raise PermissionError("worker message is missing job_id")
        lease = self.leases.get(job_id)
        if lease.worker_id != worker_id:
            raise PermissionError("worker result does not match durable lease ownership")
        protocol_version = self._protocol_versions.get(worker_id, 1)
        attempt_id = data.get("attempt_id")
        lease_token = str(data.get("lease_token", ""))
        sequence = int(data.get("sequence", 1))
        if protocol_version >= 2 and lease.attempt_id and attempt_id != lease.attempt_id:
            raise PermissionError("worker result has a stale attempt id")
        if protocol_version >= 2 and lease.lease_token and lease_token != lease.lease_token:
            raise PermissionError("worker result has a stale lease token")
        if event_type == "job.ack":
            self.leases.acknowledge(job_id, worker_id, attempt_id, lease_token, sequence)
            ack = self._ack_futures.get(job_id)
            if ack and not ack.done():
                ack.set_result(None)
            return
        if event_type == "job.started":
            if protocol_version >= 2:
                self.leases.start(job_id, worker_id, attempt_id, lease_token, sequence)
            else:
                self.leases.start(job_id)
            with self.database.session() as session:
                worker = session.get(WorkerRecord, worker_id)
                if worker:
                    worker.stage = "running"
            return
        if event_type == "job.log":
            message = JobLog.model_validate(data)
            entry = message.model_dump(mode="json", exclude={"type"})
            self.leases.append_log(job_id, entry)
            with self.database.session() as session:
                worker = session.get(WorkerRecord, worker_id)
                if worker:
                    worker.stage = message.stage
                    progress = message.telemetry.get("progress_percent")
                    if isinstance(progress, (int, float)):
                        worker.progress_percent = max(0, min(100, float(progress)))
            return
        if event_type not in {"job.completed", "job.failed"}:
            return
        if protocol_version >= 2:
            supplied_hash = str(data.get("result_hash") or "")
            canonical = dict(data)
            canonical.pop("result_hash", None)
            computed_hash = hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if not supplied_hash or not hmac.compare_digest(supplied_hash, computed_hash):
                diagnostics = WorkerFailureDiagnostics(
                    error_type="ResultIntegrityError",
                    message="worker terminal payload digest did not match its contents",
                    error_repr="ResultIntegrityError('terminal payload digest mismatch')",
                    stage="CONTROLLER_RECEIVE",
                    classification=FailureCode.RESULT_INTEGRITY_FAILURE.value,
                    retry_strategy="REASSIGN_WORKER",
                    recent_stage_history=lease.logs[-20:],
                )
                failed = JobResult(
                    type="job.failed",
                    job_id=job_id,
                    attempt_id=attempt_id,
                    lease_token=lease_token,
                    sequence=sequence,
                    error="RESULT_INTEGRITY_FAILURE: controller rejected worker result",
                    diagnostics=diagnostics,
                )
                self.leases.fail(
                    job_id,
                    failed.error or FailureCode.RESULT_INTEGRITY_FAILURE.value,
                    diagnostics=diagnostics.model_dump(mode="json"),
                )
                current_socket = self._sockets.get(worker_id)
                if current_socket is not None:
                    await current_socket.send_json(
                        {
                            "type": "job.result.ack",
                            "job_id": job_id,
                            "attempt_id": attempt_id,
                            "result_hash": supplied_hash or None,
                            "accepted": False,
                        }
                    )
                future = self._job_futures.get(job_id)
                if future and not future.done():
                    future.set_result(failed)
                return
        try:
            result = JobResult.model_validate(data)
        except ValidationError as exc:
            message = str(exc)
            classification = normalize_failure_code("", message)
            if "INVALID_ARTIFACT_FILENAME" in message:
                classification = FailureCode.INVALID_ARTIFACT_FILENAME
            diagnostics = WorkerFailureDiagnostics(
                error_type="ValidationError",
                message=message,
                error_repr=repr(exc),
                stage="CONTROLLER_RECEIVE",
                classification=classification.value,
                retry_strategy="DO_NOT_REGENERATE",
                recent_stage_history=lease.logs[-20:],
            )
            failed = JobResult(
                type="job.failed",
                job_id=job_id,
                error=f"{classification.value}: controller rejected worker result",
                diagnostics=diagnostics,
            )
            self.leases.fail(
                job_id,
                failed.error or classification.value,
                diagnostics=diagnostics.model_dump(mode="json"),
            )
            future = self._job_futures.get(job_id)
            if future and not future.done():
                future.set_result(failed)
            return
        if event_type == "job.completed":
            self.leases.complete(job_id, result.result, result.result_hash)
        else:
            self.leases.fail(
                job_id,
                result.error or "worker job failed",
                result.result,
                result.diagnostics.model_dump(mode="json") if result.diagnostics else None,
            )
        current_socket = self._sockets.get(worker_id)
        if current_socket is not None and self._protocol_versions.get(worker_id, 1) >= 2:
            await current_socket.send_json(
                {
                    "type": "job.result.ack",
                    "job_id": job_id,
                    "attempt_id": attempt_id,
                    "result_hash": result.result_hash,
                }
            )
        future = self._job_futures.get(job_id)
        if future and not future.done():
            future.set_result(result)

    async def cancel_project(self, project_id: str) -> None:
        for job in self.leases.list(project_id, include_history=True):
            if job.status not in {
                JobState.OFFERED.value,
                JobState.ACKED.value,
                JobState.ASSIGNED.value,
                JobState.RUNNING.value,
            }:
                continue
            self.leases.request_cancel(job.id)
            socket = self._sockets.get(job.worker_id or "")
            if socket is not None:
                await socket.send_json(
                    {
                        "type": "job.cancel",
                        "job_id": job.id,
                        "attempt_id": job.attempt_id,
                        "lease_token": job.lease_token,
                        "reason": "project cancelled",
                    }
                )

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
        protocol_version = self._protocol_versions.get(worker_id, 1)
        async with self._socket_lock:
            current = self._sockets.get(worker_id)
            if socket is not None and current is not socket:
                return
            self._sockets.pop(worker_id, None)
            self._sessions.pop(worker_id, None)
            self._protocol_versions.pop(worker_id, None)
        if socket is None and current is not None:
            try:
                await current.close(code=1011, reason=reason[:120])
            except RuntimeError:
                pass
        if protocol_version >= 2:
            active = self.leases.active_for_worker(worker_id)
            job_ids = [job.id for job in active]
        else:
            job_ids = self.leases.interrupt_worker(worker_id, reason)
            self._interrupt_futures(worker_id, job_ids, reason)
        with self.database.session() as session:
            worker = session.get(WorkerRecord, worker_id)
            if worker:
                worker.status = "offline"
                worker.current_task = None
        await self._notify_capacity()
        await self.events.publish(
            "worker.disconnected",
            reason,
            worker_id=worker_id,
            payload={
                "interrupted_jobs": job_ids if protocol_version < 2 else [],
                "resumable_jobs": job_ids if protocol_version >= 2 else [],
            },
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
