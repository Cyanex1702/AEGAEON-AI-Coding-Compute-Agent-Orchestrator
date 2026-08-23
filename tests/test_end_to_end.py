import time
from pathlib import Path


def test_local_demo_completes_project_with_real_tests(client, project_payload) -> None:
    project = client.post("/projects", json=project_payload).json()
    response = client.post(f"/projects/{project['id']}/run")
    assert response.status_code == 202

    deadline = time.monotonic() + 20
    current = project
    while time.monotonic() < deadline:
        current = client.get(f"/projects/{project['id']}").json()
        if current["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)

    assert current["status"] == "completed"
    assert current["progress"] == 100
    assert all(task["status"] == "COMPLETED" for task in current["tasks"])
    verification = next(task for task in current["tasks"] if task["key"] == "verification")
    assert verification["result"]["exit_code"] == 0

    repo = Path(current["workspace_path"])
    assert (repo / "app" / "main.py").is_file()
    assert (repo / "tests" / "test_api.py").is_file()
    assert len(client.get(f"/projects/{project['id']}/artifacts").json()) >= 5
    events = client.get(f"/projects/{project['id']}/events").json()
    assert any(event["type"] == "project.completed" for event in events)
