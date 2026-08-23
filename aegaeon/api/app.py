from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from time import monotonic
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from aegaeon.agents.demo import DemoAgentRuntime
from aegaeon.agents.llm import LLMAgentRuntime
from aegaeon.artifacts.manager import ArtifactManager
from aegaeon.config import Settings, get_settings
from aegaeon.database.models import ArtifactRecord
from aegaeon.database.session import Database
from aegaeon.events import EventBus
from aegaeon.execution.runner import SubprocessRunner
from aegaeon.execution.tests import TestCommandDetector
from aegaeon.jobs.leases import JobLeaseManager
from aegaeon.models.openai_compatible import OpenAICompatibleProvider
from aegaeon.models.provider import ModelProvider
from aegaeon.models.registry import ModelRegistry
from aegaeon.models.results import ProviderStatus
from aegaeon.models.scout import HardwareTarget, ModelScout, RecommendationRequest
from aegaeon.orchestrator.orchestrator import Orchestrator
from aegaeon.projects.manager import ProjectManager
from aegaeon.protocol.schemas import (
    ActivityRead,
    ArtifactRead,
    ExecutionMode,
    JobRead,
    ModelRead,
    NotebookGenerateRequest,
    NotebookRead,
    PairingRedeem,
    ProjectCreate,
    ProjectRead,
    TaskRead,
    WorkerCredential,
    WorkerHeartbeat,
    WorkerRead,
    WorkerRegister,
)
from aegaeon.remote import RemoteConnectivityManager
from aegaeon.remote.manager import REMOTE_TARGETS
from aegaeon.remote.schemas import (
    RemoteConnectionRead,
    RemoteTokenRequest,
    TunnelSetupRequest,
)
from aegaeon.research import ManualResearchImport, ResearchResult, ResearchService
from aegaeon.strategy.router import StrategyRouter
from aegaeon.workers.notebooks import ColabNotebookGenerator, NotebookSpec
from aegaeon.workers.pairing import PairingError, WorkerPairingService
from aegaeon.workers.registry import WorkerRegistry
from aegaeon.workers.scheduler import CapabilityScheduler


def create_app(
    settings: Settings | None = None,
    provider: ModelProvider | None = None,
    model_runtime: LLMAgentRuntime | None = None,
) -> FastAPI:
    runtime = settings or get_settings()
    runtime.data_dir.mkdir(parents=True, exist_ok=True)
    database = Database(runtime.database_url)
    events = EventBus(database)
    projects = ProjectManager(database, runtime.projects_dir)
    artifacts = ArtifactManager(database, runtime.projects_dir)
    strategies = StrategyRouter()
    leases = JobLeaseManager(database, runtime.job_lease_seconds)
    workers = WorkerRegistry(
        database,
        events,
        CapabilityScheduler(),
        heartbeat_timeout_seconds=runtime.heartbeat_timeout_seconds,
        leases=leases,
        assignment_timeout_seconds=runtime.job_assignment_timeout_seconds,
    )
    models = ModelRegistry(database)
    scout = ModelScout(database)
    research = ResearchService(database)
    pairing = WorkerPairingService(database, runtime.pairing_code_lifetime_seconds)
    notebooks = ColabNotebookGenerator(artifacts, pairing)
    remote = RemoteConnectivityManager(runtime, database, events)
    runner = SubprocessRunner(
        runtime.projects_dir,
        timeout=runtime.command_timeout_seconds,
        maximum_output=runtime.maximum_output_bytes,
    )
    model_provider = provider or OpenAICompatibleProvider(
        base_url=runtime.llm_base_url,
        model=runtime.llm_model,
        api_key=runtime.llm_api_key,
        timeout=runtime.llm_timeout_seconds,
        max_output_tokens=runtime.llm_max_output_tokens,
        structured_output_mode=runtime.llm_structured_output_mode,
    )
    agents = model_runtime or LLMAgentRuntime(model_provider, runtime.llm_output_retries)
    orchestrator = Orchestrator(
        runtime,
        database,
        projects,
        artifacts,
        workers,
        events,
        strategies,
        runner,
        DemoAgentRuntime(),
        leases,
        agents,
        TestCommandDetector(),
    )
    stop_monitor = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.create_all()
        await remote.reconcile()
        models.seed()
        scout.seed()
        recoverable_projects = leases.recover_orphans()
        orchestrator.recover(recoverable_projects)
        monitor = asyncio.create_task(workers.monitor(stop_monitor), name="worker-heartbeats")
        yield
        stop_monitor.set()
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        await orchestrator.shutdown()
        await remote.shutdown()
        database.close()

    app = FastAPI(
        title="AEGAEON Controller",
        version="0.3.0",
        description="Local-first AI coding and compute orchestration control plane",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.settings = runtime
    app.state.database = database
    app.state.events = events
    app.state.projects = projects
    app.state.artifacts = artifacts
    app.state.workers = workers
    app.state.models = models
    app.state.scout = scout
    app.state.research = research
    app.state.pairing = pairing
    app.state.notebooks = notebooks
    app.state.strategies = strategies
    app.state.orchestrator = orchestrator
    app.state.leases = leases
    app.state.provider = model_provider
    app.state.model_runtime = agents
    app.state.remote = remote
    pairing_attempts: dict[str, deque[float]] = defaultdict(deque)

    @app.middleware("http")
    async def restrict_public_surface(request: Request, call_next: Any) -> Any:
        public_paths = {"/health", "/pairing/redeem"}
        if (
            remote.is_public_request_host(request.headers.get("host"))
            and request.url.path not in public_paths
        ):
            return JSONResponse(
                status_code=403,
                content={"detail": "This route is not exposed to remote workers"},
            )
        return await call_next(request)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "demo_mode": runtime.demo_mode,
            "model_mode": runtime.model_mode,
            "model": model_provider.model_id,
            "version": "0.3.0",
        }

    @app.get("/remote-connectivity", response_model=RemoteConnectionRead)
    async def remote_connectivity_status() -> RemoteConnectionRead:
        return remote.status()

    @app.post("/remote-connectivity/setup", response_model=RemoteConnectionRead)
    async def setup_remote_connectivity(request: TunnelSetupRequest) -> RemoteConnectionRead:
        return await remote.start(request.provider, request.public_url)

    @app.post("/remote-connectivity/restart", response_model=RemoteConnectionRead)
    async def restart_remote_connectivity() -> RemoteConnectionRead:
        return await remote.restart()

    @app.post("/remote-connectivity/stop", response_model=RemoteConnectionRead)
    async def stop_remote_connectivity() -> RemoteConnectionRead:
        return await remote.stop()

    @app.put("/remote-connectivity/providers/{provider}/token")
    async def save_remote_token(provider: str, request: RemoteTokenRequest) -> dict[str, bool]:
        try:
            remote.save_token(provider, request.token)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"configured": True}

    @app.delete("/remote-connectivity/providers/{provider}/token")
    async def remove_remote_token(provider: str) -> dict[str, bool]:
        remote.remove_token(provider)
        return {"configured": False}

    def provider_status(reachable: bool | None = None, error: str | None = None) -> ProviderStatus:
        return ProviderStatus(
            provider=model_provider.provider_name,
            model=model_provider.model_id,
            base_url=runtime.llm_base_url,
            mode=runtime.model_mode,
            configured=bool(runtime.llm_base_url and runtime.llm_model),
            reachable=reachable,
            structured_output_mode=runtime.llm_structured_output_mode,
            error=error,
        )

    @app.get("/provider/status", response_model=ProviderStatus)
    async def get_provider_status() -> ProviderStatus:
        return provider_status()

    @app.post("/provider/check", response_model=ProviderStatus)
    async def check_provider() -> ProviderStatus:
        probe = getattr(model_provider, "probe", None)
        if probe is None:
            return provider_status(False, "This provider does not implement a reachability probe")
        reachable, error = await probe()
        return provider_status(reachable, error)

    @app.get("/integrations")
    async def integrations() -> dict[str, Any]:
        status = provider_status()
        return {
            "rich_boy_provider": status.model_dump(mode="json"),
            "hugging_face": {
                "configured": bool(runtime.hf_token),
                "secret_exposed": False,
            },
            "manual_advisors": {
                "supported": True,
                "connected": False,
                "workflow": "copy_prompt_import_json",
            },
        }

    @app.get("/stats/dashboard")
    async def dashboard_stats() -> dict[str, Any]:
        project_items = projects.list()
        worker_items = workers.list()
        return {
            "projects": len(project_items),
            "active_projects": sum(
                item.status in {"planning", "running", "testing", "reviewing"}
                for item in project_items
            ),
            "online_workers": sum(item.status in {"online", "busy"} for item in worker_items),
            "gpu_workers": sum(
                bool(item.hardware.get("gpu", {}).get("available")) for item in worker_items
            ),
            "running_tasks": sum(
                task.status in {"ASSIGNED", "RUNNING", "RETRYING"}
                for item in project_items
                for task in item.tasks
            ),
        }

    @app.post("/projects", response_model=ProjectRead, status_code=201)
    async def create_project(request: ProjectCreate) -> ProjectRead:
        if request.execution_mode == ExecutionMode.BROKE and not request.selected_model:
            recommendations = scout.recommend(
                RecommendationRequest(
                    project=request.prompt,
                    role="general coding",
                    hardware=HardwareTarget(
                        vram_mb=request.target_vram_mb,
                        quantization=request.quantization,
                    ),
                    limit=1,
                )
            )
            if not recommendations:
                raise HTTPException(
                    status_code=400,
                    detail="No evidence-backed model fits the selected hardware target",
                )
            choice = recommendations[0]
            request = request.model_copy(
                update={
                    "selected_model": choice.model.id,
                    "selected_model_vram_mb": choice.estimated_vram_mb,
                }
            )
        try:
            project = projects.create(request)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await events.publish(
            "project.created",
            f"Created {project.name}",
            project_id=project.id,
            payload={"execution_mode": request.execution_mode.value},
        )
        return project

    @app.get("/projects", response_model=list[ProjectRead])
    async def list_projects() -> list[ProjectRead]:
        return projects.list()

    @app.get("/projects/{project_id}", response_model=ProjectRead)
    async def get_project(project_id: str) -> ProjectRead:
        try:
            return projects.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc

    @app.post("/projects/{project_id}/run", status_code=202)
    async def run_project(project_id: str) -> dict[str, str]:
        try:
            orchestrator.start(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "accepted", "project_id": project_id}

    @app.post("/projects/{project_id}/cancel", status_code=202)
    async def cancel_project(project_id: str) -> dict[str, str]:
        try:
            await orchestrator.cancel(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        return {"status": "cancelled", "project_id": project_id}

    @app.post("/projects/{project_id}/research/prompt")
    async def research_prompt(project_id: str, hardware: HardwareTarget) -> dict[str, Any]:
        try:
            project = projects.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        recommendations = scout.recommend(
            RecommendationRequest(
                project=project.prompt,
                role="general coding",
                hardware=hardware,
                limit=3,
            )
        )
        prompt = research.prompt(
            project.prompt,
            "general coding",
            hardware.model_dump(mode="json"),
            recommendations,
        )
        await events.publish(
            "research.prompt.generated",
            "Generated a bounded manual model-research prompt",
            project_id=project_id,
            payload={"candidate_count": len(recommendations)},
        )
        return {
            "prompt": prompt,
            "recommendations": [item.model_dump(mode="json") for item in recommendations],
        }

    @app.post(
        "/projects/{project_id}/research/import", response_model=ResearchResult, status_code=201
    )
    async def import_research(project_id: str, request: ManualResearchImport) -> ResearchResult:
        try:
            projects.get(project_id)
            result = research.import_response(project_id, request, scout.list())
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await events.publish(
            "research.response.imported",
            f"Imported {request.advisor} model advice",
            project_id=project_id,
            payload={"research_id": result.id, "conflicts": result.conflicts},
        )
        return result

    @app.get("/projects/{project_id}/research", response_model=list[ResearchResult])
    async def project_research(project_id: str) -> list[ResearchResult]:
        try:
            projects.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        return research.list(project_id)

    @app.post(
        "/projects/{project_id}/workers/notebooks",
        response_model=list[NotebookRead],
        status_code=201,
    )
    async def generate_notebooks(
        project_id: str, request: NotebookGenerateRequest
    ) -> list[NotebookRead]:
        try:
            project = projects.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        if project.options.get("execution_mode") != ExecutionMode.BROKE.value:
            raise HTTPException(status_code=409, detail="Worker notebooks require Broke Boy mode")
        model_id = request.model_id or project.options.get("selected_model")
        if not model_id:
            raise HTTPException(status_code=409, detail="Select a model before generating workers")
        worker_count = request.worker_count or int(project.options.get("worker_count", 1))
        quantization = request.quantization or project.options.get("quantization", "4bit")
        evidence = next((item for item in scout.list() if item.id == model_id), None)
        if evidence is None:
            raise HTTPException(
                status_code=400, detail="Selected model has no trusted evidence record"
            )
        minimum_vram_mb = evidence.safe_vram_mb.get(quantization)
        if minimum_vram_mb is None:
            raise HTTPException(status_code=400, detail="Model does not support that quantization")
        compute_target = str(project.options.get("compute_target", "google_colab"))
        if compute_target in REMOTE_TARGETS:
            try:
                controller_url, generation = remote.require_remote_url()
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        else:
            controller_url = request.controller_url or remote.local_url
            generation = 0
        generated = [
            notebooks.generate(
                NotebookSpec(
                    project_id=project_id,
                    worker_name=f"{project_id[-6:]}-worker-{index + 1}",
                    model_id=model_id,
                    quantization=quantization,
                    controller_url=controller_url,
                    minimum_vram_mb=minimum_vram_mb,
                    connection_generation=generation,
                )
            )
            for index in range(worker_count)
        ]
        for notebook in generated:
            remote.record_notebook(notebook.artifact_id, project_id, controller_url, generation)
        await events.publish(
            "worker.notebooks.generated",
            f"Generated {len(generated)} secure worker notebook(s)",
            project_id=project_id,
            payload={
                "count": len(generated),
                "model": model_id,
                "quantization": quantization,
            },
        )
        return generated

    @app.get("/projects/{project_id}/tasks", response_model=list[TaskRead])
    async def project_tasks(project_id: str) -> list[TaskRead]:
        try:
            projects.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        return projects.tasks(project_id)

    @app.get("/projects/{project_id}/files")
    async def project_files(project_id: str) -> list[dict[str, object]]:
        try:
            return projects.file_tree(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc

    @app.get("/projects/{project_id}/artifacts", response_model=list[ArtifactRead])
    async def project_artifacts(project_id: str) -> list[ArtifactRead]:
        try:
            return projects.artifacts(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc

    @app.get("/projects/{project_id}/events", response_model=list[ActivityRead])
    async def project_events(
        project_id: str, limit: int = Query(default=100, ge=1, le=500)
    ) -> list[ActivityRead]:
        return events.recent(project_id, limit)

    @app.get("/events", response_model=list[ActivityRead])
    async def recent_events(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[ActivityRead]:
        return events.recent(None, limit)

    @app.get("/jobs", response_model=list[JobRead])
    async def list_jobs(
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[JobRead]:
        return leases.list(limit=limit)

    @app.get("/projects/{project_id}/jobs", response_model=list[JobRead])
    async def project_jobs(
        project_id: str, limit: int = Query(default=100, ge=1, le=500)
    ) -> list[JobRead]:
        try:
            projects.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        return leases.list(project_id=project_id, limit=limit)

    @app.get("/projects/{project_id}/export")
    async def export_project(project_id: str) -> FileResponse:
        try:
            archive = projects.export_zip(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        return FileResponse(archive, filename=f"{project_id}.zip", media_type="application/zip")

    @app.get("/tasks/{task_id}", response_model=TaskRead)
    async def get_task(task_id: str) -> TaskRead:
        try:
            return projects.task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Task not found") from exc

    @app.get("/workers", response_model=list[WorkerRead])
    async def list_workers() -> list[WorkerRead]:
        return workers.list()

    @app.get("/workers/{worker_id}", response_model=WorkerRead)
    async def get_worker(worker_id: str) -> WorkerRead:
        try:
            return workers.get(worker_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Worker not found") from exc

    @app.post("/workers/{worker_id}/revoke")
    async def revoke_worker(worker_id: str) -> dict[str, str]:
        if not pairing.revoke_worker(worker_id):
            raise HTTPException(status_code=404, detail="Active worker credential not found")
        await workers.revoke(worker_id)
        return {"status": "revoked", "worker_id": worker_id}

    @app.get("/models", response_model=list[ModelRead])
    async def list_models() -> list[ModelRead]:
        return models.list()

    @app.get("/model-scout/models")
    async def scout_models() -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in scout.list()]

    @app.post("/model-scout/recommend")
    async def recommend_models(request: RecommendationRequest) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in scout.recommend(request)]

    @app.post("/model-scout/discover")
    async def discover_models(
        query: str = Query(min_length=2, max_length=100),
    ) -> dict[str, Any]:
        try:
            items = await scout.hugging_face.discover(query, runtime.hf_token)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Hugging Face discovery failed: {exc}"
            ) from exc
        imported = scout.import_hugging_face_metadata(items)
        await events.publish(
            "model.discovery.completed",
            f"Discovered model metadata for {query}",
            payload={"query": query, "received": len(items), "imported": imported},
        )
        return {"received": len(items), "imported": imported}

    @app.get("/strategies")
    async def list_strategies() -> list[dict[str, Any]]:
        return strategies.describe()

    @app.get("/artifacts/{artifact_id}")
    async def download_artifact(artifact_id: str) -> FileResponse:
        with database.session() as session:
            artifact = session.get(ArtifactRecord, artifact_id)
            if not artifact:
                raise HTTPException(status_code=404, detail="Artifact not found")
            path = artifacts.path_for(artifact)
            filename = artifact.filename
        return FileResponse(path, filename=filename)

    @app.post("/pairing/redeem", response_model=WorkerCredential)
    async def redeem_pairing(request: PairingRedeem, http_request: Request) -> WorkerCredential:
        forwarded = http_request.headers.get("x-forwarded-for", "")
        client_key = forwarded.split(",", 1)[0].strip() or (
            http_request.client.host if http_request.client else "unknown"
        )
        now = monotonic()
        attempts = pairing_attempts[client_key]
        while attempts and attempts[0] < now - 60:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(
                status_code=429, detail="Too many pairing attempts; wait one minute"
            )
        attempts.append(now)
        try:
            token, worker_id, model_id, expires_at = pairing.redeem(request.pairing_code)
        except PairingError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return WorkerCredential(
            worker_token=token,
            worker_id=worker_id,
            model_id=model_id,
            expires_at=expires_at,
        )

    @app.websocket("/ws/ui")
    async def ui_socket(websocket: WebSocket) -> None:
        if remote.is_public_request_host(websocket.headers.get("host")):
            await websocket.close(code=1008, reason="UI socket is local-only")
            return
        await websocket.accept()
        await websocket.send_json({"type": "system.ready", "message": "Live events connected"})
        try:
            async for event in events.subscribe():
                await websocket.send_json(event)
        except WebSocketDisconnect:
            return

    @app.websocket("/ws/worker")
    async def worker_socket(websocket: WebSocket, token: str = Query(default="")) -> None:
        if websocket.headers.get("x-aegaeon-probe") == remote.probe_secret:
            await websocket.accept()
            await websocket.send_json({"type": "probe.ok"})
            await websocket.close()
            return
        credential = websocket.headers.get("x-aegaeon-worker-token") or token
        paired_identity = (
            None if credential == runtime.worker_token else pairing.identity(credential)
        )
        if credential != runtime.worker_token and paired_identity is None:
            await websocket.close(code=1008, reason="Invalid worker token")
            return
        await websocket.accept()
        worker_id: str | None = None
        try:
            while True:
                data = await websocket.receive_json()
                message_type = data.get("type")
                if message_type == "worker.register":
                    registration = WorkerRegister.model_validate(data)
                    if paired_identity is not None:
                        expected_worker, expected_model = paired_identity
                        registered_models = {item.id for item in registration.models}
                        if (
                            registration.worker_id != expected_worker
                            or expected_model not in registered_models
                        ):
                            await websocket.close(
                                code=1008,
                                reason="Pairing credential does not match worker identity or model",
                            )
                            return
                    worker_id = registration.worker_id
                    worker = await workers.register(registration, websocket)
                    await websocket.send_json({"type": "worker.registered", "worker_id": worker.id})
                elif message_type == "worker.heartbeat":
                    heartbeat = WorkerHeartbeat.model_validate(data)
                    if worker_id and heartbeat.worker_id == worker_id:
                        await workers.heartbeat(heartbeat)
                elif message_type in {"job.started", "job.log", "job.completed", "job.failed"}:
                    if not worker_id:
                        await websocket.send_json(
                            {"type": "protocol.error", "message": "Register before sending jobs"}
                        )
                        continue
                    try:
                        await workers.handle_job_message(worker_id, data)
                    except (KeyError, PermissionError, RuntimeError, ValueError) as exc:
                        await websocket.send_json({"type": "protocol.error", "message": str(exc)})
                        await events.publish(
                            "security.worker_message_rejected",
                            "Rejected an invalid or stale worker job message",
                            worker_id=worker_id,
                            payload={"job_id": data.get("job_id"), "type": message_type},
                        )
                        continue
                    await events.publish(
                        message_type,
                        data.get("message")
                        or (
                            "Worker completed job"
                            if message_type == "job.completed"
                            else message_type
                        ),
                        project_id=data.get("project_id"),
                        task_id=data.get("task_id"),
                        worker_id=worker_id,
                        payload={key: value for key, value in data.items() if key != "artifacts"},
                    )
                else:
                    await websocket.send_json(
                        {"type": "protocol.error", "message": "Unsupported message type"}
                    )
        except WebSocketDisconnect:
            pass
        finally:
            if worker_id:
                await workers.unregister(worker_id, websocket)

    return app
