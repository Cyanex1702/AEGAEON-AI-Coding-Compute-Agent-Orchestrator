from __future__ import annotations

import json
import time

import pytest
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from aegaeon.protocol.schemas import (
    ArtifactPayload,
    JobAssign,
    JobRequirements,
    ModelGeneratePayload,
    ModelGenerationContext,
)
from aegaeon.workers.scheduler import CapabilityScheduler


def test_broke_project_recommendation_notebook_and_one_time_pairing(
    client, project_payload
) -> None:
    payload = {
        **project_payload,
        "execution_mode": "broke_boy",
        "worker_count": 2,
        "compute_target": "local_gpu",
        "model_selection": "recommend",
        "target_vram_mb": 15_000,
        "quantization": "4bit",
        "research_level": "standard",
    }
    response = client.post("/projects", json=payload)
    assert response.status_code == 201
    project = response.json()
    assert project["options"]["execution_mode"] == "broke_boy"
    assert project["options"]["selected_model"]
    assert project["options"]["selected_model_vram_mb"] <= 12_750

    run = client.post(f"/projects/{project['id']}/run")
    assert run.status_code == 409
    assert "model worker" in run.json()["detail"].lower()

    notebooks = client.post(
        f"/projects/{project['id']}/workers/notebooks",
        json={"controller_url": "http://127.0.0.1:8000"},
    )
    assert notebooks.status_code == 201
    generated = notebooks.json()
    assert len(generated) == 2
    first = generated[0]
    assert first["pairing_code"].startswith("AG-")

    notebook_response = client.get(first["download_url"])
    assert notebook_response.status_code == 200
    notebook_text = notebook_response.text
    assert first["pairing_code"] not in notebook_text
    assert "development-token" not in notebook_text
    assert "getpass.getpass" in notebook_text
    assert "model.generate" in notebook_text

    redeemed = client.post("/pairing/redeem", json={"pairing_code": first["pairing_code"]})
    assert redeemed.status_code == 200
    credential = redeemed.json()
    assert credential["worker_token"] not in notebook_text
    assert credential["model_id"] == project["options"]["selected_model"]
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/worker?token={credential['worker_token']}") as socket:
            socket.send_json(
                {
                    "type": "worker.register",
                    "worker_id": "forged-worker",
                    "hostname": "forged-worker",
                    "hardware": {"cpu_cores": 2, "ram_mb": 4096, "gpu": {}},
                    "capabilities": ["model.generate"],
                    "models": [
                        {
                            "id": credential["model_id"],
                            "loaded": True,
                            "status": "ready",
                        }
                    ],
                }
            )
            socket.receive_json()
    assert (
        client.post("/pairing/redeem", json={"pairing_code": first["pairing_code"]}).status_code
        == 400
    )


def test_model_scout_hard_filters_and_safe_integration_status(client) -> None:
    too_small = client.post(
        "/model-scout/recommend",
        json={
            "project": "Build a tested Python API with structured source output.",
            "hardware": {"vram_mb": 2_000, "quantization": "4bit"},
        },
    )
    assert too_small.status_code == 200
    assert too_small.json() == []

    fitting = client.post(
        "/model-scout/recommend",
        json={
            "project": "Build a tested Python API with structured source output.",
            "hardware": {"vram_mb": 15_000, "quantization": "4bit"},
        },
    ).json()
    assert fitting
    assert all(item["estimated_vram_mb"] <= 12_750 for item in fitting)
    assert fitting[0]["reasons"]
    assert fitting[0]["score_breakdown"]["hardware_fit"] > 0

    integrations = client.get("/integrations").json()
    assert integrations["hugging_face"]["secret_exposed"] is False
    assert "token" not in integrations["hugging_face"]


def test_manual_research_prompt_and_conflict_import(client, project_payload) -> None:
    project = client.post("/projects", json=project_payload).json()
    prompt_response = client.post(
        f"/projects/{project['id']}/research/prompt",
        json={"vram_mb": 15_000, "quantization": "4bit"},
    )
    assert prompt_response.status_code == 200
    prompt = prompt_response.json()["prompt"]
    assert "RETURN EXACT JSON" in prompt
    assert "Do not invent model facts" in prompt

    advisor = {
        "recommended_model": "made-up/model",
        "runner_up": None,
        "tradeoffs": ["small"],
        "warnings": [],
        "confidence": "high",
        "vram_claims_mb": {},
    }
    imported = client.post(
        f"/projects/{project['id']}/research/import",
        json={"advisor": "manual-review", "response": json.dumps(advisor)},
    )
    assert imported.status_code == 201
    assert imported.json()["conflicts"]
    assert len(client.get(f"/projects/{project['id']}/research").json()) == 1


def test_typed_model_job_and_artifact_scope_validation() -> None:
    payload = ModelGeneratePayload(
        role="coder",
        instructions="Implement the requested feature",
        context=ModelGenerationContext(
            project_summary="A complete test project",
            repository="README.md",
        ),
    )
    with pytest.raises(ValidationError):
        JobAssign(
            job_id="job-bad",
            job_type="model.generate",
            project_id="project-one",
            task_id="task-one",
            agent_role="coder",
            requirements=JobRequirements(),
            instructions=payload.instructions,
            payload=payload.model_dump(mode="json"),
        )
    with pytest.raises(ValidationError):
        ArtifactPayload(
            type="source_bundle",
            filename="source.json",
            files={"../controller.env": "nope"},
        )


def test_scheduler_rejects_wrong_model_and_accepts_exact_ready_runtime() -> None:
    scheduler = CapabilityScheduler()
    worker = {
        "id": "worker-model",
        "status": "online",
        "capabilities": ["model.generate"],
        "hardware": {
            "ram_mb": 16_384,
            "gpu": {"available": True, "vram_mb": 15_000},
        },
        "models": [
            {
                "id": "Qwen/model",
                "loaded": True,
                "status": "ready",
                "runtime": "transformers",
                "quantization": "4bit",
                "task_scores": {"coder": 80},
            }
        ],
        "cpu_percent": 5,
        "vram_used_mb": 2_000,
        "current_task": None,
    }
    wrong = JobRequirements(
        gpu=True,
        minimum_vram_mb=3_000,
        minimum_ram_mb=4_096,
        capabilities=["model.generate"],
        model="other/model",
        runtime="transformers",
        quantization="4bit",
        task_category="coder",
        minimum_model_score=60,
    )
    assert scheduler.select([worker], wrong) is None
    exact = wrong.model_copy(update={"model": "Qwen/model"})
    decision = scheduler.select([worker], exact)
    assert decision is not None
    assert decision.worker_id == "worker-model"


def _complete_model_job(socket, job: dict[str, object], files: dict[str, str]) -> None:
    common = {
        "job_id": job["job_id"],
        "project_id": job["project_id"],
        "task_id": job["task_id"],
    }
    socket.send_json({"type": "job.started", **common})
    socket.send_json(
        {
            "type": "job.completed",
            "artifacts": [
                {
                    "type": "source_bundle",
                    "filename": f"{str(job['task_id']).split(':')[-1]}.json",
                    "files": files,
                }
            ],
            "result": {"model": "mock-local", "runtime": "transformers"},
            **common,
        }
    )


def test_broke_model_worker_completes_typed_generation_and_verification(
    client, project_payload
) -> None:
    payload = {
        **project_payload,
        "name": "Worker arithmetic",
        "execution_mode": "broke_boy",
        "worker_count": 1,
        "target_vram_mb": 15_000,
        "quantization": "4bit",
    }
    project = client.post("/projects", json=payload).json()
    selected_model = project["options"]["selected_model"]
    minimum_vram = project["options"]["selected_model_vram_mb"]

    with client.websocket_connect("/ws/worker?token=test-worker-token") as socket:
        socket.send_json(
            {
                "type": "worker.register",
                "worker_id": "worker-broke-e2e",
                "hostname": "mock-colab",
                "hardware": {
                    "cpu_cores": 4,
                    "ram_mb": 16_384,
                    "cuda": True,
                    "gpu": {"available": True, "name": "T4", "vram_mb": 15_000},
                },
                "capabilities": ["model.generate", "model.repair"],
                "models": [
                    {
                        "id": selected_model,
                        "provider": "huggingface",
                        "loaded": True,
                        "runtime": "transformers",
                        "quantization": "4bit",
                        "status": "ready",
                        "context_length": 32_768,
                        "task_scores": {"coder": 80, "repair": 75},
                    }
                ],
            }
        )
        assert socket.receive_json()["type"] == "worker.registered"
        assert minimum_vram <= 15_000
        assert client.post(f"/projects/{project['id']}/run").status_code == 202

        implementation = socket.receive_json()
        assert implementation["job_type"] == "model.generate"
        assert implementation["requirements"]["model"] == selected_model
        assert implementation["payload"]["context"]["project_summary"] == payload["prompt"]
        _complete_model_job(
            socket,
            implementation,
            {
                "calculator.py": (
                    "def add(left: int, right: int) -> int:\n    return left + right\n"
                ),
                "pyproject.toml": (
                    '[tool.pytest.ini_options]\npythonpath = ["."]\ntestpaths = ["tests"]\n'
                ),
                "tests/test_calculator.py": (
                    "from calculator import add\n\n\n"
                    "def test_add() -> None:\n"
                    "    assert add(2, 3) == 5\n"
                ),
            },
        )

        tests_job = socket.receive_json()
        assert tests_job["job_type"] == "model.generate"
        _complete_model_job(
            socket,
            tests_job,
            {
                "tests/test_more.py": (
                    "from calculator import add\n\n\n"
                    "def test_negative_add() -> None:\n"
                    "    assert add(-2, 1) == -1\n"
                )
            },
        )

        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            project = client.get(f"/projects/{project['id']}").json()
            if project["status"] in {"completed", "failed"}:
                break
            time.sleep(0.05)

    assert project["status"] == "completed"
    jobs = client.get(f"/projects/{project['id']}/jobs").json()
    assert len(jobs) == 2
    assert {job["job_type"] for job in jobs} == {"model.generate"}
    assert {job["status"] for job in jobs} == {"COMPLETED"}
    events = client.get(f"/projects/{project['id']}/events").json()
    assert any(event["type"] == "model.inference.completed" for event in events)
    assert any(event["type"] == "tests.completed" for event in events)
