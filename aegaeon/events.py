from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import or_, select

from aegaeon.database.models import EventRecord, ProjectRecord
from aegaeon.database.session import Database
from aegaeon.protocol.schemas import ActivityRead
from aegaeon.security.redaction import redact_diagnostics


class EventBus:
    """Persists structured, UTC event truth and fans it out to connected UIs."""

    def __init__(self, database: Database) -> None:
        self.database = database
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()

    async def publish(
        self,
        event_type: str,
        message: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        worker_id: str | None = None,
        run_id: str | None = None,
        job_id: str | None = None,
        payload: dict[str, Any] | None = None,
        severity: str | None = None,
        stage: str | None = None,
        classification: str | None = None,
        error_type: str | None = None,
        retry_strategy: str | None = None,
        attempt: int | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if project_id and run_id is None:
            with self.database.session() as session:
                project = session.get(ProjectRecord, project_id)
                run_id = project.active_run_id if project else None
        safe_payload = redact_diagnostics(payload or {})
        nested_diagnostics = safe_payload.get("diagnostics")
        if not isinstance(nested_diagnostics, dict):
            nested_diagnostics = {}
        if job_id is None:
            candidate = safe_payload.get("job_id")
            job_id = str(candidate) if candidate else None
        resolved_severity = (severity or self._severity(event_type)).upper()
        resolved_stage = (
            stage
            or safe_payload.get("failure_stage")
            or safe_payload.get("stage")
            or nested_diagnostics.get("failure_stage")
            or nested_diagnostics.get("stage")
        )
        resolved_classification = (
            classification
            or safe_payload.get("failure_classification")
            or safe_payload.get("classification")
            or safe_payload.get("failure_code")
            or nested_diagnostics.get("failure_classification")
            or nested_diagnostics.get("classification")
        )
        resolved_error_type = (
            error_type or safe_payload.get("error_type") or nested_diagnostics.get("error_type")
        )
        resolved_retry = (
            retry_strategy
            or safe_payload.get("retry_strategy")
            or nested_diagnostics.get("retry_strategy")
        )
        resolved_attempt = attempt if attempt is not None else safe_payload.get("attempt")
        safe_diagnostics = redact_diagnostics(
            diagnostics
            or safe_payload.get("diagnostics")
            or (safe_payload if resolved_severity == "ERROR" else {})
        )
        event = EventRecord(
            id=f"event-{uuid4().hex}",
            type=event_type,
            project_id=project_id,
            task_id=task_id,
            run_id=run_id,
            job_id=job_id,
            worker_id=worker_id,
            message=message,
            severity=resolved_severity,
            stage=str(resolved_stage).upper() if resolved_stage else None,
            classification=str(resolved_classification) if resolved_classification else None,
            error_type=str(resolved_error_type) if resolved_error_type else None,
            retry_strategy=str(resolved_retry) if resolved_retry else None,
            attempt=int(resolved_attempt) if isinstance(resolved_attempt, (int, float)) else None,
            diagnostics=safe_diagnostics,
            payload=safe_payload,
            created_at=datetime.now(UTC),
        )
        with self.database.session() as session:
            session.add(event)
        serialized = ActivityRead.model_validate(event).model_dump(mode="json")
        for queue in tuple(self._subscribers):
            try:
                queue.put_nowait(serialized)
            except asyncio.QueueFull:
                self._subscribers.discard(queue)
        return serialized

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    def recent(self, project_id: str | None = None, limit: int = 100) -> list[ActivityRead]:
        with self.database.session() as session:
            statement = select(EventRecord)
            if project_id:
                statement = statement.where(EventRecord.project_id == project_id)
            statement = statement.order_by(EventRecord.created_at.desc()).limit(limit)
            return [ActivityRead.model_validate(item) for item in session.scalars(statement)]

    def since(self, event_id: str, limit: int = 500) -> list[ActivityRead]:
        """Replay events after a persisted cursor in chronological order."""
        with self.database.session() as session:
            cursor = session.get(EventRecord, event_id)
            if cursor is None:
                return []
            statement = (
                select(EventRecord)
                .where(
                    or_(
                        EventRecord.created_at > cursor.created_at,
                        (
                            (EventRecord.created_at == cursor.created_at)
                            & (EventRecord.id > cursor.id)
                        ),
                    )
                )
                .order_by(EventRecord.created_at.asc(), EventRecord.id.asc())
                .limit(limit)
            )
            return [ActivityRead.model_validate(item) for item in session.scalars(statement)]

    @staticmethod
    def _severity(event_type: str) -> str:
        lowered = event_type.lower()
        if any(value in lowered for value in ("failed", "error", "rejected")):
            return "ERROR"
        if any(value in lowered for value in ("retry", "requeue", "conflict", "interrupt")):
            return "WARNING"
        if any(value in lowered for value in ("completed", "created", "passed", "stored")):
            return "SUCCESS"
        if any(value in lowered for value in ("started", "assigned", "running", "waiting")):
            return "RUNNING"
        return "INFO"
