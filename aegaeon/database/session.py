from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from aegaeon.database.models import Base


class Database:
    """SQLAlchemy unit-of-work wrapper with SQLite integrity and migrations."""

    def __init__(self, url: str) -> None:
        self.url = url
        if url.startswith("sqlite:///"):
            raw_path = url.removeprefix("sqlite:///")
            if raw_path != ":memory:":
                Path(raw_path).parent.mkdir(parents=True, exist_ok=True)
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        self.engine = create_engine(url, connect_args=connect_args, pool_pre_ping=True)
        if url.startswith("sqlite"):
            event.listen(self.engine, "connect", self._enable_sqlite_integrity)
        self.session_factory = sessionmaker(
            bind=self.engine, class_=Session, expire_on_commit=False, autoflush=False
        )

    @staticmethod
    def _enable_sqlite_integrity(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()

    def create_all(self) -> None:
        Base.metadata.create_all(self.engine)
        self._migrate()

    def _migrate(self) -> None:
        """Apply small, idempotent schema migrations to existing local databases."""

        if not self.url.startswith("sqlite"):
            return
        migrations: dict[str, dict[str, str]] = {
            "projects": {
                "active_run_id": "VARCHAR(64)",
                "is_pinned": "BOOLEAN NOT NULL DEFAULT 0",
                "sort_order": "INTEGER NOT NULL DEFAULT 0",
            },
            "tasks": {
                "run_id": "VARCHAR(64)",
                "milestone_id": "VARCHAR(64)",
                "base_commit": "VARCHAR(40)",
                "contract_version": "INTEGER NOT NULL DEFAULT 1",
                "allowed_files": "JSON NOT NULL DEFAULT '[\"**/*\"]'",
                "acceptance_criteria": "JSON NOT NULL DEFAULT '[]'",
            },
            "events": {
                "run_id": "VARCHAR(64)",
                "job_id": "VARCHAR(80)",
                "severity": "VARCHAR(16) NOT NULL DEFAULT 'INFO'",
                "stage": "VARCHAR(80)",
                "classification": "VARCHAR(80)",
                "error_type": "VARCHAR(160)",
                "retry_strategy": "VARCHAR(80)",
                "attempt": "INTEGER",
                "diagnostics": "JSON NOT NULL DEFAULT '{}'",
            },
            "jobs": {
                "run_id": "VARCHAR(64)",
                "attempt_id": "VARCHAR(80)",
                "diagnostics": "JSON NOT NULL DEFAULT '{}'",
                "failure_stage": "VARCHAR(80)",
                "failure_classification": "VARCHAR(80)",
                "error_type": "VARCHAR(160)",
                "retry_strategy": "VARCHAR(80)",
                "failed_at": "DATETIME",
                "logs": "JSON NOT NULL DEFAULT '[]'",
                "lease_token": "VARCHAR(64) NOT NULL DEFAULT ''",
                "connection_generation": "INTEGER NOT NULL DEFAULT 0",
                "message_sequence": "INTEGER NOT NULL DEFAULT 0",
                "payload": "JSON NOT NULL DEFAULT '{}'",
                "offer_expires_at": "DATETIME",
                "acknowledged_at": "DATETIME",
                "result_uploaded_at": "DATETIME",
                "cancel_requested_at": "DATETIME",
                "result_hash": "VARCHAR(64)",
            },
            "project_blueprints": {
                "project": "VARCHAR(160) NOT NULL DEFAULT 'Application'",
                "ui": "JSON NOT NULL DEFAULT '{}'",
                "state": "JSON NOT NULL DEFAULT '{}'",
            },
            "contract_proposals": {
                "current_value": "JSON",
                "proposed_value": "JSON",
                "expected_revision": "INTEGER",
            },
            "contract_revisions": {
                "previous_version": "INTEGER",
                "affected_tasks": "JSON NOT NULL DEFAULT '[]'",
            },
            "workers": {
                "current_job_id": "VARCHAR(80)",
                "stage": "VARCHAR(80)",
                "progress_percent": "FLOAT NOT NULL DEFAULT 0",
                "role_preferences": "JSON NOT NULL DEFAULT '[]'",
                "session_id": "VARCHAR(80) NOT NULL DEFAULT ''",
                "connection_generation": "INTEGER NOT NULL DEFAULT 0",
                "progress_sequence": "INTEGER NOT NULL DEFAULT 0",
                "telemetry_at": "DATETIME",
                "draining": "BOOLEAN NOT NULL DEFAULT 0",
            },
        }
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, "
                    "applied_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
            )
            inspector = inspect(connection)
            for table_name, additions in migrations.items():
                existing = {column["name"] for column in inspector.get_columns(table_name)}
                for column_name, definition in additions.items():
                    if column_name not in existing:
                        connection.execute(
                            text(
                                f'ALTER TABLE "{table_name}" ADD COLUMN '
                                f'"{column_name}" {definition}'
                            )
                        )
            connection.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (2)"))
            connection.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (3)"))
            connection.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (4)"))
            version_five = connection.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = 5")
            ).scalar()
            if not version_five:
                project_ids = connection.execute(
                    text("SELECT id FROM projects ORDER BY created_at DESC, id")
                ).scalars()
                for position, project_id in enumerate(project_ids):
                    connection.execute(
                        text("UPDATE projects SET sort_order = :position WHERE id = :project_id"),
                        {"position": position, "project_id": project_id},
                    )
            connection.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (5)"))
            connection.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (6)"))
            version_seven = connection.execute(
                text("SELECT 1 FROM schema_migrations WHERE version = 7")
            ).scalar()
            if not version_seven:
                notebook_foreign_keys = inspect(connection).get_foreign_keys("worker_notebooks")
                if notebook_foreign_keys:
                    connection.execute(
                        text("ALTER TABLE worker_notebooks RENAME TO worker_notebooks_legacy_v7")
                    )
                    connection.execute(
                        text(
                            "CREATE TABLE worker_notebooks ("
                            "artifact_id VARCHAR(64) NOT NULL PRIMARY KEY, "
                            "project_id VARCHAR(40) NOT NULL, "
                            "controller_url TEXT NOT NULL, "
                            "connection_generation INTEGER NOT NULL, "
                            "stale BOOLEAN NOT NULL, "
                            "created_at DATETIME NOT NULL)"
                        )
                    )
                    connection.execute(
                        text(
                            "INSERT INTO worker_notebooks "
                            "(artifact_id, project_id, controller_url, "
                            "connection_generation, stale, created_at) "
                            "SELECT artifact_id, project_id, controller_url, "
                            "connection_generation, stale, created_at "
                            "FROM worker_notebooks_legacy_v7"
                        )
                    )
                    connection.execute(text("DROP TABLE worker_notebooks_legacy_v7"))
                    connection.execute(
                        text(
                            "CREATE INDEX ix_worker_notebooks_project_id "
                            "ON worker_notebooks (project_id)"
                        )
                    )
                    connection.execute(
                        text("CREATE INDEX ix_worker_notebooks_stale ON worker_notebooks (stale)")
                    )
            connection.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (7)"))
            connection.execute(text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (8)"))

    def close(self) -> None:
        self.engine.dispose()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
