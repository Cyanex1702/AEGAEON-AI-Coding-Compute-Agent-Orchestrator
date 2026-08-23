from __future__ import annotations

from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictResult(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GeneratedFile(StrictResult):
    path: str = Field(min_length=1, max_length=240)
    content: str

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if "\\" in value:
            raise ValueError("generated paths must use forward slashes")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
            raise ValueError("generated path must stay inside the project repository")
        if path.parts and path.parts[0].startswith("."):
            raise ValueError("generated hidden control files are not allowed")
        return path.as_posix()


class GeneratedChangeSet(StrictResult):
    summary: str = Field(min_length=1, max_length=1_000)
    files: list[GeneratedFile] = Field(min_length=1, max_length=200)
    test_commands: list[str]
    notes: list[str]

    @model_validator(mode="after")
    def unique_paths(self) -> GeneratedChangeSet:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("generated file paths must be unique")
        return self

    def as_file_map(self) -> dict[str, str]:
        return {item.path: item.content for item in self.files}


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
