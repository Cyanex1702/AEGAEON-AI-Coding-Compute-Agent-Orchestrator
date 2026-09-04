from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictResult(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _safe_path(value: str) -> str:
    if "\\" in value:
        raise ValueError("generated paths must use forward slashes")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        raise ValueError("generated path must stay inside the project repository")
    if path.parts and path.parts[0].startswith("."):
        raise ValueError("generated hidden control files are not allowed")
    return path.as_posix()


class GeneratedFile(StrictResult):
    path: str = Field(min_length=1, max_length=240)
    content: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return _safe_path(value)


class ChangeOperation(StrictResult):
    operation: Literal["create", "update", "delete", "rename"]
    path: str = Field(min_length=1, max_length=240)
    content: str | None = None
    from_path: str | None = Field(default=None, max_length=240)

    @field_validator("path", "from_path")
    @classmethod
    def validate_paths(cls, value: str | None) -> str | None:
        return _safe_path(value) if value is not None else None

    @model_validator(mode="after")
    def validate_operation(self) -> ChangeOperation:
        if self.operation in {"create", "update"} and self.content is None:
            raise ValueError(f"{self.operation} requires content")
        if self.operation == "rename" and not self.from_path:
            raise ValueError("rename requires from_path")
        if self.operation in {"delete", "rename"} and self.content is not None:
            raise ValueError(f"{self.operation} does not accept content")
        if self.operation != "rename" and self.from_path is not None:
            raise ValueError("from_path is only valid for rename")
        if self.operation == "rename" and self.from_path == self.path:
            raise ValueError("rename source and target must differ")
        return self


class WorkerChange(ChangeOperation):
    """Canonical worker mutation shape shared by every execution runtime."""


class AcceptanceEvidence(StrictResult):
    criterion: str = Field(min_length=1, max_length=500)
    evidence: str = Field(min_length=1, max_length=2_000)


class WorkerRisk(StrictResult):
    description: str = Field(min_length=1, max_length=2_000)
    severity: Literal["low", "medium", "high", "critical"] = "medium"


class WorkerBlocker(StrictResult):
    description: str = Field(min_length=1, max_length=2_000)
    target: str | None = Field(default=None, max_length=240)


class ContractProposal(StrictResult):
    proposal_id: str | None = Field(default=None, max_length=80)
    type: Literal[
        "add_field",
        "add_entity",
        "add_api",
        "add_service",
        "add_dependency",
        "rename",
        "remove",
        "change_type",
        "other",
    ]
    target: str = Field(min_length=1, max_length=240)
    current_value: object | None = None
    proposed_value: object | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    change: dict[str, object] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def normalize_proposed_value(self) -> ContractProposal:
        if self.proposed_value is None and self.change:
            self.proposed_value = self.change
        if self.proposed_value is None and self.type.startswith("add_"):
            raise ValueError("additive contract proposals require a proposed_value or change")
        return self


class GeneratedChangeSet(StrictResult):
    summary: str = Field(min_length=1, max_length=1_000)
    files: list[GeneratedFile] = Field(default_factory=list, max_length=200)
    changes: list[ChangeOperation] = Field(default_factory=list, max_length=200)
    test_commands: list[str]
    notes: list[str]
    contract_proposals: list[ContractProposal] = Field(default_factory=list, max_length=25)
    risks: list[str] = Field(default_factory=list, max_length=50)
    blocked_by: list[str] = Field(default_factory=list, max_length=50)
    acceptance_evidence: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_paths(self) -> GeneratedChangeSet:
        if not self.files and not self.changes:
            raise ValueError("a generated change set must contain files or changes")
        paths = [item.path for item in self.files]
        targets = [item.path for item in self.changes]
        if len(paths) != len(set(paths)) or len(targets) != len(set(targets)):
            raise ValueError("generated change targets must be unique")
        if set(paths) & set(targets):
            raise ValueError("a path cannot appear in both legacy files and change operations")
        return self

    def as_file_map(self) -> dict[str, str]:
        result = {item.path: item.content for item in self.files}
        for item in self.changes:
            if item.operation in {"create", "update"} and item.content is not None:
                result[item.path] = item.content
        return result

    def as_operations(self) -> list[ChangeOperation]:
        legacy = [
            ChangeOperation(operation="update", path=item.path, content=item.content)
            for item in self.files
        ]
        return [*legacy, *self.changes]


class WorkerResult(GeneratedChangeSet):
    """End-to-end worker result protocol used by local, Colab, and controller runtimes."""


class ReviewReport(StrictResult):
    summary: str = Field(min_length=1, max_length=2_000)
    severity: Literal["none", "low", "medium", "high", "critical"]
    root_cause: str = Field(min_length=1, max_length=4_000)
    recommended_changes: list[str]
    affected_files: list[str]


class FinalReviewReport(StrictResult):
    summary: str = Field(min_length=1, max_length=2_000)
    approved: bool
    severity: Literal["none", "low", "medium", "high", "critical"]
    findings: list[str]


class ProviderStatus(StrictResult):
    provider: str
    model: str
    base_url: str
    mode: Literal["demo", "model"]
    configured: bool
    reachable: bool | None
    structured_output_mode: str
    error: str | None
