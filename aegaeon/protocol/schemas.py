from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class TaskState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class JobState(StrEnum):
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    RUNNING = "running"
    TESTING = "testing"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionMode(StrEnum):
    RICH = "rich_boy"
    BROKE = "broke_boy"


class ResearchLevel(StrEnum):
    STANDARD = "standard"
    MANUAL = "manual"
    AI_ASSISTED = "ai_assisted"
    MULTI_ADVISOR = "multi_advisor"


class StrategyName(StrEnum):
    AUTO = "auto"
    SINGLE_MODEL = "single_model_multi_agent"
    SWARM = "multi_model_swarm"
    SPECIALIZED = "specialized_pipeline"
    DISTRIBUTED = "distributed_large_model"
    CUSTOM = "custom"


class GPUInfo(BaseModel):
    available: bool = False
    name: str | None = None
    vram_mb: int = 0


class HardwareInfo(BaseModel):
    cpu_cores: int = 1
    ram_mb: int = 0
    gpu: GPUInfo = Field(default_factory=GPUInfo)
    cuda: bool = False
    storage_mb: int = 0


class WorkerModel(BaseModel):
    id: str
    provider: str = "local"
    loaded: bool = False
    runtime: str | None = None
    quantization: str | None = None
    status: Literal["loading", "ready", "incompatible", "error"] = "loading"
    context_length: int = 0
    task_scores: dict[str, float] = Field(default_factory=dict)
    last_error: str | None = None


class WorkerRegister(BaseModel):
    type: Literal["worker.register"] = "worker.register"
    worker_id: str = Field(min_length=1, max_length=80)
    hostname: str
    hardware: HardwareInfo
    capabilities: list[str]
    models: list[WorkerModel] = Field(default_factory=list)


class WorkerHeartbeat(BaseModel):
    type: Literal["worker.heartbeat"] = "worker.heartbeat"
    worker_id: str
    status: Literal[
        "idle",
        "busy",
        "connecting",
        "detecting_hardware",
        "installing_runtime",
        "downloading_model",
        "loading_model",
        "ready",
        "retrying",
        "incompatible",
    ] = "idle"
    cpu_percent: float = 0
    ram_percent: float = 0
    gpu_percent: float = 0
    vram_used_mb: int = 0
    model_download_percent: float = Field(default=0, ge=0, le=100)


class JobRequirements(BaseModel):
    gpu: bool = False
    minimum_vram_mb: int = 0
    minimum_ram_mb: int = 0
    capabilities: list[str] = Field(default_factory=list)
    model: str | None = None
    runtime: str | None = None
    quantization: str | None = None
    task_category: str | None = None
    minimum_model_score: float = 0


class ModelGenerationContext(BaseModel):
    project_summary: str
    repository: str
    interfaces: dict[str, Any] = Field(default_factory=dict)
    acceptance_criteria: list[str] = Field(default_factory=list)
    permitted_file_scopes: list[str] = Field(default_factory=lambda: ["**/*"])


class ModelGenerationConstraints(BaseModel):
    maximum_output_bytes: int = Field(default=2_000_000, ge=1, le=2_000_000)
    maximum_files: int = Field(default=200, ge=1, le=200)
    maximum_new_tokens: int = Field(default=4_096, ge=128, le=32_768)


class ModelGeneratePayload(BaseModel):
    role: str
    instructions: str
    context: ModelGenerationContext
    constraints: ModelGenerationConstraints = Field(default_factory=ModelGenerationConstraints)
    failure_evidence: dict[str, Any] = Field(default_factory=dict)


class JobAssign(BaseModel):
    type: Literal["job.assign"] = "job.assign"
    job_id: str
    job_type: str = "demo.generate_project"
    project_id: str
    task_id: str
    agent_role: str
    requirements: JobRequirements
    instructions: str
    context_files: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_typed_payload(self) -> JobAssign:
        if self.job_type in {"model.generate", "model.repair"}:
            ModelGeneratePayload.model_validate(self.payload)
            if "model.generate" not in self.requirements.capabilities:
                raise ValueError("model jobs require the model.generate capability")
        return self


class ArtifactPayload(BaseModel):
    type: str
    filename: str
    content: str | None = None
    files: dict[str, str] = Field(default_factory=dict)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value: str) -> str:
        if "/" in value or "\\" in value or value in {".", ".."}:
            raise ValueError("filename must not contain a path")
        return value

    @field_validator("files")
    @classmethod
    def safe_source_files(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 200:
            raise ValueError("source bundle cannot contain more than 200 files")
        total = 0
        for name, content in value.items():
            if "\\" in name:
                raise ValueError("source paths must use forward slashes")
            path = PurePosixPath(name)
            if (
                path.is_absolute()
                or ".." in path.parts
                or ".git" in path.parts
                or (path.parts and path.parts[0].startswith("."))
            ):
                raise ValueError("source path must stay inside the disposable workspace")
            total += len(content.encode("utf-8"))
        if total > 2_000_000:
            raise ValueError("source bundle exceeds the 2 MB result limit")
        return value


class JobResult(BaseModel):
    type: Literal["job.completed", "job.failed"]
    job_id: str
    artifacts: list[ArtifactPayload] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class PlanTask(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    title: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    suggested_agent: Literal["planner", "coder", "reviewer", "integrator"]
    parallelizable: bool = False
    expected_outputs: list[str] = Field(default_factory=list)


class ProjectPlan(BaseModel):
    project_summary: str
    tasks: list[PlanTask] = Field(min_length=1)

    @field_validator("tasks")
    @classmethod
    def unique_valid_dependencies(cls, tasks: list[PlanTask]) -> list[PlanTask]:
        ids = [task.id for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("task ids must be unique")
        known = set(ids)
        positions = {task_id: index for index, task_id in enumerate(ids)}
        for task in tasks:
            dependencies = set(task.dependencies)
            if task.id in dependencies or not dependencies.issubset(known):
                raise ValueError(f"invalid dependencies for {task.id}")
            if len(task.dependencies) != len(dependencies):
                raise ValueError(f"duplicate dependencies for {task.id}")
            if any(positions[item] >= positions[task.id] for item in dependencies):
                raise ValueError(f"tasks must be in dependency order: {task.id}")

        for reserved in ("plan", "verification", "review"):
            if ids.count(reserved) != 1:
                raise ValueError(f"{reserved} must appear exactly once")
        by_id = {task.id: task for task in tasks}
        plan = by_id["plan"]
        if ids[0] != "plan" or plan.dependencies or plan.suggested_agent != "planner":
            raise ValueError("plan must be the first dependency-free planner task")
        if any(not task.dependencies for task in tasks if task.id != "plan"):
            raise ValueError("every non-plan task must have at least one dependency")
        if by_id["review"].dependencies != ["verification"]:
            raise ValueError("review must depend only on verification")
        return tasks


class ProjectCreate(BaseModel):
    prompt: str = Field(min_length=10, max_length=20_000)
    name: str | None = Field(default=None, max_length=160)
    strategy: StrategyName = StrategyName.AUTO
    intelligence: int = Field(default=70, ge=0, le=100)
    maximum_retries: int = Field(default=3, ge=0, le=10)
    run_tests: bool = True
    review_code: bool = True
    retry_failed_tasks: bool = True
    execution_mode: ExecutionMode = ExecutionMode.RICH
    worker_count: int = Field(default=1, ge=1, le=12)
    compute_target: Literal["google_colab", "kaggle", "local_gpu", "cloud_gpu"] = "google_colab"
    model_selection: Literal["recommend", "manual"] = "recommend"
    selected_model: str | None = Field(default=None, max_length=200)
    selected_model_vram_mb: int | None = Field(default=None, ge=0, le=200_000)
    target_vram_mb: int = Field(default=15_000, ge=0, le=200_000)
    quantization: Literal["4bit", "8bit", "bf16"] = "4bit"
    research_level: ResearchLevel = ResearchLevel.STANDARD


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class TaskRead(ORMModel):
    id: str
    project_id: str
    key: str
    title: str
    description: str
    agent_role: str
    status: str
    dependencies: list[str]
    required_capabilities: list[str]
    sequence: int
    retry_count: int
    max_retries: int
    worker_id: str | None
    job_id: str | None
    duration_seconds: float | None
    logs: list[str]
    files_modified: list[str]
    result: dict[str, Any]
    started_at: datetime | None
    completed_at: datetime | None


class JobRead(ORMModel):
    id: str
    project_id: str
    task_id: str
    job_type: str
    agent_role: str
    worker_id: str | None
    status: str
    attempt: int
    requirements: dict[str, Any]
    instructions: str
    payload_summary: dict[str, Any]
    result: dict[str, Any]
    error: str | None
    lease_expires_at: datetime | None
    assigned_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProjectRead(ORMModel):
    id: str
    name: str
    prompt: str
    status: str
    strategy: str
    progress: int
    workspace_path: str
    options: dict[str, Any]
    summary: str
    created_at: datetime
    updated_at: datetime
    tasks: list[TaskRead] = Field(default_factory=list)


class WorkerRead(ORMModel):
    id: str
    hostname: str
    status: str
    hardware: dict[str, Any]
    capabilities: list[str]
    models: list[dict[str, Any]]
    cpu_percent: float
    ram_percent: float
    gpu_percent: float
    vram_used_mb: int
    current_task: str | None
    last_heartbeat: datetime


class PairingCreate(BaseModel):
    worker_name: str = Field(min_length=1, max_length=60)
    model_id: str = Field(min_length=3, max_length=200)


class PairingRead(BaseModel):
    pairing_code: str
    expires_at: datetime
    worker_name: str
    model_id: str


class PairingRedeem(BaseModel):
    pairing_code: str = Field(min_length=8, max_length=20)


class WorkerCredential(BaseModel):
    worker_token: str
    worker_id: str
    model_id: str
    expires_at: datetime


class NotebookGenerateRequest(BaseModel):
    controller_url: str | None = Field(default=None, pattern=r"^https?://")
    model_id: str | None = Field(default=None, max_length=200)
    worker_count: int | None = Field(default=None, ge=1, le=12)
    quantization: Literal["4bit", "8bit", "bf16"] | None = None


class NotebookRead(BaseModel):
    artifact_id: str
    filename: str
    worker_name: str
    model_id: str
    pairing_code: str
    pairing_expires_at: datetime
    download_url: str
    controller_url: str
    connection_generation: int = 0
    stale: bool = False


class ModelRead(ORMModel):
    id: str
    name: str
    type: str
    provider: str
    capabilities: list[str]
    minimum_vram_mb: int
    context_length: int
    status: str


class ArtifactRead(ORMModel):
    id: str
    project_id: str
    task_id: str | None
    type: str
    filename: str
    size_bytes: int
    checksum: str
    created_at: datetime


class ActivityRead(ORMModel):
    id: str
    type: str
    project_id: str | None
    task_id: str | None
    worker_id: str | None
    message: str
    payload: dict[str, Any]
    created_at: datetime


class ExecutionResult(BaseModel):
    command: str
    exit_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool = False
