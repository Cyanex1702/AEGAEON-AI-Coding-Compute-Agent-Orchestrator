from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from aegaeon.database.models import RateLimitBucketRecord
from aegaeon.database.session import Database


class PairingRateLimiter:
    """Persistent fixed-window limiter shared by controller processes using the same DB."""

    def __init__(self, database: Database, limit: int = 10, window_seconds: int = 60) -> None:
        self.database = database
        self.limit = limit
        self.window = timedelta(seconds=window_seconds)

    @staticmethod
    def _digest(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def allow(self, key: str, now: datetime | None = None) -> bool:
        current = now or datetime.now(UTC)
        key_hash = self._digest(key)
        with self.database.session() as session:
            bucket = session.get(RateLimitBucketRecord, key_hash)
            if bucket is None:
                session.add(
                    RateLimitBucketRecord(
                        key_hash=key_hash,
                        window_started_at=current,
                        attempts=1,
                    )
                )
                return True
            started = bucket.window_started_at
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if current - started >= self.window:
                bucket.window_started_at = current
                bucket.attempts = 1
                return True
            if bucket.attempts >= self.limit:
                return False
            bucket.attempts += 1
            return True
