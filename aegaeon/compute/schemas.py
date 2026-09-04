from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ComputePolicyPreset(StrEnum):
    QUALITY_FIRST = "QUALITY_FIRST"
    BALANCED = "BALANCED"
    MEMORY_SAFE = "MEMORY_SAFE"
    FAST = "FAST"


class OOMRisk(StrEnum):
    LOW = "LOW"
    MODERATE = "MODERATE"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"


class DeviceStrategy(StrEnum):
    GPU = "GPU"
    GPU_WITH_CPU_OFFLOAD = "GPU_WITH_CPU_OFFLOAD"
    MULTI_GPU = "MULTI_GPU"
    CPU_ONLY = "CPU_ONLY"


class RecoveryAction(StrEnum):
    CLEAN_MEMORY = "CLEAN_MEMORY"
    RETRY_REDUCED_OUTPUT = "RETRY_REDUCED_OUTPUT"
    RETRY_REDUCED_CONTEXT = "RETRY_REDUCED_CONTEXT"
    SPLIT_TASK = "SPLIT_TASK"
    RETRY_WITH_CPU_OFFLOAD = "RETRY_WITH_CPU_OFFLOAD"
    RETRY_WITH_DIFFERENT_QUANTIZATION = "RETRY_WITH_DIFFERENT_QUANTIZATION"
    RETRY_DIFFERENT_RUNTIME = "RETRY_DIFFERENT_RUNTIME"
    RETRY_DIFFERENT_WORKER = "RETRY_DIFFERENT_WORKER"
    FALLBACK_MODEL = "FALLBACK_MODEL"
    ESCALATE_USER = "ESCALATE_USER"


class IncidentState(StrEnum):
    RECOVERING = "RECOVERING"
    RETRYING = "RETRYING"
    RESOLVED = "RESOLVED"
    FAILED_FINAL = "FAILED_FINAL"


class ComputePolicy(BaseModel):
    preset: ComputePolicyPreset = ComputePolicyPreset.BALANCED
    allow_model_fallback: bool = False
    minimum_quality_score: float = Field(default=60, ge=0, le=100)
    prefer_same_model: bool = True
    prefer_same_model_family: bool = True
    allow_runtime_change: bool = False
    allow_quantization_change: bool = True
    allow_cpu_offload: bool = True
    allow_multi_gpu: bool = True
    allow_cpu_only: bool = False
    minimum_viable_output_tokens: int = Field(default=768, ge=256, le=4096)
    ram_safety_buffer_mb: int = Field(default=4096, ge=1024)


class GPUState(BaseModel):
    index: int = 0
    name: str = "Unknown GPU"
    total_mb: int = 0
    allocated_mb: int = 0
    reserved_mb: int = 0
    free_mb: int = 0
    utilization_percent: float = 0
    compute_capability: str | None = None


class WorkerComputeState(BaseModel):
    worker_id: str
    hostname: str
    status: str
    gpus: list[GPUState] = Field(default_factory=list)
    ram_total_mb: int = 0
    ram_available_mb: int = 0
    cpu_cores: int = 1
    disk_free_mb: int = 0
    current_running_jobs: int = 0
    resident_model: str | None = None
    runtime: str | None = None
    quantization: str | None = None
    model_memory_mb: int = 0
    max_recommended_prompt_tokens: int = 0
    max_recommended_output_tokens: int = 0
    memory_risk_profile: str = OOMRisk.LOW.value
    supports_cpu_offload: bool = False
    supports_multi_gpu: bool = False

    @property
    def free_vram_mb(self) -> int:
        return sum(max(0, gpu.free_mb) for gpu in self.gpus)

    @property
    def total_vram_mb(self) -> int:
        return sum(max(0, gpu.total_mb) for gpu in self.gpus)


class ContextSection(BaseModel):
    name: str
    content: str
    priority: int = Field(default=50, ge=0, le=100)
    protected: bool = False
    estimated_tokens: int = 0
    included_tokens: int = 0
    included: bool = True


class ContextPlan(BaseModel):
    token_budget: int
    estimated_tokens: int
    packed_tokens: int
    sections: list[ContextSection]
    dropped_sections: list[str] = Field(default_factory=list)


class ModelExecutionPlan(BaseModel):
    id: str
    project_id: str | None = None
    task_id: str | None = None
    model_id: str
    runtime: str
    quantization: str
    worker_id: str | None = None
    actual_worker_id: str | None = None
    status: str = "PLANNED"
    device_strategy: DeviceStrategy = DeviceStrategy.GPU
    prompt_token_budget: int = 8192
    maximum_new_tokens: int = 2048
    memory_strategy: str = "PRESERVE_HEADROOM"
    offload_strategy: str = "NONE"
    attention_strategy: str = "RUNTIME_DEFAULT"
    estimated_vram_required_mb: int = 0
    estimated_ram_required_mb: int = 0
    estimated_oom_risk: OOMRisk = OOMRisk.LOW
    fit_score: int = Field(default=100, ge=0, le=100)
    fallback_models: list[str] = Field(default_factory=list)
    fallback_workers: list[str] = Field(default_factory=list)
    task_split_allowed: bool = True
    recovery_policy: list[RecoveryAction] = Field(default_factory=list)
    next_recovery_action: RecoveryAction | None = None
    reasoning_summary: list[str] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    context_plan: ContextPlan | None = None
    policy: ComputePolicy = Field(default_factory=ComputePolicy)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ComputeObservation(BaseModel):
    id: str
    project_id: str | None = None
    task_id: str | None = None
    plan_id: str | None = None
    worker_id: str | None = None
    model_id: str
    runtime: str
    quantization: str
    gpu_name: str = "Unknown GPU"
    vram_total_mb: int = 0
    prompt_tokens: int = 0
    output_limit: int = 0
    generated_tokens: int = 0
    peak_vram_mb: int = 0
    elapsed_seconds: float = 0
    result: str
    failure_classification: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ComputeIncident(BaseModel):
    id: str
    root_failure_id: str
    project_id: str
    task_id: str
    worker_id: str | None = None
    model_id: str | None = None
    gpu_name: str | None = None
    classification: str = "CUDA_OOM"
    state: IncidentState = IncidentState.RECOVERING
    attempts: list[dict[str, Any]] = Field(default_factory=list)
    recovery_action: RecoveryAction | None = None
    message: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    resolved_at: datetime | None = None


class ComputeOverview(BaseModel):
    policy: ComputePolicy
    workers: list[WorkerComputeState]
    active_plans: list[ModelExecutionPlan]
    observations: list[ComputeObservation]
    incidents: list[ComputeIncident]
    summary: dict[str, Any] = Field(default_factory=dict)
