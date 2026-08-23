from pathlib import Path


def test_health_models_and_project_creation(client, project_payload) -> None:
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["demo_mode"] is True
    assert len(client.get("/models").json()) >= 3

    response = client.post("/projects", json=project_payload)
    assert response.status_code == 201
    project = response.json()
    assert project["status"] == "draft"
    assert Path(project["workspace_path"]).is_dir()
    assert client.get(f"/projects/{project['id']}").status_code == 200


def test_worker_registration_and_heartbeat(client) -> None:
    with client.websocket_connect("/ws/worker?token=test-worker-token") as socket:
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


def test_worker_rejects_invalid_token(client) -> None:
    from starlette.websockets import WebSocketDisconnect

    try:
        with client.websocket_connect("/ws/worker?token=wrong"):
            raise AssertionError("connection should not be accepted")
    except WebSocketDisconnect as exc:
        assert exc.code == 1008
