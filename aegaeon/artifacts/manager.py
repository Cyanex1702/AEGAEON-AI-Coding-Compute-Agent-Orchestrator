from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from aegaeon.database.models import ArtifactRecord
from aegaeon.database.session import Database
from aegaeon.portable import validate_portable_filename


class ArtifactStorageError(OSError):
    """A deterministic controller-side artifact persistence failure."""


class ArtifactManager:
    def __init__(self, database: Database, projects_dir: Path) -> None:
        self.database = database
        self.projects_dir = projects_dir.resolve()

    def create(
        self,
        project_id: str,
        task_id: str | None,
        artifact_type: str,
        filename: str,
        content: bytes | str | dict[str, str],
    ) -> ArtifactRecord:
        safe_name = validate_portable_filename(filename)
        artifact_id = f"artifact-{uuid4().hex}"
        artifact_dir = (self.projects_dir / project_id / "artifacts").resolve()
        if not artifact_dir.is_relative_to(self.projects_dir):
            raise ValueError("artifact path escapes data directory")
        try:
            artifact_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ArtifactStorageError(f"artifact directory creation failed: {exc}") from exc
        path = artifact_dir / f"{artifact_id}-{safe_name}"
        if isinstance(content, dict):
            raw = json.dumps(content, indent=2, sort_keys=True).encode()
        elif isinstance(content, str):
            raw = content.encode()
        else:
            raw = content
        try:
            temporary = path.with_suffix(path.suffix + ".tmp")
            temporary.write_bytes(raw)
            temporary.replace(path)
        except OSError as exc:
            raise ArtifactStorageError(f"artifact write failed: {exc}") from exc
        record = ArtifactRecord(
            id=artifact_id,
            project_id=project_id,
            task_id=task_id,
            type=artifact_type,
            filename=safe_name,
            relative_path=str(path.relative_to(self.projects_dir)),
            size_bytes=len(raw),
            checksum=hashlib.sha256(raw).hexdigest(),
        )
        try:
            with self.database.session() as session:
                session.add(record)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return record

    def path_for(self, artifact: ArtifactRecord) -> Path:
        path = (self.projects_dir / artifact.relative_path).resolve()
        if not path.is_relative_to(self.projects_dir):
            raise ValueError("artifact path escapes data directory")
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ArtifactStorageError(f"artifact is missing or unreadable: {exc}") from exc
        checksum = hashlib.sha256(raw).hexdigest()
        if len(raw) != artifact.size_bytes or checksum != artifact.checksum:
            raise ArtifactStorageError("artifact checksum or size does not match its record")
        return path
