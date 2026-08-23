from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from aegaeon.database.models import EventRecord
from aegaeon.database.session import Database
from aegaeon.protocol.schemas import ActivityRead


class EventBus:
    """Persists observable events and fans them out to connected UI clients."""

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
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = EventRecord(
            id=f"event-{uuid4().hex}",
            type=event_type,
            project_id=project_id,
            task_id=task_id,
            worker_id=worker_id,
            message=message,
            payload=payload or {},
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
