from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import uuid4

from aegaeon.database.models import ArtifactRecord
from aegaeon.database.session import Database


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
        safe_name = Path(filename).name
        if safe_name != filename or safe_name in {"", ".", ".."}:
            raise ValueError("unsafe artifact filename")
        artifact_id = f"artifact-{uuid4().hex}"
        artifact_dir = (self.projects_dir / project_id / "artifacts").resolve()
        if not artifact_dir.is_relative_to(self.projects_dir):
            raise ValueError("artifact path escapes data directory")
        artifact_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_dir / f"{artifact_id}-{safe_name}"
        if isinstance(content, dict):
            raw = json.dumps(content, indent=2, sort_keys=True).encode()
        elif isinstance(content, str):
            raw = content.encode()
        else:
            raw = content
        path.write_bytes(raw)
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
        with self.database.session() as session:
            session.add(record)
        return record

    def path_for(self, artifact: ArtifactRecord) -> Path:
        path = (self.projects_dir / artifact.relative_path).resolve()
        if not path.is_relative_to(self.projects_dir):
            raise ValueError("artifact path escapes data directory")
        return path
