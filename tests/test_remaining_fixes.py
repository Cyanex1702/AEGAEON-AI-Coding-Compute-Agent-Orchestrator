from __future__ import annotations

import hashlib
import zipfile
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from aegaeon.agents.deterministic import BrokeBoyPlanner
from aegaeon.api import create_app
from aegaeon.config import Settings
from aegaeon.database.session import Database
from aegaeon.failures import FailureCode, normalize_failure_code
from aegaeon.models.results import ChangeOperation
from aegaeon.protocol.schemas import JobRequirements
from aegaeon.remote.providers import NgrokTunnelProvider
from aegaeon.security import PairingRateLimiter
from aegaeon.workers.scheduler import CapabilityScheduler, WorkerCapacityStatus
from scripts.build_source_release import build_release


def _worker(status: str) -> dict[str, object]:
    return {
        "id": f"worker-{status}",
        "status": status,
        "capabilities": ["model.generate"],
        "hardware": {
            "ram_mb": 16_384,
            "gpu": {"available": True, "vram_mb": 15_000},
        },
        "models": [{"id": "code-model", "loaded": True, "status": "ready"}],
    }


def test_capacity_assessment_distinguishes_busy_offline_and_missing() -> None:
    scheduler = CapabilityScheduler()
    requirements = JobRequirements(
        capabilities=["model.generate"],
        gpu=True,
        model="code-model",
    )
    assert scheduler.assess([_worker("busy")], requirements).status == (
        WorkerCapacityStatus.COMPATIBLE_WORKER_BUSY
    )
    assert scheduler.assess([_worker("offline")], requirements).status == (
        WorkerCapacityStatus.WORKER_OFFLINE
    )
    assert scheduler.assess([], requirements).status == (
        WorkerCapacityStatus.NO_COMPATIBLE_WORKER_EXISTS
    )
    assert scheduler.assess([_worker("online")], requirements).status == (
        WorkerCapacityStatus.WORKER_AVAILABLE
    )


def test_worker_count_changes_parallelism_not_decomposition() -> None:
    planner = BrokeBoyPlanner()
    one = planner.plan("Build a tested application", worker_count=1)
    two = planner.plan("Build a tested application", worker_count=2)
    assert [task.id for task in one.tasks] == [task.id for task in two.tasks]
    assert {task.id for task in one.tasks if task.parallelizable} == {"backend", "frontend"}
    assert "implementation" not in {task.id for task in one.tasks}


def test_failure_classifier_covers_recovery_classes() -> None:
    assert normalize_failure_code("", "CUDA out of memory") == FailureCode.CUDA_OOM
    assert normalize_failure_code("", "invalid JSON response") == FailureCode.INVALID_JSON
    assert normalize_failure_code("", "unsafe generated path") == FailureCode.UNSAFE_PATH
    assert normalize_failure_code("", "model output truncated") == FailureCode.OUTPUT_TRUNCATED
    assert normalize_failure_code("", "worker disconnected") == FailureCode.WORKER_DISCONNECTED


def test_persistent_rate_limiter_is_shared(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'rate.db'}")
    database.create_all()
    first = PairingRateLimiter(database, limit=2)
    second = PairingRateLimiter(database, limit=2)
    now = datetime.now(UTC)
    assert first.allow("198.51.100.1", now)
    assert second.allow("198.51.100.1", now)
    assert not first.allow("198.51.100.1", now)
    assert first.allow("198.51.100.1", now + timedelta(seconds=61))
    database.close()


def test_query_worker_token_is_rejected_and_control_token_protects_local_api(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'auth.db'}",
        data_dir=tmp_path / "data",
        worker_token="test-worker-token",
        allow_development_worker_token=True,
        control_token="control-secret",
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/projects").status_code == 401
        assert (
            client.get("/projects", headers={"authorization": "Bearer control-secret"}).status_code
            == 200
        )
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/worker?token=test-worker-token") as socket:
                socket.receive_json()


def test_change_operations_create_update_rename_delete(client, project_payload) -> None:
    project = client.post("/projects", json=project_payload).json()
    manager = client.app.state.projects
    manager.integrate_changes(
        project["id"],
        "changes",
        "exercise safe operations",
        [
            ChangeOperation(operation="create", path="src/first.txt", content="one"),
            ChangeOperation(operation="update", path="README.md", content="# Updated\n"),
        ],
    )
    manager.integrate_changes(
        project["id"],
        "rename-delete",
        "rename and delete",
        [
            ChangeOperation(operation="rename", path="src/renamed.txt", from_path="src/first.txt"),
            ChangeOperation(operation="delete", path="README.md"),
        ],
    )
    repo = manager.repo_path(project["id"])
    assert not (repo / "src" / "first.txt").exists()
    assert (repo / "src" / "renamed.txt").read_text(encoding="utf-8") == "one"
    assert not (repo / "README.md").exists()


def test_ngrok_uses_only_owned_tunnel_when_multiple_exist() -> None:
    provider = NgrokTunnelProvider()
    provider._tunnel_name = "aegaeon-owned"
    provider._target = "127.0.0.1:8000"
    tunnels = [
        {
            "name": "some-other-app",
            "proto": "https",
            "public_url": "https://wrong.example",
            "config": {"addr": "http://127.0.0.1:9999"},
        },
        {
            "name": "aegaeon-owned",
            "proto": "https",
            "public_url": "https://right.example/",
            "config": {"addr": "http://127.0.0.1:8000"},
        },
    ]
    assert provider._owned_https_url(tunnels) == "https://right.example"


def test_ngrok_adopts_only_an_existing_aegaeon_tunnel_for_the_same_target() -> None:
    provider = NgrokTunnelProvider()
    provider._target = "127.0.0.1:8000"
    tunnels = [
        {
            "name": "another-app",
            "proto": "https",
            "public_url": "https://wrong.example",
            "config": {"addr": "http://127.0.0.1:8000"},
        },
        {
            "name": "aegaeon-stale-controller",
            "proto": "https",
            "public_url": "https://right.example/",
            "config": {"addr": "http://127.0.0.1:8000"},
        },
    ]

    assert provider._adoptable_https_tunnel(tunnels) == (
        "aegaeon-stale-controller",
        "https://right.example",
    )


def test_ngrok_reports_endpoint_conflicts_separately_from_invalid_tokens() -> None:
    assert "already online" in NgrokTunnelProvider._stopped_error("ERR_NGROK_334")
    invalid = NgrokTunnelProvider._stopped_error("authentication failed ERR_NGROK_105")
    assert "rejected the saved credential" in invalid
    assert "ERR_NGROK_105" in invalid


@pytest.mark.asyncio
async def test_stopping_an_adopted_tunnel_clears_local_provider_state() -> None:
    provider = NgrokTunnelProvider()
    provider._target = "127.0.0.1:8000"
    provider._tunnel_name = "aegaeon-adopted"
    provider._adopted_url = "https://adopted.example"

    await provider.stop()

    assert provider.running is False
    assert provider._target is None
    assert provider._tunnel_name is None


def test_source_release_is_deterministic_and_excludes_runtime_dependencies(tmp_path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "app.py").write_text("print('ok')\n", encoding="utf-8")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "index.js").write_text("ignored", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "state.db").write_bytes(b"ignored")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    build_release(root, first)
    build_release(root, second)
    assert (
        hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    )
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["app.py"]


def test_websocket_origin_boundaries(client) -> None:
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/ui", headers={"origin": "https://evil.example"}
        ) as socket:
            socket.receive_json()
    with client.websocket_connect("/ws/ui", headers={"origin": "http://127.0.0.1:3000"}) as socket:
        assert socket.receive_json()["type"] == "system.ready"
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws/worker",
            headers={
                "origin": "http://127.0.0.1:3000",
                "x-aegaeon-worker-token": "test-worker-token",
            },
        ) as socket:
            socket.receive_json()
