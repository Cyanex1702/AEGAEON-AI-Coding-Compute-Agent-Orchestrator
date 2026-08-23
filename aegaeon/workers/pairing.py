from __future__ import annotations

import hashlib
import secrets
import string
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from aegaeon.database.models import WorkerPairingRecord
from aegaeon.database.session import Database


class PairingError(ValueError):
    pass


class WorkerPairingService:
    def __init__(self, database: Database, lifetime_seconds: int = 1800) -> None:
        self.database = database
        self.lifetime_seconds = lifetime_seconds

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def create(self, project_id: str, worker_name: str, model_id: str) -> tuple[str, datetime]:
        alphabet = string.ascii_uppercase + string.digits
        raw = "".join(secrets.choice(alphabet) for _ in range(8))
        code = f"AG-{raw[:4]}-{raw[4:]}"
        expires = datetime.now(UTC) + timedelta(seconds=self.lifetime_seconds)
        record = WorkerPairingRecord(
            id=f"pair-{secrets.token_hex(8)}",
            project_id=project_id,
            worker_name=worker_name,
            model_id=model_id,
            code_hash=self._digest(code),
            expires_at=expires,
        )
        with self.database.session() as session:
            session.add(record)
        return code, expires

    def redeem(self, code: str) -> tuple[str, str, str, datetime]:
        now = datetime.now(UTC)
        normalized = code.strip().upper()
        with self.database.session() as session:
            record = session.scalar(
                select(WorkerPairingRecord).where(
                    WorkerPairingRecord.code_hash == self._digest(normalized)
                )
            )
            if record is None or record.revoked_at is not None:
                raise PairingError("Pairing code is invalid or revoked")
            expires = record.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= now:
                raise PairingError("Pairing code expired; generate a fresh worker notebook")
            if record.redeemed_at is not None:
                raise PairingError("Pairing code was already used")
            token = secrets.token_urlsafe(32)
            credential_expires = now + timedelta(hours=12)
            record.redeemed_at = now
            record.credential_hash = self._digest(token)
            record.credential_expires_at = credential_expires
            worker_id = f"{record.worker_name}-{record.id[-6:]}"
            return token, worker_id, record.model_id, credential_expires

    def identity(self, token: str) -> tuple[str, str] | None:
        if not token:
            return None
        now = datetime.now(UTC)
        with self.database.session() as session:
            record = session.scalar(
                select(WorkerPairingRecord).where(
                    WorkerPairingRecord.credential_hash == self._digest(token),
                    WorkerPairingRecord.revoked_at.is_(None),
                )
            )
            if record is None or record.credential_expires_at is None:
                return None
            expires = record.credential_expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if expires <= now:
                return None
            return f"{record.worker_name}-{record.id[-6:]}", record.model_id

    def revoke_worker(self, worker_id: str) -> bool:
        now = datetime.now(UTC)
        revoked = False
        with self.database.session() as session:
            records = session.scalars(
                select(WorkerPairingRecord).where(WorkerPairingRecord.revoked_at.is_(None))
            ).all()
            for record in records:
                candidate = f"{record.worker_name}-{record.id[-6:]}"
                if candidate == worker_id:
                    record.revoked_at = now
                    revoked = True
        return revoked

    def authenticate(self, token: str) -> bool:
        return self.identity(token) is not None
