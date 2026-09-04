from pathlib import Path

import pytest
from starlette.websockets import WebSocketDisconnect


def test_health_models_and_project_creation(client, project_payload) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["demo_mode"] is True
    assert client.get("/live").json() == {"status": "alive"}
    readiness = client.get("/ready")
    assert readiness.status_code == 200
    assert all(readiness.json()["checks"].values())
    assert len(client.get("/models").json()) >= 3

    response = client.post("/projects", json=project_payload)
    assert response.status_code == 201
    project = response.json()
    assert project["status"] == "draft"
    assert Path(project["workspace_path"]).is_dir()
    assert client.get(f"/projects/{project['id']}").status_code == 200


def test_worker_registration_and_heartbeat(client) -> None:
    with client.websocket_connect(
        "/ws/worker", headers={"x-aegaeon-worker-token": "test-worker-token"}
    ) as socket:
        socket.send_json(
            {
                "type": "worker.register",
                "worker_id": "worker-test",
                "hostname": "test-machine",
                "hardware": {
                    "cpu_cores": 8,
                    "ram_mb": 16384,
                    "gpu": {"available": False, "vram_mb": 0},
                },
                "capabilities": ["python", "git"],
                "models": [],
            }
        )
        assert socket.receive_json()["type"] == "worker.registered"
        socket.send_json(
            {
                "type": "worker.heartbeat",
                "worker_id": "worker-test",
                "status": "busy",
                "cpu_percent": 42,
                "ram_percent": 35,
            }
        )
        worker = client.get("/workers/worker-test").json()
        assert worker["status"] == "busy"
        assert worker["cpu_percent"] == 42
    assert client.get("/workers/worker-test").json()["status"] == "offline"


def test_duplicate_legacy_worker_cannot_evict_the_active_connection(client) -> None:
    registration = {
        "type": "worker.register",
        "worker_id": "worker-legacy-duplicate",
        "hostname": "legacy-colab",
        "hardware": {"cpu_cores": 2, "ram_mb": 4096, "gpu": {}},
        "capabilities": ["model.generate"],
        "models": [],
    }
    headers = {"x-aegaeon-worker-token": "test-worker-token"}
    with client.websocket_connect("/ws/worker", headers=headers) as active:
        active.send_json(registration)
        assert active.receive_json()["type"] == "worker.registered"

        with client.websocket_connect("/ws/worker", headers=headers) as duplicate:
            duplicate.send_json(registration)
            with pytest.raises(WebSocketDisconnect) as closed:
                duplicate.receive_json()
            assert closed.value.code == 4009

        active.send_json(
            {
                "type": "worker.heartbeat",
                "worker_id": registration["worker_id"],
                "status": "ready",
                "cpu_percent": 7,
            }
        )
        worker = client.get(f"/workers/{registration['worker_id']}").json()
        assert worker["status"] == "online"
        assert worker["cpu_percent"] == 7


def test_new_session_supersedes_old_session_once(client) -> None:
    headers = {"x-aegaeon-worker-token": "test-worker-token"}
    registration = {
        "type": "worker.register",
        "worker_id": "worker-session-fence",
        "hostname": "colab",
        "hardware": {"cpu_cores": 2, "ram_mb": 4096, "gpu": {}},
        "capabilities": ["model.generate"],
        "models": [],
    }
    with client.websocket_connect("/ws/worker", headers=headers) as old:
        old.send_json({**registration, "session_id": "old-session"})
        assert old.receive_json()["type"] == "worker.registered"
        with client.websocket_connect("/ws/worker", headers=headers) as new:
            new.send_json({**registration, "session_id": "new-session"})
            assert new.receive_json()["type"] == "worker.registered"
            with pytest.raises(WebSocketDisconnect) as superseded:
                old.receive_json()
            assert superseded.value.code == 4009


def test_worker_registration_coerces_fractional_gpu_memory(client) -> None:
    with client.websocket_connect(
        "/ws/worker", headers={"x-aegaeon-worker-token": "test-worker-token"}
    ) as socket:
        socket.send_json(
            {
                "type": "worker.register",
                "worker_id": "worker-fractional-memory",
                "hostname": "colab-t4",
                "hardware": {
                    "cpu_cores": 2,
                    "ram_mb": 12975,
                    "gpu": {
                        "available": True,
                        "name": "Tesla T4",
                        "vram_mb": 15360,
                        "allocated_mb": 5588.29,
                        "reserved_mb": 5600.0,
                        "free_mb": 9101.81,
                    },
                    "gpus": [
                        {
                            "available": True,
                            "name": "Tesla T4",
                            "vram_mb": 15360,
                            "allocated_mb": 5588.29,
                            "reserved_mb": 5600.0,
                            "free_mb": 9101.81,
                        }
                    ],
                    "cuda": True,
                },
                "capabilities": ["cuda", "model.generate"],
                "models": [],
            }
        )
        assert socket.receive_json()["type"] == "worker.registered"
        registered = client.get("/workers/worker-fractional-memory").json()
        assert registered["hardware"]["gpu"]["allocated_mb"] == 5588
        assert registered["hardware"]["gpu"]["reserved_mb"] == 5600
        assert registered["hardware"]["gpu"]["free_mb"] == 9102


def test_invalid_worker_registration_receives_protocol_error_and_close_frame(client) -> None:
    with client.websocket_connect(
        "/ws/worker", headers={"x-aegaeon-worker-token": "test-worker-token"}
    ) as socket:
        socket.send_json(
            {
                "type": "worker.register",
                "worker_id": "worker-invalid-telemetry",
                "hostname": "invalid-worker",
                "hardware": {
                    "gpu": {
                        "available": True,
                        "allocated_mb": "not-a-number",
                    }
                },
                "capabilities": ["model.generate"],
                "models": [],
            }
        )
        error = socket.receive_json()
        assert error["type"] == "protocol.error"
        assert error["message"] == "Invalid worker registration telemetry"
        try:
            socket.receive_json()
            raise AssertionError("worker socket should close after invalid registration")
        except WebSocketDisconnect as exc:
            assert exc.code == 1003


def test_worker_rejects_invalid_token(client) -> None:
    try:
        with client.websocket_connect("/ws/worker", headers={"x-aegaeon-worker-token": "wrong"}):
            raise AssertionError("connection should not be accepted")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008
