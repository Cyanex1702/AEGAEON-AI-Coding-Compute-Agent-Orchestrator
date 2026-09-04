from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect

from aegaeon.api import create_app
from aegaeon.config import Settings
from aegaeon.database.models import WorkerNotebookRecord, WorkerRecord
from aegaeon.jobs.leases import JobLeaseManager
from aegaeon.protocol.schemas import JobAssign, JobRequirements


def payload(name: str) -> dict[str, object]:
    return {
        "name": name,
        "prompt": f"Build {name} as a small tested Python service.",
        "strategy": "auto",
        "maximum_retries": 1,
    }


def test_pin_move_and_order_survive_controller_restart(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'management.db'}",
        data_dir=tmp_path / "data",
        worker_token="management-token",
    )
    with TestClient(create_app(settings)) as client:
        project_a = client.post("/projects", json=payload("Project A")).json()
        project_b = client.post("/projects", json=payload("Project B")).json()
        project_c = client.post("/projects", json=payload("Project C")).json()
        pinned = client.put(f"/projects/{project_b['id']}/pin", json={"is_pinned": True})
        assert pinned.status_code == 200
        moved = client.post(f"/projects/{project_c['id']}/move", json={"direction": "up"})
        assert moved.status_code == 200
        ordered = client.get("/projects").json()
        assert [item["id"] for item in ordered] == [
            project_b["id"],
            project_c["id"],
            project_a["id"],
        ]
        assert ordered[0]["is_pinned"] is True

    with TestClient(create_app(settings)) as restarted:
        ordered = restarted.get("/projects").json()
        assert [item["id"] for item in ordered] == [
            project_b["id"],
            project_c["id"],
            project_a["id"],
        ]


def test_delete_removes_project_state_and_files_but_preserves_global_workers(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'delete.db'}",
        data_dir=tmp_path / "data",
        worker_token="delete-token",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        project = client.post("/projects", json=payload("Disposable")).json()
        project_root = Path(project["workspace_path"]).parent
        artifact = app.state.artifacts.create(project["id"], None, "json", "foundation.json", "{}")
        artifact_path = app.state.artifacts.path_for(artifact)
        with app.state.database.session() as session:
            session.add(
                WorkerRecord(
                    id="global-worker",
                    hostname="persistent-worker",
                    status="offline",
                )
            )

        response = client.delete(f"/projects/{project['id']}")
        assert response.status_code == 200
        assert response.json()["filesystem_removed"] is True
        assert not project_root.exists()
        assert not artifact_path.exists()
        assert client.get(f"/projects/{project['id']}").status_code == 404
        assert any(worker["id"] == "global-worker" for worker in client.get("/workers").json())

    with TestClient(create_app(settings)) as restarted:
        assert all(item["id"] != project["id"] for item in restarted.get("/projects").json())


def test_legacy_notebook_foreign_keys_are_migrated_and_do_not_block_delete(
    tmp_path,
) -> None:
    database_path = tmp_path / "legacy-notebooks.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE worker_notebooks (
                artifact_id VARCHAR(64) NOT NULL PRIMARY KEY
                    REFERENCES artifacts(id),
                project_id VARCHAR(40) NOT NULL
                    REFERENCES projects(id),
                controller_url TEXT NOT NULL,
                connection_generation INTEGER NOT NULL DEFAULT 0,
                stale BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    settings = Settings(
        database_url=f"sqlite:///{database_path}",
        data_dir=tmp_path / "data",
        worker_token="legacy-delete-token",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert inspect(app.state.database.engine).get_foreign_keys("worker_notebooks") == []
        project = client.post("/projects", json=payload("Legacy notebook cleanup")).json()
        artifact = app.state.artifacts.create(
            project["id"],
            None,
            "worker_notebook",
            "legacy-worker.ipynb",
            "{}",
        )
        with app.state.database.session() as session:
            session.add(
                WorkerNotebookRecord(
                    artifact_id=artifact.id,
                    project_id=project["id"],
                    controller_url="https://example.ngrok-free.dev",
                    connection_generation=0,
                    stale=True,
                )
            )

        response = client.delete(f"/projects/{project['id']}")
        assert response.status_code == 200
        assert client.get(f"/projects/{project['id']}").status_code == 404


def test_notebook_children_are_deleted_before_project_artifacts(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'notebook-delete.db'}",
        data_dir=tmp_path / "data",
        worker_token="notebook-delete-token",
    )
    app = create_app(settings)
    with TestClient(app) as client:
        project = client.post("/projects", json=payload("Notebook child order")).json()
        artifact = app.state.artifacts.create(
            project["id"], None, "worker_notebook", "worker.ipynb", "{}"
        )
        with app.state.database.session() as session:
            session.add(
                WorkerNotebookRecord(
                    artifact_id=artifact.id,
                    project_id=project["id"],
                    controller_url="https://example.ngrok-free.dev",
                )
            )
        assert client.delete(f"/projects/{project['id']}").status_code == 200


def test_active_project_deletion_is_blocked(client) -> None:
    project = client.post("/projects", json=payload("Active deletion guard")).json()
    with client.app.state.database.session() as session:
        record = session.get(
            __import__("aegaeon.database.models", fromlist=["ProjectRecord"]).ProjectRecord,
            project["id"],
        )
        assert record is not None
        record.status = "running"
    response = client.delete(f"/projects/{project['id']}")
    assert response.status_code == 409
    assert client.get(f"/projects/{project['id']}").status_code == 200


def test_api_and_event_timestamps_are_utc_and_diagnostics_are_redacted(client) -> None:
    project = client.post("/projects", json=payload("UTC diagnostics")).json()
    assert project["created_at"].endswith("Z") or project["created_at"].endswith("+00:00")

    event = asyncio.run(
        client.app.state.events.publish(
            "task.failed",
            "Artifact storage failed",
            project_id=project["id"],
            payload={
                "failure_stage": "ARTIFACT_STORAGE",
                "failure_classification": "INVALID_ARTIFACT_FILENAME",
                "error_type": "PortableFilenameError",
                "error_message": "invalid artifact",
                "retry_strategy": "DO_NOT_REGENERATE",
                "diagnostics": {
                    "Authorization": "Bearer top-secret",
                    "traceback": "request?token=secret-value",
                },
            },
        )
    )
    assert event["severity"] == "ERROR"
    assert event["stage"] == "ARTIFACT_STORAGE"
    assert event["classification"] == "INVALID_ARTIFACT_FILENAME"
    assert event["created_at"].endswith("Z") or event["created_at"].endswith("+00:00")
    assert event["diagnostics"]["Authorization"] == "[REDACTED]"
    assert "secret-value" not in event["diagnostics"]["traceback"]

    nested = asyncio.run(
        client.app.state.events.publish(
            "job.failed",
            "worker generation failed",
            project_id=project["id"],
            payload={
                "job_id": "job-nested",
                "diagnostics": {
                    "stage": "PARSING",
                    "classification": "INVALID_JSON",
                    "error_type": "ValueError",
                    "retry_strategy": "REQUEST_STRUCTURED_CORRECTION",
                },
            },
        )
    )
    assert nested["job_id"] == "job-nested"
    assert nested["stage"] == "PARSING"
    assert nested["classification"] == "INVALID_JSON"
    assert nested["error_type"] == "ValueError"
    assert nested["retry_strategy"] == "REQUEST_STRUCTURED_CORRECTION"


def test_job_failure_fields_are_persisted_separately(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'jobs.db'}",
        data_dir=tmp_path / "data",
    )
    app = create_app(settings)
    with TestClient(app):
        project = app.state.projects.create(
            __import__("aegaeon.protocol.schemas", fromlist=["ProjectCreate"]).ProjectCreate(
                name="Job fields", prompt="Build a small service"
            )
        )
        run_id = "run-test"
        with app.state.database.session() as session:
            project_record = session.get(
                __import__("aegaeon.database.models", fromlist=["ProjectRecord"]).ProjectRecord,
                project.id,
            )
            assert project_record is not None
            project_record.active_run_id = run_id
            task_type = __import__("aegaeon.database.models", fromlist=["TaskRecord"]).TaskRecord
            session.add(
                task_type(
                    id="task-test",
                    project_id=project.id,
                    run_id=run_id,
                    key="foundation",
                    title="Foundation",
                    description="Build it",
                )
            )
        leases = JobLeaseManager(app.state.database)
        job = JobAssign(
            job_id="job-test",
            run_id=run_id,
            project_id=project.id,
            task_id="task-test",
            agent_role="backend",
            requirements=JobRequirements(),
            instructions="Build it",
        )
        leases.queue(job, 0)
        leases.fail(
            job.id if hasattr(job, "id") else job.job_id,
            "write failed",
            diagnostics={
                "stage": "ARTIFACT_STORAGE",
                "classification": "ARTIFACT_STORAGE_FAILURE",
                "error_type": "OSError",
                "retry_strategy": "RETRY_CONTROLLER_STORAGE_ONLY",
            },
        )
        persisted = leases.get(job.job_id)
        assert persisted.failure_stage == "ARTIFACT_STORAGE"
        assert persisted.failure_classification == "ARTIFACT_STORAGE_FAILURE"
        assert persisted.error_type == "OSError"
        assert persisted.failed_at is not None and persisted.failed_at.tzinfo is not None
