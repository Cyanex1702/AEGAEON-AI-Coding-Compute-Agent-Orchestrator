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


def test_complex_demo_executes_the_canonical_plan_without_late_acceptance_failure(
    client, project_payload
) -> None:
    prompt = """Build an advanced scientific calculator with:
- a modern responsive UI
- trigonometric and logarithmic functions
- memory and history
- keyboard support
- error handling
- automated tests
- security and deployment readiness
"""
    project = client.post(
        "/projects",
        json={**project_payload, "name": "Canonical plan coverage", "prompt": prompt},
    ).json()
    assert client.post(f"/projects/{project['id']}/run").status_code == 202

    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        current = client.get(f"/projects/{project['id']}").json()
        if current["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)

    assert current["status"] == "completed"
    assert any(task["key"] == "foundation" for task in current["tasks"])
    repo = Path(current["workspace_path"])
    assert (repo / "pyproject.toml").is_file()
    assert (repo / "README.md").is_file()
    orchestration = client.get(f"/projects/{project['id']}/orchestration").json()
    assert all(item["status"] == "COMPLETED" for item in orchestration["milestones"])
    assert all(item["status"] == "VERIFIED" for item in orchestration["requirements"])
    final = next(item for item in orchestration["verifications"] if item["scope"] == "final")
    assert final["status"] == "PASSED"
