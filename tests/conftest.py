from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from aegaeon.api import create_app
from aegaeon.config import Settings


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path / 'aegaeon-test.db'}",
        data_dir=tmp_path / "data",
        worker_token="test-worker-token",
        allow_development_worker_token=True,
        heartbeat_timeout_seconds=5,
        command_timeout_seconds=30,
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def project_payload() -> dict[str, object]:
    return {
        "name": "Calculator API",
        "prompt": (
            "Build a Python calculator API with add, subtract, multiply and divide "
            "endpoints and pytest tests."
        ),
        "strategy": "auto",
        "intelligence": 70,
        "maximum_retries": 2,
        "run_tests": True,
        "review_code": True,
        "retry_failed_tasks": True,
    }
