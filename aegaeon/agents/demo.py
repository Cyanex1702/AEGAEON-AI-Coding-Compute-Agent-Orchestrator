from __future__ import annotations

from aegaeon.protocol.schemas import PlanTask, ProjectPlan

CALCULATOR_MAIN = """from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Calculator API", version="1.0.0")


class Calculation(BaseModel):
    a: float
    b: float


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/add")
def add(values: Calculation) -> dict[str, float]:
    return {"result": values.a + values.b}


@app.post("/subtract")
def subtract(values: Calculation) -> dict[str, float]:
    return {"result": values.a - values.b}


@app.post("/multiply")
def multiply(values: Calculation) -> dict[str, float]:
    return {"result": values.a * values.b}


@app.post("/divide")
def divide(values: Calculation) -> dict[str, float]:
    if values.b == 0:
        raise HTTPException(status_code=400, detail="Division by zero is not allowed")
    return {"result": values.a / values.b}
"""

GENERIC_MAIN = """from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI(title="AEGAEON Generated API", version="1.0.0")


class Message(BaseModel):
    text: str


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/message")
def message(payload: Message) -> dict[str, str]:
    return {"message": payload.text}
"""

CALCULATOR_TESTS = """from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_calculator_operations() -> None:
    assert client.post("/add", json={"a": 8, "b": 2}).json() == {"result": 10}
    assert client.post("/subtract", json={"a": 8, "b": 2}).json() == {"result": 6}
    assert client.post("/multiply", json={"a": 8, "b": 2}).json() == {"result": 16}
    assert client.post("/divide", json={"a": 8, "b": 2}).json() == {"result": 4}


def test_divide_by_zero() -> None:
    response = client.post("/divide", json={"a": 8, "b": 0})
    assert response.status_code == 400
    assert response.json()["detail"] == "Division by zero is not allowed"
"""

GENERIC_TESTS = """from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health() -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_message() -> None:
    assert client.post("/message", json={"text": "hello"}).json() == {"message": "hello"}
"""


class DemoAgentRuntime:
    """Deterministic portfolio-safe runtime used when no model endpoint is configured."""

    def plan(self, prompt: str) -> ProjectPlan:
        summary = (
            "FastAPI calculator service with four operations and pytest coverage"
            if self._calculator(prompt)
            else "Typed FastAPI service with automated tests"
        )
        return ProjectPlan(
            project_summary=summary,
            tasks=[
                PlanTask(
                    id="plan",
                    title="Plan architecture",
                    description="Translate the request into a validated task graph.",
                    suggested_agent="planner",
                    expected_outputs=["json"],
                ),
                PlanTask(
                    id="implementation",
                    title="Implement application",
                    description="Create a focused, typed FastAPI application scaffold.",
                    dependencies=["plan"],
                    required_capabilities=["python", "git"],
                    suggested_agent="coder",
                    expected_outputs=["source_file", "git_patch"],
                ),
                PlanTask(
                    id="tests",
                    title="Add automated tests",
                    description="Add pytest coverage for the generated API behavior.",
                    dependencies=["implementation"],
                    required_capabilities=["python"],
                    suggested_agent="coder",
                    expected_outputs=["source_file", "git_patch"],
                ),
                PlanTask(
                    id="verification",
                    title="Run test suite",
                    description="Execute the generated project tests in the canonical workspace.",
                    dependencies=["tests"],
                    required_capabilities=["python"],
                    suggested_agent="integrator",
                    expected_outputs=["test_report"],
                ),
                PlanTask(
                    id="review",
                    title="Review final result",
                    description="Review test output, diffs, and integration quality.",
                    dependencies=["verification"],
                    suggested_agent="reviewer",
                    expected_outputs=["json"],
                ),
            ],
        )

    def generate(self, prompt: str, stage: str) -> dict[str, str]:
        calculator = self._calculator(prompt)
        if stage in {"implementation", "fix"}:
            return {
                "app/__init__.py": "",
                "app/main.py": CALCULATOR_MAIN if calculator else GENERIC_MAIN,
                "requirements.txt": "fastapi>=0.115\nuvicorn>=0.34\npytest>=8.3\nhttpx>=0.28\n",
                "pyproject.toml": (
                    '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n'
                ),
                "README.md": self._readme(calculator),
            }
        if stage == "tests":
            return {
                "tests/__init__.py": "",
                "tests/test_api.py": CALCULATOR_TESTS if calculator else GENERIC_TESTS,
            }
        return {}

    @staticmethod
    def _calculator(prompt: str) -> bool:
        lowered = prompt.lower()
        return "calculator" in lowered or all(
            operation in lowered for operation in ("add", "subtract", "multiply", "divide")
        )

    @staticmethod
    def _readme(calculator: bool) -> str:
        title = "Calculator API" if calculator else "Generated FastAPI Service"
        endpoints = (
            "`POST /add`, `POST /subtract`, `POST /multiply`, and `POST /divide`"
            if calculator
            else "`GET /health` and `POST /message`"
        )
        return f"# {title}\n\nGenerated and verified by AEGAEON.\n\nEndpoints: {endpoints}.\n"
