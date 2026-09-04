from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from secrets import compare_digest
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import text

from aegaeon.agents.demo import DemoAgentRuntime
from aegaeon.agents.llm import LLMAgentRuntime
from aegaeon.artifacts.manager import ArtifactManager
from aegaeon.compute import ComputeIntelligence
from aegaeon.compute.schemas import ComputePolicy
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
from aegaeon.orchestration import OrchestrationArchitectureService
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
    RunRead,
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
from aegaeon.security import PairingRateLimiter
from aegaeon.strategy.router import StrategyRouter
from aegaeon.workers.notebooks import ColabNotebookGenerator, NotebookSpec
from aegaeon.workers.pairing import PairingError, WorkerPairingService
from aegaeon.workers.registry import (
    WORKER_SUPERSEDED_CLOSE_CODE,
    WorkerRegistry,
    WorkerSessionConflict,
)
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
    compute = ComputeIntelligence(database)
    research = ResearchService(database)
    pairing = WorkerPairingService(
        database,
        runtime.pairing_code_lifetime_seconds,
        runtime.worker_credential_lifetime_seconds,
    )
    pairing_rate_limiter = PairingRateLimiter(database)
    notebooks = ColabNotebookGenerator(artifacts, pairing)
    remote = RemoteConnectivityManager(runtime, database, events)
    architecture = OrchestrationArchitectureService(database, projects)
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
        architecture,
        agents,
        TestCommandDetector(),
        compute=compute,
        scout=scout,
    )
    stop_monitor = asyncio.Event()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        database.create_all()
        await remote.reconcile()
        models.seed()
        scout.seed()
        for project in projects.list():
            try:
                preview_plan = orchestrator.broke_planner.plan(
                    project.prompt,
                    int(project.options.get("worker_count", 1)),
                )
                architecture.prepare(
                    project.id,
                    project.prompt,
                    preview_plan,
                    project.options,
                    projects.current_revision(project.id),
                )
            except (KeyError, OSError, RuntimeError, ValueError):
                # A damaged legacy workspace must not prevent healthy projects from loading.
                continue
        recoverable_projects = leases.recover_orphans()
        orchestrator.recover(recoverable_projects)
        monitor = asyncio.create_task(workers.monitor(stop_monitor), name="worker-heartbeats")
        yield
        stop_monitor.set()
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)
        await orchestrator.shutdown()
        await remote.shutdown()
        close_provider = getattr(model_provider, "aclose", None)
        if close_provider is not None:
            await close_provider()
        database.close()

    app = FastAPI(
        title="AEGAEON Controller",
        version="0.3.0",
        description="Local-first AI coding and compute orchestration control plane",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=runtime.ui_origins,
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
    app.state.compute = compute
    app.state.research = research
    app.state.pairing = pairing
    app.state.notebooks = notebooks
    app.state.strategies = strategies
    app.state.orchestrator = orchestrator
    app.state.architecture = architecture
    app.state.leases = leases
    app.state.provider = model_provider
    app.state.model_runtime = agents
    app.state.remote = remote

    @app.middleware("http")
    async def restrict_public_surface(request: Request, call_next: Any) -> Any:
        public_paths = {"/health", "/live", "/ready", "/pairing/redeem"}
        is_public_host = remote.is_public_request_host(request.headers.get("host"))
        if is_public_host and request.url.path not in public_paths:
            return JSONResponse(
                status_code=403,
                content={"detail": "This route is not exposed to remote workers"},
            )
        peer = request.client.host if request.client else "unknown"
        local_peers = {"127.0.0.1", "::1", "localhost", "testclient"}
        protected = request.url.path not in public_paths
        authentication_required = protected and (
            peer not in local_peers or bool(runtime.control_token)
        )
        if authentication_required:
            scheme, _, supplied = request.headers.get("authorization", "").partition(" ")
            valid = (
                bool(runtime.control_token)
                and scheme.lower() == "bearer"
                and compare_digest(supplied, runtime.control_token)
            )
            if not valid:
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Control-plane authentication required"},
                    headers={"WWW-Authenticate": "Bearer"},
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

    @app.get("/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/ready")
    async def ready() -> dict[str, Any]:
        checks: dict[str, bool] = {"database": False, "data_directory": False, "disk": False}
        try:
            with database.session() as session:
                session.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:
            pass
        data_dir = runtime.data_dir.resolve()
        checks["data_directory"] = data_dir.is_dir()
        try:
            checks["disk"] = shutil.disk_usage(data_dir).free >= 100 * 1024 * 1024
        except OSError:
            pass
        if not all(checks.values()):
            raise HTTPException(status_code=503, detail={"status": "not_ready", "checks": checks})
        return {"status": "ready", "checks": checks}

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
        if (
            request.execution_mode == ExecutionMode.BROKE
            and request.model_selection == "manual"
            and not request.selected_model
        ):
            raise HTTPException(
                status_code=400,
                detail="Manual model selection requires a selected model",
            )
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
        preview_plan = orchestrator.broke_planner.plan(request.prompt, request.worker_count)
        architecture.prepare(
            project.id,
            request.prompt,
            preview_plan,
            project.options,
            projects.current_revision(project.id),
        )
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

    @app.put("/projects/{project_id}/pin", response_model=ProjectRead)
    async def pin_project(project_id: str, values: dict[str, Any]) -> ProjectRead:
        try:
            project = projects.set_pinned(project_id, bool(values.get("is_pinned", True)))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        await events.publish(
            "project.pinned" if project.is_pinned else "project.unpinned",
            f"{project.name} {'pinned' if project.is_pinned else 'unpinned'}",
            project_id=project_id,
            payload={"is_pinned": project.is_pinned, "sort_order": project.sort_order},
        )
        return project

    @app.post("/projects/{project_id}/move", response_model=ProjectRead)
    async def move_project(project_id: str, values: dict[str, Any]) -> ProjectRead:
        try:
            project = projects.move(project_id, str(values.get("direction", "")))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await events.publish(
            "project.reordered",
            f"Moved {project.name} {values.get('direction')}",
            project_id=project_id,
            payload={"direction": values.get("direction"), "sort_order": project.sort_order},
        )
        return project

    @app.delete("/projects/{project_id}")
    async def delete_project(project_id: str) -> dict[str, object]:
        try:
            project = projects.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        active_jobs = [
            job
            for job in leases.list(project_id=project_id, include_history=True)
            if job.status in {"QUEUED", "ASSIGNED", "RUNNING"}
        ]
        if project.status in {"planning", "running", "testing", "reviewing"} or active_jobs:
            raise HTTPException(
                status_code=409,
                detail="Cancel the active run and wait for its jobs to stop before deletion",
            )
        result = projects.delete(project_id)
        await events.publish(
            "project.deleted",
            f"Deleted project {project.name}",
            payload={"deleted_project_id": project_id, **result},
        )
        return result

    @app.post("/projects/{project_id}/run", status_code=202)
    async def run_project(project_id: str) -> dict[str, str]:
        try:
            run_id = orchestrator.start(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"status": "accepted", "project_id": project_id, "run_id": run_id}

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
        target_vram_mb = int(project.options.get("target_vram_mb") or 15_000)
        automatic_selection = (
            project.options.get("model_selection") == "recommend" and request.model_id is None
        )
        if automatic_selection:
            recommendations = scout.recommend(
                RecommendationRequest(
                    project=project.prompt,
                    role="general coding",
                    hardware=HardwareTarget(
                        vram_mb=target_vram_mb,
                        quantization=quantization,
                    ),
                    limit=1,
                )
            )
            if not recommendations:
                raise HTTPException(
                    status_code=400,
                    detail="No trusted model has enough measured VRAM headroom for this target",
                )
            recommendation = recommendations[0]
            model_id = recommendation.model.id
            if model_id != project.options.get("selected_model"):
                project = projects.update_options(
                    project_id,
                    {
                        "selected_model": model_id,
                        "selected_model_vram_mb": recommendation.estimated_vram_mb,
                        "compute_adaptation": {
                            "reason": "RESOURCE_AWARE_MODEL_SELECTION",
                            "requested_model": project.options.get("selected_model"),
                            "selected_model": model_id,
                            "target_vram_mb": target_vram_mb,
                        },
                    },
                )
        evidence = next((item for item in scout.list() if item.id == model_id), None)
        if evidence is None:
            raise HTTPException(
                status_code=400, detail="Selected model has no trusted evidence record"
            )
        if evidence.safe_vram_mb.get(quantization) is None:
            raise HTTPException(status_code=400, detail="Model does not support that quantization")
        if automatic_selection:
            model_candidates = scout.fallback_ladder(
                model_id,
                quantization,
                vram_mb=target_vram_mb,
            )
        else:
            peak = scout.estimated_peak_vram(evidence, quantization)
            model_candidates = [
                {
                    "id": model_id,
                    "base_vram_mb": int(evidence.safe_vram_mb[quantization]),
                    "estimated_peak_vram_mb": int(peak or evidence.safe_vram_mb[quantization]),
                }
            ]
        if not model_candidates:
            raise HTTPException(
                status_code=400,
                detail="No model candidate retains the required VRAM safety reserve",
            )
        minimum_vram_mb = min(int(item["base_vram_mb"]) for item in model_candidates)
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
                    model_candidates=tuple(model_candidates),
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
                "model_candidates": [item["id"] for item in model_candidates],
            },
        )
        return generated

    @app.get("/projects/{project_id}/orchestration")
    async def project_orchestration(project_id: str) -> dict[str, Any]:
        try:
            projects.get(project_id)
            return architecture.summary(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc

    @app.patch("/projects/{project_id}/milestones/{milestone_id}")
    async def update_milestone(
        project_id: str, milestone_id: str, values: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            projects.get(project_id)
            result = architecture.update_milestone(project_id, milestone_id, values)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project or milestone not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await events.publish(
            "milestone.updated",
            "Milestone plan updated and dependency graph revalidated",
            project_id=project_id,
            payload={"milestone_id": milestone_id},
        )
        return result

    @app.put("/projects/{project_id}/compute-plan")
    async def update_compute_plan(project_id: str, values: dict[str, Any]) -> dict[str, Any]:
        try:
            projects.get(project_id)
            result = architecture.select_compute(
                project_id,
                str(values.get("strategy", "balanced")),
                int(values.get("worker_count", 1)),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await events.publish(
            "compute.plan.updated",
            "Compute strategy and reusable worker count updated",
            project_id=project_id,
            payload={
                "strategy": values.get("strategy"),
                "worker_count": values.get("worker_count"),
            },
        )
        return result

    @app.get("/projects/{project_id}/runs", response_model=list[RunRead])
    async def project_runs(project_id: str) -> list[RunRead]:
        try:
            return projects.runs(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc

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
        project_id: str,
        limit: int = Query(default=100, ge=1, le=500),
        include_history: bool = Query(default=False),
    ) -> list[JobRead]:
        try:
            projects.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        return leases.list(project_id=project_id, limit=limit, include_history=include_history)

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
    async def revoke_worker(worker_id: str) -> dict[str, Any]:
        credential_revoked = pairing.revoke_worker(worker_id)
        disconnected = await workers.revoke(worker_id)
        if not credential_revoked and not disconnected:
            raise HTTPException(status_code=404, detail="Worker not found")
        return {
            "status": "revoked",
            "worker_id": worker_id,
            "credential_revoked": credential_revoked,
            "disconnected": disconnected,
        }

    @app.get("/compute")
    async def compute_overview(
        project_id: str | None = Query(default=None),
    ) -> dict[str, Any]:
        options: dict[str, Any] = {}
        if project_id:
            try:
                options = projects.get(project_id).options
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Project not found") from exc
        overview = compute.overview(
            workers=workers.list(),
            project_id=project_id,
            options=options,
        )
        return overview.model_dump(mode="json")

    @app.get("/compute/plans")
    async def compute_plans(
        project_id: str | None = Query(default=None),
        task_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in compute.plans(project_id=project_id, task_id=task_id, limit=limit)
        ]

    @app.get("/compute/observations")
    async def compute_observations(
        project_id: str | None = Query(default=None),
        model_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in compute.observations(
                project_id=project_id,
                model_id=model_id,
                limit=limit,
            )
        ]

    @app.get("/compute/incidents")
    async def compute_incidents(
        project_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, Any]]:
        return [
            item.model_dump(mode="json")
            for item in compute.incidents(project_id=project_id, limit=limit)
        ]

    @app.put("/projects/{project_id}/compute-policy")
    async def update_compute_policy(
        project_id: str,
        request: ComputePolicy,
    ) -> dict[str, Any]:
        try:
            projects.get(project_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Project not found") from exc
        return compute.save_policy(project_id, request).model_dump(mode="json")

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
        peer = http_request.client.host if http_request.client else "unknown"
        forwarded = (
            http_request.headers.get("x-forwarded-for", "")
            if peer in runtime.trusted_proxy_ips
            else ""
        )
        client_key = forwarded.split(",", 1)[0].strip() or peer
        if not pairing_rate_limiter.allow(client_key):
            raise HTTPException(
                status_code=429, detail="Too many pairing attempts; wait one minute"
            )
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
        origin = websocket.headers.get("origin")
        if remote.is_public_request_host(websocket.headers.get("host")) or (
            origin is not None and origin not in runtime.ui_origins
        ):
            await websocket.close(code=1008, reason="UI socket is local-only")
            return
        await websocket.accept()
        cursor = websocket.query_params.get("after")
        if cursor:
            for event in events.since(cursor):
                await asyncio.wait_for(
                    websocket.send_json(event.model_dump(mode="json")),
                    timeout=10,
                )
        await websocket.send_json({"type": "system.ready", "message": "Live events connected"})
        try:
            async for event in events.subscribe():
                await asyncio.wait_for(websocket.send_json(event), timeout=10)
        except (WebSocketDisconnect, TimeoutError):
            return

    @app.websocket("/ws/worker")
    async def worker_socket(websocket: WebSocket) -> None:
        if websocket.headers.get("x-aegaeon-probe") == remote.probe_secret:
            await websocket.accept()
            await websocket.send_json({"type": "probe.ok"})
            await websocket.close()
            return
        if websocket.headers.get("origin"):
            await websocket.close(code=1008, reason="Browser origins cannot open worker sockets")
            return
        is_public_host = remote.is_public_request_host(websocket.headers.get("host"))
        credential = websocket.headers.get("x-aegaeon-worker-token", "")
        configured_bootstrap_credential = (
            not is_public_host
            and compare_digest(credential, runtime.worker_token)
            and (
                runtime.worker_token != "development-token"
                or runtime.allow_development_worker_token
            )
        )
        paired_context = (
            None if configured_bootstrap_credential else pairing.identity_context(credential)
        )
        paired_identity = paired_context[:2] if paired_context else None
        if not configured_bootstrap_credential and paired_identity is None:
            await websocket.close(code=1008, reason="Invalid worker token")
            return
        await websocket.accept()
        worker_id: str | None = None
        try:
            while True:
                data = await websocket.receive_json()
                message_type = data.get("type")
                if message_type == "worker.register":
                    try:
                        registration = WorkerRegister.model_validate(data)
                    except ValidationError as exc:
                        await websocket.send_json(
                            {
                                "type": "protocol.error",
                                "message": "Invalid worker registration telemetry",
                                "validation_errors": exc.errors(
                                    include_url=False,
                                    include_input=False,
                                ),
                            }
                        )
                        await websocket.close(
                            code=1003,
                            reason="Invalid worker registration",
                        )
                        return
                    model_adaptation: dict[str, Any] | None = None
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
                        ready_models = [
                            item
                            for item in registration.models
                            if item.loaded and item.status == "ready"
                        ]
                        if ready_models:
                            actual_model = ready_models[0]
                            expected_worker, expected_model, project_id = paired_context
                            if actual_model.id != expected_model:
                                try:
                                    project = projects.get(project_id)
                                except KeyError:
                                    await websocket.close(
                                        code=1008,
                                        reason="Paired project no longer exists",
                                    )
                                    return
                                if project.options.get("model_selection") != "recommend":
                                    await websocket.close(
                                        code=1008,
                                        reason=(
                                            "Manual model selection cannot be changed by a worker"
                                        ),
                                    )
                                    return
                                total_vram = int(registration.hardware.gpu.vram_mb or 0)
                                quantization = str(actual_model.quantization or "4bit")
                                allowed = scout.fallback_ladder(
                                    expected_model,
                                    quantization,
                                    vram_mb=total_vram,
                                    prompt_tokens=max(
                                        1_024,
                                        actual_model.max_recommended_prompt_tokens,
                                    ),
                                    output_tokens=max(
                                        256,
                                        actual_model.max_recommended_output_tokens,
                                    ),
                                )
                                allowed_ids = {str(item["id"]) for item in allowed}
                                if actual_model.id not in allowed_ids:
                                    await websocket.close(
                                        code=1008,
                                        reason="Worker selected an untrusted model fallback",
                                    )
                                    return
                                selected = next(
                                    item for item in allowed if item["id"] == actual_model.id
                                )
                                model_adaptation = {
                                    "reason": "LIVE_RESOURCE_GOVERNOR",
                                    "requested_model": expected_model,
                                    "selected_model": actual_model.id,
                                    "gpu": registration.hardware.gpu.name,
                                    "vram_mb": total_vram,
                                    "measured_prompt_tokens": (
                                        actual_model.max_recommended_prompt_tokens
                                    ),
                                }
                                projects.update_options(
                                    project_id,
                                    {
                                        "selected_model": actual_model.id,
                                        "selected_model_vram_mb": int(
                                            selected["estimated_peak_vram_mb"]
                                        ),
                                        "compute_adaptation": model_adaptation,
                                    },
                                )
                                await events.publish(
                                    "compute.model.adapted",
                                    f"Resource governor selected {actual_model.id}",
                                    project_id=project_id,
                                    worker_id=registration.worker_id,
                                    payload=model_adaptation,
                                )
                    try:
                        worker = await workers.register(registration, websocket)
                    except WorkerSessionConflict:
                        await websocket.close(
                            code=WORKER_SUPERSEDED_CLOSE_CODE,
                            reason="Another legacy worker session is already active",
                        )
                        return
                    worker_id = registration.worker_id
                    await websocket.send_json(
                        {
                            "type": "worker.registered",
                            "worker_id": worker.id,
                            "connection_generation": worker.connection_generation,
                            "resume_jobs": [
                                {
                                    "job_id": job.id,
                                    "attempt_id": job.attempt_id,
                                    "lease_token": job.lease_token,
                                    "status": job.status,
                                }
                                for job in workers.leases.active_for_worker(worker.id)
                            ],
                            "model_adaptation": model_adaptation,
                        }
                    )
                elif message_type == "worker.heartbeat":
                    try:
                        heartbeat = WorkerHeartbeat.model_validate(data)
                    except ValidationError as exc:
                        await websocket.send_json(
                            {
                                "type": "protocol.error",
                                "message": "Invalid worker heartbeat telemetry",
                                "validation_errors": exc.errors(
                                    include_url=False,
                                    include_input=False,
                                ),
                            }
                        )
                        continue
                    if worker_id and heartbeat.worker_id == worker_id:
                        if not workers.is_current(worker_id, websocket):
                            await websocket.close(
                                code=WORKER_SUPERSEDED_CLOSE_CODE,
                                reason="Worker session superseded",
                            )
                            return
                        await workers.heartbeat(heartbeat, websocket)
                elif message_type in {
                    "job.ack",
                    "job.started",
                    "job.log",
                    "job.completed",
                    "job.failed",
                    "job.cancelled",
                }:
                    if not worker_id:
                        await websocket.send_json(
                            {"type": "protocol.error", "message": "Register before sending jobs"}
                        )
                        continue
                    if not workers.is_current(worker_id, websocket):
                        await websocket.close(
                            code=WORKER_SUPERSEDED_CLOSE_CODE,
                            reason="Worker session superseded",
                        )
                        return
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
                        or data.get("error")
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
