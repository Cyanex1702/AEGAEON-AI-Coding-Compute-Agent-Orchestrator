from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from fastapi.testclient import TestClient

from aegaeon.api import create_app
from aegaeon.config import Settings
from aegaeon.models.provider import ModelProvider
from aegaeon.models.results import FinalReviewReport, GeneratedChangeSet, GeneratedFile
from aegaeon.protocol.schemas import PlanTask, ProjectPlan


class ModelPathProvider(ModelProvider):
    def __init__(self) -> None:
        plan = ProjectPlan(
            project_summary="Model-generated arithmetic library with passing tests",
            tasks=[
                PlanTask(
                    id="plan",
                    title="Plan implementation",
                    description="Create a small tested Python library.",
                    suggested_agent="planner",
                    expected_outputs=["json"],
                ),
                PlanTask(
                    id="implementation",
                    title="Implement and test arithmetic",
                    description="Create the arithmetic function and pytest coverage.",
                    dependencies=["plan"],
                    required_capabilities=["python"],
                    suggested_agent="coder",
                    expected_outputs=["source_file", "git_patch"],
                ),
                PlanTask(
                    id="verification",
                    title="Run tests",
                    description="Run repository-native verification.",
                    dependencies=["implementation"],
                    required_capabilities=["python"],
                    suggested_agent="integrator",
                    expected_outputs=["test_report"],
                ),
                PlanTask(
                    id="review",
                    title="Review result",
                    description="Review the passing implementation.",
                    dependencies=["verification"],
                    suggested_agent="reviewer",
                    expected_outputs=["json"],
                ),
            ],
        )
        changes = GeneratedChangeSet(
            summary="Implemented a typed add function with pytest coverage.",
            files=[
                GeneratedFile(path="app/__init__.py", content=""),
                GeneratedFile(
                    path="app/main.py",
                    content=("def add(left: int, right: int) -> int:\n    return left + right\n"),
                ),
                GeneratedFile(path="tests/__init__.py", content=""),
                GeneratedFile(
                    path="tests/test_main.py",
                    content=(
                        "from app.main import add\n\n\n"
                        "def test_add() -> None:\n"
                        "    assert add(2, 3) == 5\n"
                    ),
                ),
                GeneratedFile(
                    path="pyproject.toml",
                    content=(
                        '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n'
                    ),
                ),
            ],
            test_commands=[],
            notes=[],
        )
        review = FinalReviewReport(
            summary="Implementation is focused and verification passed.",
            approved=True,
            severity="none",
            findings=[],
        )
        self.outputs = [
            plan.model_dump_json(),
            changes.model_dump_json(),
            review.model_dump_json(),
        ]

    @property
    def model_id(self) -> str:
        return "queued-code-model"

    @property
    def provider_name(self) -> str:
        return "queued-test-provider"

    def structured_formats(
        self, schema: dict[str, Any], schema_name: str
    ) -> list[dict[str, Any] | None]:
        return [None]

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        temperature: float = 0.1,
    ) -> str:
        return self.outputs.pop(0)

    async def probe(self) -> tuple[bool, str | None]:
        return True, None


def model_client(tmp_path) -> Iterator[TestClient]:
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'model-test.db'}",
        data_dir=tmp_path / "data",
        worker_token="model-test-token",
        demo_mode=False,
        allow_unisolated_verification=True,
        llm_model="queued-code-model",
        command_timeout_seconds=30,
    )
    app = create_app(settings, provider=ModelPathProvider())
    with TestClient(app) as client:
        yield client


def test_model_provider_drives_plan_code_verification_and_review(tmp_path) -> None:
    client_context = model_client(tmp_path)
    client = next(client_context)
    try:
        status = client.get("/provider/status").json()
        assert status["mode"] == "model"
        assert status["model"] == "queued-code-model"
        assert client.post("/provider/check").json()["reachable"] is True

        response = client.post(
            "/projects",
            json={
                "name": "Model arithmetic",
                "prompt": "Build a tested Python arithmetic library with an add function.",
                "strategy": "auto",
                "maximum_retries": 1,
            },
        )
        assert response.status_code == 201
        project_id = response.json()["id"]
        assert client.post(f"/projects/{project_id}/run").status_code == 202

        deadline = time.monotonic() + 20
        project = response.json()
        while time.monotonic() < deadline:
            project = client.get(f"/projects/{project_id}").json()
            if project["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)

        assert project["status"] == "completed"
        assert [task["key"] for task in project["tasks"]] == [
            "plan",
            "implementation",
            "verification",
            "review",
        ]
        assert all(task["status"] == "COMPLETED" for task in project["tasks"])
        verification = next(task for task in project["tasks"] if task["key"] == "verification")
        assert verification["result"]["exit_code"] == 0
    finally:
        try:
            next(client_context)
        except StopIteration:
            pass
