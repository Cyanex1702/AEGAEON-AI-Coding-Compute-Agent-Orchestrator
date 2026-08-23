from __future__ import annotations

import time

from fastapi.testclient import TestClient

from aegaeon.api import create_app
from aegaeon.config import Settings
from aegaeon.models.results import FinalReviewReport, GeneratedChangeSet, GeneratedFile
from aegaeon.protocol.schemas import PlanTask, ProjectPlan


class ParallelModelRuntime:
    name = "model:parallel-test"

    async def plan(self, prompt: str, repository_context: str) -> ProjectPlan:
        return ProjectPlan(
            project_summary="Parallel arithmetic project",
            tasks=[
                PlanTask(
                    id="plan",
                    title="Plan",
                    description="Plan parallel work.",
                    suggested_agent="planner",
                    expected_outputs=["json"],
                ),
                PlanTask(
                    id="application",
                    title="Application",
                    description="Implement arithmetic.",
                    dependencies=["plan"],
                    required_capabilities=["python"],
                    suggested_agent="coder",
                    parallelizable=True,
                    expected_outputs=["source_file"],
                ),
                PlanTask(
                    id="tests",
                    title="Tests",
                    description="Add arithmetic tests.",
                    dependencies=["plan"],
                    required_capabilities=["python"],
                    suggested_agent="coder",
                    parallelizable=True,
                    expected_outputs=["source_file"],
                ),
                PlanTask(
                    id="verification",
                    title="Verify",
                    description="Run pytest.",
                    dependencies=["application", "tests"],
                    required_capabilities=["python"],
                    suggested_agent="integrator",
                    expected_outputs=["test_report"],
                ),
                PlanTask(
                    id="review",
                    title="Review",
                    description="Review passing work.",
                    dependencies=["verification"],
                    suggested_agent="reviewer",
                    expected_outputs=["json"],
                ),
            ],
        )

    async def generate(
        self, prompt: str, task: PlanTask, repository_context: str
    ) -> GeneratedChangeSet:
        if task.id == "application":
            files = [
                GeneratedFile(path="app/__init__.py", content=""),
                GeneratedFile(
                    path="app/main.py",
                    content="def add(left: int, right: int) -> int:\n    return left + right\n",
                ),
                GeneratedFile(
                    path="pyproject.toml",
                    content=(
                        '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n'
                    ),
                ),
            ]
        else:
            files = [
                GeneratedFile(path="tests/__init__.py", content=""),
                GeneratedFile(
                    path="tests/test_main.py",
                    content=(
                        "from app.main import add\n\n\n"
                        "def test_add() -> None:\n    assert add(2, 3) == 5\n"
                    ),
                ),
            ]
        return GeneratedChangeSet(
            summary=f"Generated {task.id}", files=files, test_commands=[], notes=[]
        )

    async def final_review(
        self,
        prompt: str,
        repository_context: str,
        task_summary: list[dict[str, object]],
    ) -> FinalReviewReport:
        return FinalReviewReport(
            summary="Parallel tasks integrated and tests passed.",
            approved=True,
            severity="none",
            findings=[],
        )


def register_worker(socket, worker_id: str) -> None:
    socket.send_json(
        {
            "type": "worker.register",
            "worker_id": worker_id,
            "hostname": worker_id,
            "hardware": {
                "cpu_cores": 4,
                "ram_mb": 8192,
                "gpu": {"available": False, "vram_mb": 0},
            },
            "capabilities": ["python", "git"],
            "models": [],
        }
    )
    assert socket.receive_json()["type"] == "worker.registered"


def complete_materialization(socket, job: dict[str, object]) -> None:
    common = {
        "job_id": job["job_id"],
        "project_id": job["project_id"],
        "task_id": job["task_id"],
    }
    socket.send_json({"type": "job.started", **common})
    payload = job["payload"]
    assert isinstance(payload, dict)
    files = payload["files"]
    socket.send_json(
        {
            "type": "job.completed",
            "artifacts": [
                {
                    "type": "source_bundle",
                    "filename": f"{payload['label']}-files.json",
                    "files": files,
                }
            ],
            "result": {"materializer": "test-worker"},
            **common,
        }
    )


def test_independent_tasks_dispatch_to_two_workers_concurrently(tmp_path) -> None:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'parallel.db'}",
        data_dir=tmp_path / "data",
        worker_token="parallel-worker-token",
        demo_mode=False,
        command_timeout_seconds=30,
        job_assignment_timeout_seconds=30,
    )
    app = create_app(settings, model_runtime=ParallelModelRuntime())
    with TestClient(app) as client:
        with (
            client.websocket_connect("/ws/worker?token=parallel-worker-token") as first,
            client.websocket_connect("/ws/worker?token=parallel-worker-token") as second,
        ):
            register_worker(first, "worker-one")
            register_worker(second, "worker-two")
            project = client.post(
                "/projects",
                json={
                    "name": "Parallel arithmetic",
                    "prompt": "Build a tested Python arithmetic library using parallel tasks.",
                    "strategy": "auto",
                    "maximum_retries": 1,
                },
            ).json()
            assert client.post(f"/projects/{project['id']}/run").status_code == 202

            first_job = first.receive_json()
            second_job = second.receive_json()
            assert first_job["type"] == "job.assign"
            assert second_job["type"] == "job.assign"
            assert first_job["task_id"] != second_job["task_id"]
            complete_materialization(first, first_job)
            complete_materialization(second, second_job)

            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                project = client.get(f"/projects/{project['id']}").json()
                if project["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)

            assert project["status"] == "completed"
            jobs = client.get(f"/projects/{project['id']}/jobs").json()
            assert len(jobs) == 2
            assert {job["worker_id"] for job in jobs} == {"worker-one", "worker-two"}
            assert {job["status"] for job in jobs} == {"COMPLETED"}
            events = client.get(f"/projects/{project['id']}/events").json()
            assert any(event["type"] == "tasks.parallel" for event in events)
