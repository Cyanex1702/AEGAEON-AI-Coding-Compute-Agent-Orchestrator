from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Persist UTC and restore timezone-aware values, including legacy SQLite rows."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        utc_value = aware.astimezone(UTC)
        return utc_value.replace(tzinfo=None) if dialect.name == "sqlite" else utc_value

    def process_result_value(self, value: datetime | None, _: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class ProjectRecord(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    prompt: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    strategy: Mapped[str] = mapped_column(String(64), default="auto")
    progress: Mapped[int] = mapped_column(Integer, default=0)
    workspace_path: Mapped[str] = mapped_column(Text)
    options: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    summary: Mapped[str] = mapped_column(Text, default="")
    active_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)

    runs: Mapped[list[RunRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[TaskRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan", order_by="TaskRecord.sequence"
    )
    artifacts: Mapped[list[ArtifactRecord]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class RunRecord(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    recovered: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    project: Mapped[ProjectRecord] = relationship(back_populates="runs")


class TaskRecord(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    milestone_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    base_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contract_version: Mapped[int] = mapped_column(Integer, default=1)
    allowed_files: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["**/*"])
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, default=list)
    key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text)
    agent_role: Mapped[str] = mapped_column(String(32), default="coder")
    status: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    required_capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    parallelizable: Mapped[bool] = mapped_column(default=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    duration_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    logs: Mapped[list[str]] = mapped_column(JSON, default=list)
    files_modified: Mapped[list[str]] = mapped_column(JSON, default=list)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    project: Mapped[ProjectRecord] = relationship(back_populates="tasks")


class JobRecord(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    attempt_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    lease_token: Mapped[str] = mapped_column(String(64), default="")
    connection_generation: Mapped[int] = mapped_column(Integer, default=0)
    message_sequence: Mapped[int] = mapped_column(Integer, default=0)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    job_type: Mapped[str] = mapped_column(String(80))
    agent_role: Mapped[str] = mapped_column(String(32))
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    requirements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    instructions: Mapped[str] = mapped_column(Text)
    payload_summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    failure_stage: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    failure_classification: Mapped[str | None] = mapped_column(
        String(80), nullable=True, index=True
    )
    error_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retry_strategy: Mapped[str | None] = mapped_column(String(80), nullable=True)
    failed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    logs: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    offer_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    result_uploaded_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    assigned_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class WorkerRecord(Base):
    __tablename__ = "workers"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32), default="offline", index=True)
    hardware: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    models: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    cpu_percent: Mapped[float] = mapped_column(Float, default=0)
    ram_percent: Mapped[float] = mapped_column(Float, default=0)
    gpu_percent: Mapped[float] = mapped_column(Float, default=0)
    vram_used_mb: Mapped[int] = mapped_column(Integer, default=0)
    current_task: Mapped[str | None] = mapped_column(String(64), nullable=True)
    current_job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    stage: Mapped[str | None] = mapped_column(String(80), nullable=True)
    progress_percent: Mapped[float] = mapped_column(Float, default=0)
    session_id: Mapped[str] = mapped_column(String(80), default="")
    connection_generation: Mapped[int] = mapped_column(Integer, default=0)
    progress_sequence: Mapped[int] = mapped_column(Integer, default=0)
    telemetry_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    draining: Mapped[bool] = mapped_column(Boolean, default=False)
    role_preferences: Mapped[list[str]] = mapped_column(JSON, default=list)
    connected_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    last_heartbeat: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ModelRecord(Base):
    __tablename__ = "models"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160))
    type: Mapped[str] = mapped_column(String(32), default="llm")
    provider: Mapped[str] = mapped_column(String(80))
    capabilities: Mapped[list[str]] = mapped_column(JSON, default=list)
    minimum_vram_mb: Mapped[int] = mapped_column(Integer, default=0)
    context_length: Mapped[int] = mapped_column(Integer, default=32768)
    status: Mapped[str] = mapped_column(String(32), default="configured")


class ModelCatalogRecord(Base):
    __tablename__ = "model_catalog"

    id: Mapped[str] = mapped_column(String(200), primary_key=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class RateLimitBucketRecord(Base):
    __tablename__ = "rate_limit_buckets"

    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    window_started_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class WorkerPairingRecord(Base):
    __tablename__ = "worker_pairings"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    worker_name: Mapped[str] = mapped_column(String(80))
    model_id: Mapped[str] = mapped_column(String(200))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    redeemed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    credential_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    credential_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class WorkerNotebookRecord(Base):
    __tablename__ = "worker_notebooks"

    # Remote-connection metadata can be recorded independently during provider tests;
    # the artifact manager still validates real notebook identifiers at the API boundary.
    artifact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    controller_url: Mapped[str] = mapped_column(Text)
    connection_generation: Mapped[int] = mapped_column(Integer, default=0)
    stale: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ResearchRecord(Base):
    __tablename__ = "research_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    advisor: Mapped[str] = mapped_column(String(80))
    response: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    conflicts: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ArtifactRecord(Base):
    __tablename__ = "artifacts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(240))
    relative_path: Mapped[str] = mapped_column(Text)
    size_bytes: Mapped[int] = mapped_column(Integer)
    checksum: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)

    project: Mapped[ProjectRecord] = relationship(back_populates="artifacts")


class EventRecord(Base):
    __tablename__ = "events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    type: Mapped[str] = mapped_column(String(80), index=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), default="INFO", index=True)
    stage: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    classification: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    error_type: Mapped[str | None] = mapped_column(String(160), nullable=True)
    retry_strategy: Mapped[str | None] = mapped_column(String(80), nullable=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    diagnostics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ProjectAnalysisRecord(Base):
    __tablename__ = "project_analyses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True, index=True)
    complexity: Mapped[str] = mapped_column(String(24), index=True)
    required_features: Mapped[list[str]] = mapped_column(JSON, default=list)
    optional_features: Mapped[list[str]] = mapped_column(JSON, default=list)
    platform_targets: Mapped[list[str]] = mapped_column(JSON, default=list)
    data_requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    security_requirements: Mapped[list[str]] = mapped_column(JSON, default=list)
    external_apis: Mapped[list[str]] = mapped_column(JSON, default=list)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    deployment_expectations: Mapped[list[str]] = mapped_column(JSON, default=list)
    unknowns: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ProjectBlueprintRecord(Base):
    __tablename__ = "project_blueprints"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True, index=True)
    project: Mapped[str] = mapped_column(String(160), default="Application")
    architecture: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    services: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    ui: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    constraints: Mapped[list[str]] = mapped_column(JSON, default=list)
    risks: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ProjectRequirementRecord(Base):
    __tablename__ = "project_requirements"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(40), default="feature")
    priority: Mapped[str] = mapped_column(String(16), default="required")
    milestone_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ArchitectureDecisionRecord(Base):
    __tablename__ = "architecture_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    topic: Mapped[str] = mapped_column(String(120))
    decision: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    contract_version: Mapped[int] = mapped_column(Integer, default=1)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ProjectContractRecord(Base):
    __tablename__ = "project_contracts"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True, index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ContractRevisionRecord(Base):
    __tablename__ = "contract_revisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    proposal_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    previous_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    affected_tasks: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class MilestoneRecord(Base):
    __tablename__ = "milestones"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    key: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(180))
    goal: Mapped[str] = mapped_column(Text)
    description: Mapped[str] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer)
    dependencies: Mapped[list[str]] = mapped_column(JSON, default=list)
    deliverables: Mapped[list[str]] = mapped_column(JSON, default=list)
    acceptance_criteria: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    base_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    completion_commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    starting_contract_version: Mapped[int] = mapped_column(Integer, default=1)
    ending_contract_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ContractProposalRecord(Base):
    __tablename__ = "contract_proposals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    proposal_type: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(240))
    change: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    reason: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="PENDING", index=True)
    review_reason: Mapped[str] = mapped_column(Text, default="")
    reviewed_by: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)
    current_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    proposed_value: Mapped[Any | None] = mapped_column(JSON, nullable=True)
    expected_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)


class WorkerResultRecord(Base):
    __tablename__ = "worker_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    task_id: Mapped[str] = mapped_column(String(96), index=True)
    job_id: Mapped[str] = mapped_column(String(80), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    contract_version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(40), default="WORKER_RESULT_RECEIVED", index=True)
    result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    artifacts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    integrated_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class VerificationRecord(Base):
    __tablename__ = "verification_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    milestone_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scope: Mapped[str] = mapped_column(String(32), default="milestone")
    status: Mapped[str] = mapped_column(String(24), index=True)
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    commit: Mapped[str | None] = mapped_column(String(40), nullable=True)
    contract_version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class RepairRecord(Base):
    __tablename__ = "repair_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    milestone_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    failure_code: Mapped[str] = mapped_column(String(80))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    strategy: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(24), default="PLANNED", index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ComputePlanRecord(Base):
    __tablename__ = "compute_plans"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True, index=True)
    strategy: Mapped[str] = mapped_column(String(32), default="balanced")
    complexity: Mapped[str] = mapped_column(String(24))
    milestone_count: Mapped[int] = mapped_column(Integer)
    estimated_task_count: Mapped[int] = mapped_column(Integer)
    parallelizable_task_count: Mapped[int] = mapped_column(Integer)
    recommended_worker_count: Mapped[int] = mapped_column(Integer)
    selected_worker_count: Mapped[int] = mapped_column(Integer)
    recommended_roles: Mapped[list[str]] = mapped_column(JSON, default=list)
    model_requirements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class LeadDecisionRecord(Base):
    __tablename__ = "lead_decisions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    decision_type: Mapped[str] = mapped_column(String(64), index=True)
    summary: Mapped[str] = mapped_column(Text)
    rationale: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(80), default="deterministic")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ModelExecutionPlanRecord(Base):
    __tablename__ = "model_execution_plans"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="PLANNED", index=True)
    plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)


class ComputeObservationRecord(Base):
    __tablename__ = "compute_observations"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    project_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    plan_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True, index=True)
    model_id: Mapped[str] = mapped_column(String(200), index=True)
    runtime: Mapped[str] = mapped_column(String(80))
    quantization: Mapped[str] = mapped_column(String(40))
    gpu_name: Mapped[str] = mapped_column(String(200), default="Unknown GPU", index=True)
    vram_total_mb: Mapped[int] = mapped_column(Integer, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_limit: Mapped[int] = mapped_column(Integer, default=0)
    generated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    peak_vram_mb: Mapped[int] = mapped_column(Integer, default=0)
    elapsed_seconds: Mapped[float] = mapped_column(Float, default=0)
    result: Mapped[str] = mapped_column(String(32), index=True)
    failure_classification: Mapped[str | None] = mapped_column(
        String(80), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)


class ComputeIncidentRecord(Base):
    __tablename__ = "compute_incidents"

    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    root_failure_id: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    project_id: Mapped[str] = mapped_column(String(40), index=True)
    task_id: Mapped[str] = mapped_column(String(64), index=True)
    worker_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gpu_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    classification: Mapped[str] = mapped_column(String(80), index=True)
    state: Mapped[str] = mapped_column(String(32), index=True)
    attempts: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    recovery_action: Mapped[str | None] = mapped_column(String(80), nullable=True)
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True)


class ComputePolicyRecord(Base):
    __tablename__ = "compute_policies"

    project_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    policy: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, onupdate=utcnow)
