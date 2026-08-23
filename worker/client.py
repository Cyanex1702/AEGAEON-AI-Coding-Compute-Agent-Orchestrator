from __future__ import annotations

import asyncio
import contextlib
import socket
from typing import Any
from uuid import uuid4

from websockets.asyncio.client import ClientConnection, connect

from aegaeon.protocol.schemas import JobAssign
from worker.executor import WorkerJobExecutor
from worker.hardware import detect_capabilities, detect_hardware, utilization
from worker.model_runtime import LocalModelRuntime


class WorkerClient:
    def __init__(
        self,
        controller: str,
        token: str,
        worker_id: str | None = None,
        model_runtime: LocalModelRuntime | None = None,
    ) -> None:
        self.controller = controller
        self.worker_token = token
        self.worker_id = worker_id or f"worker-{socket.gethostname().lower()}-{uuid4().hex[:5]}"
        self.hostname = socket.gethostname()
        self.model_runtime = model_runtime
        self.executor = WorkerJobExecutor(model_runtime)
        self.busy = False

    async def run_forever(self) -> None:
        delay = 1
        while True:
            try:
                async with connect(
                    self.controller,
                    max_size=4_000_000,
                    additional_headers={"X-AEGAEON-Worker-Token": self.worker_token},
                ) as websocket:
                    delay = 1
                    await self._session(websocket)
            except (OSError, ConnectionError) as exc:
                print(f"worker connection lost: {exc}; reconnecting in {delay}s", flush=True)
                await asyncio.sleep(delay)
                delay = min(15, delay * 2)

    async def _session(self, websocket: ClientConnection) -> None:
        hardware = detect_hardware()
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
                failed = self.model_runtime.registration().model_copy(
                    update={"status": "error", "last_error": str(exc), "loaded": False}
                )
                models = [failed.model_dump(mode="json")]
        await websocket.send(
            self._json(
                {
                    "type": "worker.register",
                    "worker_id": self.worker_id,
                    "hostname": self.hostname,
                    "hardware": hardware,
                    "capabilities": sorted(set(capabilities)),
                    "models": models,
                }
            )
        )
        heartbeat = asyncio.create_task(self._heartbeat(websocket))
        try:
            async for raw in websocket:
                data = self._loads(raw)
                if data.get("type") == "worker.registered":
                    print(f"registered as {self.worker_id}", flush=True)
                elif data.get("type") == "job.assign":
                    asyncio.create_task(self._handle_job(websocket, JobAssign.model_validate(data)))
                elif data.get("type") == "protocol.error":
                    print(f"controller protocol error: {data.get('message')}", flush=True)
        finally:
            heartbeat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat

    async def _heartbeat(self, websocket: ClientConnection) -> None:
        while True:
            await asyncio.sleep(5)
            await websocket.send(
                self._json(
                    {
                        "type": "worker.heartbeat",
                        "worker_id": self.worker_id,
                        "status": "busy" if self.busy else "ready",
                        **utilization(),
                    }
                )
            )

    async def _handle_job(self, websocket: ClientConnection, job: JobAssign) -> None:
        self.busy = True
        common = {
            "job_id": job.job_id,
            "project_id": job.project_id,
            "task_id": job.task_id,
        }
        try:
            await websocket.send(self._json({"type": "job.started", **common}))
            await websocket.send(
                self._json(
                    {
                        "type": "job.log",
                        "message": f"Preparing disposable workspace for {job.agent_role} agent",
                        **common,
                    }
                )
            )
            result = await self.executor.execute(job)
            await websocket.send(self._json({**result.model_dump(mode="json"), **common}))
        finally:
            self.busy = False

    @staticmethod
    def _json(data: dict[str, Any]) -> str:
        import json

        return json.dumps(data)

    @staticmethod
    def _loads(data: str | bytes) -> dict[str, Any]:
        import json

        if isinstance(data, bytes):
            data = data.decode()
        return json.loads(data)
