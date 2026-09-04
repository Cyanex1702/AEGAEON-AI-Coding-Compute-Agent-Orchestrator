from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import os
import random
import socket
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from aegaeon.protocol.schemas import JobAssign
from worker.executor import WorkerJobExecutor
from worker.hardware import detect_capabilities, detect_hardware, utilization
from worker.model_runtime import LocalModelRuntime


class WorkerClient:
    """Resumable single-slot worker with fenced attempts and a durable result outbox."""

    def __init__(
        self,
        controller: str,
        token: str,
        worker_id: str | None = None,
        model_runtime: LocalModelRuntime | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.controller = controller
        self.worker_token = token
        self.worker_id = worker_id or f"worker-{socket.gethostname().lower()}-{uuid4().hex[:5]}"
        self.session_id = uuid4().hex
        self.hostname = socket.gethostname()
        self.model_runtime = model_runtime
        self.executor = WorkerJobExecutor(model_runtime)
        self.busy = False
        self.current_job_id: str | None = None
        self.current_attempt_id: str | None = None
        self.stage = "ready"
        self.progress_percent = 0.0
        self.progress_sequence = 0
        self.connection_generation = 0
        self._websocket: ClientConnection | None = None
        self._send_lock = asyncio.Lock()
        self._active_job: JobAssign | None = None
        self._active_task: asyncio.Task[None] | None = None
        configured_state = os.getenv("AEGAEON_WORKER_STATE_DIR", "").strip()
        self.state_dir = state_dir or Path(configured_state or ".aegaeon-worker")
        self.outbox_path = self.state_dir / f"{self.worker_id}-outbox.json"
        self._outbox = self._load_outbox()
        self._hardware: dict[str, Any] | None = None

    async def run_forever(self) -> None:
        delay = 1.0
        while True:
            try:
                async with connect(
                    self.controller,
                    max_size=4_000_000,
                    ping_interval=30,
                    ping_timeout=180,
                    close_timeout=10,
                    additional_headers={"X-AEGAEON-Worker-Token": self.worker_token},
                ) as websocket:
                    self._websocket = websocket
                    await self._session(websocket)
                    delay = 1.0
            except ConnectionClosed as exc:
                if exc.code == 4009:
                    print("worker session superseded; stopping duplicate client", flush=True)
                    return
                if exc.code == 1008:
                    print(
                        "worker credential rejected; update the token before reconnecting",
                        flush=True,
                    )
                    return
                wait = random.uniform(0.5, delay)
                print(
                    f"worker connection closed ({exc.code}); reconnecting in {wait:.1f}s",
                    flush=True,
                )
                await asyncio.sleep(wait)
                delay = min(30.0, delay * 2)
            except (OSError, ConnectionError, ValueError, ValidationError) as exc:
                wait = random.uniform(0.5, delay)
                print(f"worker connection lost: {exc}; reconnecting in {wait:.1f}s", flush=True)
                await asyncio.sleep(wait)
                delay = min(30.0, delay * 2)
            finally:
                self._websocket = None

    async def _session(self, websocket: ClientConnection) -> None:
        hardware = self._hardware or detect_hardware()
        self._hardware = hardware
        capabilities = detect_capabilities()
        models: list[dict[str, Any]] = [
            {"id": "code-7b", "provider": "demo", "loaded": True, "status": "ready"}
        ]
        if self.model_runtime is not None:
            try:
                self.model_runtime.load(hardware)
                capabilities.extend(["model.generate", "model.repair"])
                models = [self.model_runtime.registration().model_dump(mode="json")]
            except Exception as exc:
                detail = str(exc)
                if "out of memory" in detail.lower() and not detail.startswith(
                    "MODEL_LOAD_CUDA_OOM"
                ):
                    detail = f"MODEL_LOAD_CUDA_OOM: {detail}"
                failed = self.model_runtime.registration().model_copy(
                    update={"status": "error", "last_error": detail, "loaded": False}
                )
                models = [failed.model_dump(mode="json")]
        await self._send(
            {
                "type": "worker.register",
                "worker_id": self.worker_id,
                "session_id": self.session_id,
                "protocol_version": 2,
                "connection_generation": self.connection_generation,
                "active_attempts": self._active_attempts(),
                "completed_attempts": self._completed_attempts(),
                "hostname": self.hostname,
                "hardware": hardware,
                "capabilities": sorted(set(capabilities)),
                "models": models,
            }
        )
        heartbeat = asyncio.create_task(self._heartbeat(), name=f"heartbeat:{self.worker_id}")
        try:
            async for raw in websocket:
                data = self._loads(raw)
                message_type = data.get("type")
                if message_type == "worker.registered":
                    self.connection_generation = int(data.get("connection_generation", 0))
                    print(f"registered as {self.worker_id}", flush=True)
                    await self._resend_outbox()
                elif message_type == "job.assign":
                    await self._accept_job(JobAssign.model_validate(data))
                elif message_type == "job.cancel":
                    await self._cancel_job(data)
                elif message_type == "job.result.ack":
                    self._acknowledge_result(data)
                elif message_type == "protocol.error":
                    print(f"controller protocol error: {data.get('message')}", flush=True)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _accept_job(self, job: JobAssign) -> None:
        attempt_id = job.attempt_id or job.job_id
        if self._active_job is not None:
            active_attempt = self._active_job.attempt_id or self._active_job.job_id
            if active_attempt == attempt_id:
                await self._send(self._job_message("job.ack", self._active_job))
                return
            await self._send(
                {
                    "type": "protocol.error",
                    "message": "worker slot is already occupied",
                    "job_id": job.job_id,
                }
            )
            return
        if job.connection_generation != self.connection_generation:
            await self._send(
                {
                    "type": "protocol.error",
                    "message": "job offer uses a stale connection generation",
                    "job_id": job.job_id,
                }
            )
            return
        self._active_job = job
        self.current_job_id = job.job_id
        self.current_attempt_id = attempt_id
        self.busy = True
        self.stage = "assigned"
        self.progress_percent = 0
        self.progress_sequence = 1
        await self._send(self._job_message("job.ack", job))
        self._active_task = asyncio.create_task(
            self._run_job(job), name=f"job:{job.job_id}:{attempt_id}"
        )

    async def _run_job(self, job: JobAssign) -> None:
        async def log(stage: str, message: str, telemetry: dict[str, object]) -> None:
            self.stage = stage
            self.progress_sequence += 1
            progress = telemetry.get("progress_percent")
            if isinstance(progress, (int, float)):
                self.progress_percent = float(progress)
            await self._try_send(
                {
                    **self._job_message("job.log", job),
                    "stage": stage,
                    "message": message,
                    "telemetry": telemetry,
                    "level": "info",
                }
            )

        try:
            await self._try_send(self._job_message("job.started", job))
            result = await self.executor.execute(job, log)
            terminal = {
                **result.model_dump(mode="json"),
                **self._job_identity(job),
                "sequence": self.progress_sequence + 1,
            }
            terminal["result_hash"] = self._result_hash(terminal)
            attempt_id = job.attempt_id or job.job_id
            self._outbox[attempt_id] = terminal
            self._save_outbox()
            await self._try_send(terminal)
        except asyncio.CancelledError:
            raise
        finally:
            self.busy = False
            self.current_job_id = None
            self.current_attempt_id = None
            self.stage = "ready"
            self.progress_percent = 0
            self._active_job = None
            self._active_task = None

    async def _cancel_job(self, data: dict[str, Any]) -> None:
        job = self._active_job
        if job is None or data.get("job_id") != job.job_id:
            return
        if data.get("attempt_id") != job.attempt_id or data.get("lease_token") != job.lease_token:
            return
        task = self._active_task
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._try_send(
            {
                **self._job_message("job.cancelled", job),
                "reason": str(data.get("reason", "cancelled")),
            }
        )

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(5)
            await self._send(
                {
                    "type": "worker.heartbeat",
                    "worker_id": self.worker_id,
                    "status": "busy" if self.busy else "ready",
                    "current_job_id": self.current_job_id,
                    "current_attempt_id": self.current_attempt_id,
                    "connection_generation": self.connection_generation,
                    "progress_sequence": self.progress_sequence,
                    "stage": self.stage,
                    "progress_percent": self.progress_percent,
                    **utilization(),
                }
            )

    async def _resend_outbox(self) -> None:
        for result in list(self._outbox.values()):
            await self._send(result)

    def _acknowledge_result(self, data: dict[str, Any]) -> None:
        attempt_id = str(data.get("attempt_id") or "")
        result = self._outbox.get(attempt_id)
        if result is None:
            return
        if data.get("result_hash") and data.get("result_hash") != result.get("result_hash"):
            return
        self._outbox.pop(attempt_id, None)
        self._save_outbox()

    async def _send(self, data: dict[str, Any]) -> None:
        websocket = self._websocket
        if websocket is None:
            raise ConnectionError("worker is not connected")
        async with self._send_lock:
            await websocket.send(self._json(data))

    async def _try_send(self, data: dict[str, Any]) -> None:
        try:
            await self._send(data)
        except (ConnectionClosed, ConnectionError, OSError):
            pass

    def _job_message(self, message_type: str, job: JobAssign) -> dict[str, Any]:
        self.progress_sequence += 1
        return {
            "type": message_type,
            **self._job_identity(job),
            "sequence": self.progress_sequence,
        }

    @staticmethod
    def _job_identity(job: JobAssign) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "project_id": job.project_id,
            "task_id": job.task_id,
            "run_id": job.run_id,
            "attempt_id": job.attempt_id,
            "lease_token": job.lease_token,
        }

    def _active_attempts(self) -> list[dict[str, Any]]:
        if self._active_job is None:
            return []
        return [
            {
                **self._job_identity(self._active_job),
                "stage": self.stage,
                "progress_sequence": self.progress_sequence,
            }
        ]

    def _completed_attempts(self) -> list[dict[str, Any]]:
        return [
            {
                "job_id": item.get("job_id"),
                "attempt_id": item.get("attempt_id"),
                "result_hash": item.get("result_hash"),
            }
            for item in self._outbox.values()
        ]

    def _load_outbox(self) -> dict[str, dict[str, Any]]:
        try:
            loaded = json.loads(self.outbox_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except (FileNotFoundError, OSError, ValueError):
            return {}

    def _save_outbox(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.outbox_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._outbox, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        temporary.replace(self.outbox_path)

    @staticmethod
    def _result_hash(data: dict[str, Any]) -> str:
        canonical = dict(data)
        canonical.pop("result_hash", None)
        encoded = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _json(data: dict[str, Any]) -> str:
        return json.dumps(data)

    @staticmethod
    def _loads(data: str | bytes) -> dict[str, Any]:
        if isinstance(data, bytes):
            data = data.decode()
        loaded = json.loads(data)
        if not isinstance(loaded, dict):
            raise ValueError("worker protocol messages must be JSON objects")
        return loaded
