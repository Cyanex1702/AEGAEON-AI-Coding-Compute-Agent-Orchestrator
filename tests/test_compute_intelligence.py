from __future__ import annotations

from aegaeon.compute.planner import AdaptiveComputePlanner, ContextBudgeter
from aegaeon.compute.schemas import (
    ComputePolicy,
    ContextSection,
    DeviceStrategy,
    IncidentState,
    OOMRisk,
    RecoveryAction,
)
from aegaeon.compute.service import ComputeIntelligence
from aegaeon.database.session import Database

CATALOG = [
    {
        "id": "Qwen/Qwen2.5-Coder-7B-Instruct",
        "family": "Qwen2.5-Coder",
        "parameter_count_b": 7.61,
        "quantizations": ["4bit", "8bit", "bf16"],
        "safe_vram_mb": {"4bit": 8704, "8bit": 12288, "bf16": 20480},
        "benchmark_scores": {"coding": 91},
    },
    {
        "id": "Qwen/Qwen2.5-Coder-3B-Instruct",
        "family": "Qwen2.5-Coder",
        "parameter_count_b": 3.09,
        "quantizations": ["4bit", "8bit", "bf16"],
        "safe_vram_mb": {"4bit": 4608, "8bit": 6656, "bf16": 10240},
        "benchmark_scores": {"coding": 84},
    },
]


def worker(
    worker_id: str = "worker-t4",
    *,
    free_mb: int = 13000,
    total_mb: int = 15360,
    ram_available_mb: int = 10000,
    model_id: str = "Qwen/Qwen2.5-Coder-7B-Instruct",
    gpus: int = 1,
    supports_cpu_offload: bool = True,
) -> dict[str, object]:
    per_total = total_mb // gpus
    per_free = free_mb // gpus
    gpu_rows = [
        {
            "index": index,
            "name": "NVIDIA T4",
            "total_mb": per_total,
            "allocated_mb": per_total - per_free,
            "reserved_mb": per_total - per_free,
            "free_mb": per_free,
            "utilization_percent": 15,
            "compute_capability": "7.5",
        }
        for index in range(gpus)
    ]
    return {
        "id": worker_id,
        "hostname": worker_id,
        "status": "ready",
        "hardware": {
            "cpu_cores": 4,
            "ram_mb": 16000,
            "ram_available_mb": ram_available_mb,
            "gpu": {"available": True, **gpu_rows[0], "vram_mb": per_total},
            "gpus": gpu_rows,
        },
        "models": [
            {
                "id": model_id,
                "provider": "huggingface",
                "loaded": True,
                "status": "ready",
                "runtime": "transformers",
                "quantization": "4bit",
                "memory_footprint_mb": total_mb - free_mb,
                "max_recommended_prompt_tokens": 8192,
                "max_recommended_output_tokens": 3072,
                "memory_risk_profile": "LOW",
                "supports_cpu_offload": supports_cpu_offload,
                "supports_multi_gpu": gpus > 1,
            }
        ],
        "ram_percent": 30,
        "vram_used_mb": total_mb - free_mb,
    }


def make_plan(
    planner: AdaptiveComputePlanner,
    *,
    workers: list[dict[str, object]],
    policy: ComputePolicy | None = None,
    model_id: str = "Qwen/Qwen2.5-Coder-7B-Instruct",
    observations: list[dict[str, object]] | None = None,
    attempt: int = 0,
    previous_failure: str | None = None,
):
    return planner.plan(
        project_id="project-compute",
        task_id="task-compute",
        task_description="Implement a robust authenticated API and its full regression tests.",
        model_id=model_id,
        runtime="transformers",
        quantization="4bit",
        workers=workers,
        model_catalog=CATALOG,
        policy=policy or ComputePolicy(allow_model_fallback=True),
        context_sections=[
            ContextSection(
                name="task", content="Exact task contract.", priority=100, protected=True
            ),
            ContextSection(name="history", content="old log " * 4000, priority=10),
        ],
        requested_output_tokens=4096,
        observations=observations or [],
        attempt=attempt,
        previous_failure=previous_failure,
    )


def test_context_budget_preserves_contract_and_drops_low_priority_history() -> None:
    packed = ContextBudgeter().pack(
        [
            ContextSection(
                name="contract", content="must preserve this", priority=100, protected=True
            ),
            ContextSection(name="repository", content="x" * 8000, priority=40),
            ContextSection(name="history", content="y" * 8000, priority=5),
        ],
        600,
    )

    contract = next(item for item in packed.sections if item.name == "contract")
    assert contract.included is True
    assert contract.content == "must preserve this"
    assert "history" in packed.dropped_sections


def test_t4_plan_reduces_context_and_output_with_explicit_risk() -> None:
    plan = make_plan(AdaptiveComputePlanner(), workers=[worker(free_mb=6500)])

    assert plan.worker_id == "worker-t4"
    assert plan.maximum_new_tokens >= 1536
    assert plan.maximum_new_tokens <= 3072
    assert plan.context_plan is not None
    assert "history" in plan.context_plan.dropped_sections
    assert plan.estimated_oom_risk in {OOMRisk.MODERATE, OOMRisk.HIGH, OOMRisk.VERY_HIGH}
    assert plan.reasoning_summary


def test_truncated_structured_output_requests_a_smaller_task_shard() -> None:
    plan = make_plan(
        AdaptiveComputePlanner(),
        workers=[worker(free_mb=10_000)],
        attempt=1,
        previous_failure="OUTPUT_TRUNCATED",
    )

    assert plan.next_recovery_action == RecoveryAction.SPLIT_TASK


def test_larger_gpu_gets_more_output_and_lower_risk() -> None:
    planner = AdaptiveComputePlanner()
    small = make_plan(planner, workers=[worker(free_mb=6500)])
    large = make_plan(
        planner,
        workers=[worker("worker-a100", free_mb=36000, total_mb=40960, ram_available_mb=30000)],
    )

    assert large.maximum_new_tokens >= small.maximum_new_tokens
    assert large.fit_score > small.fit_score
    assert large.estimated_oom_risk == OOMRisk.LOW


def test_cpu_offload_requires_safe_host_ram_and_capability() -> None:
    planner = AdaptiveComputePlanner()
    allowed = make_plan(
        planner,
        workers=[worker(free_mb=5000, ram_available_mb=24000, supports_cpu_offload=True)],
    )
    unsafe = make_plan(
        planner,
        workers=[worker(free_mb=5000, ram_available_mb=5000, supports_cpu_offload=True)],
    )

    assert allowed.device_strategy == DeviceStrategy.GPU_WITH_CPU_OFFLOAD
    assert unsafe.device_strategy == DeviceStrategy.GPU


def test_multi_gpu_is_same_machine_only_and_workers_are_not_sharded() -> None:
    planner = AdaptiveComputePlanner()
    same_machine = make_plan(
        planner,
        workers=[worker("worker-dual", free_mb=10000, total_mb=12000, gpus=2)],
    )
    separate_workers = make_plan(
        planner,
        workers=[
            worker("worker-one", free_mb=5000, total_mb=6000),
            worker("worker-two", free_mb=5000, total_mb=6000),
        ],
    )

    assert same_machine.device_strategy == DeviceStrategy.MULTI_GPU
    assert separate_workers.device_strategy != DeviceStrategy.MULTI_GPU
    assert separate_workers.worker_id in {"worker-one", "worker-two"}


def test_model_fallback_is_policy_gated_and_never_changes_explicit_model() -> None:
    planner = AdaptiveComputePlanner()
    strict = make_plan(
        planner,
        workers=[worker(free_mb=4500)],
        policy=ComputePolicy(allow_model_fallback=False),
    )
    permissive = make_plan(
        planner,
        workers=[worker(free_mb=4500)],
        policy=ComputePolicy(allow_model_fallback=True),
    )

    assert strict.model_id == "Qwen/Qwen2.5-Coder-7B-Instruct"
    assert strict.fallback_models == []
    assert permissive.model_id == strict.model_id
    assert permissive.fallback_models == ["Qwen/Qwen2.5-Coder-3B-Instruct"]


def test_history_increases_risk_and_recovery_falls_back_after_splitting() -> None:
    planner = AdaptiveComputePlanner()
    baseline = make_plan(planner, workers=[worker(free_mb=10000)])
    history = [
        {
            "model_id": baseline.model_id,
            "quantization": "4bit",
            "gpu_name": "NVIDIA T4",
            "prompt_tokens": baseline.context_plan.packed_tokens if baseline.context_plan else 0,
            "output_limit": baseline.maximum_new_tokens,
            "result": "OOM",
            "failure_classification": "GENERATION_CUDA_OOM",
        }
    ]
    learned = make_plan(planner, workers=[worker(free_mb=10000)], observations=history)
    recovering = make_plan(
        planner,
        workers=[worker(free_mb=10000)],
        observations=history,
        attempt=3,
        previous_failure="GENERATION_CUDA_OOM",
    )

    assert learned.fit_score < baseline.fit_score
    assert recovering.maximum_new_tokens >= recovering.policy.minimum_viable_output_tokens
    assert recovering.task_split_allowed is True
    assert recovering.next_recovery_action == RecoveryAction.FALLBACK_MODEL


def test_worker_measured_prompt_limit_caps_the_controller_budget() -> None:
    measured = worker(free_mb=10_000)
    measured["models"][0]["max_recommended_prompt_tokens"] = 4_096

    plan = make_plan(AdaptiveComputePlanner(), workers=[measured])

    assert plan.context_plan is not None
    assert plan.context_plan.token_budget <= 3_072


def test_observed_peak_memory_overrides_an_optimistic_static_estimate() -> None:
    observations = [
        {
            "model_id": "Qwen/Qwen2.5-Coder-7B-Instruct",
            "quantization": "4bit",
            "gpu_name": "NVIDIA T4",
            "prompt_tokens": 6_022,
            "output_limit": 768,
            "peak_vram_mb": 13_982,
            "result": "OOM",
            "failure_classification": "GENERATION_CUDA_OOM",
        }
    ]

    plan = make_plan(
        AdaptiveComputePlanner(),
        workers=[worker(free_mb=13_000)],
        observations=observations,
    )

    assert plan.estimated_vram_required_mb >= 14_681
    assert plan.estimated_oom_risk == OOMRisk.VERY_HIGH


def test_oom_retries_update_one_incident_timeline(tmp_path) -> None:
    database = Database(f"sqlite:///{tmp_path / 'compute.db'}")
    database.create_all()
    compute = ComputeIntelligence(database)
    plan = compute.create_plan(
        project_id="project-compute",
        task_id="task-compute",
        task_description="Implement a durable parser.",
        model_id="Qwen/Qwen2.5-Coder-7B-Instruct",
        runtime="transformers",
        quantization="4bit",
        workers=[worker(free_mb=6500)],
        model_catalog=CATALOG,
        options={"allow_model_fallback": True},
        context_sections=[ContextSection(name="task", content="Exact task", protected=True)],
        requested_output_tokens=2048,
        attempt=1,
        previous_failure="GENERATION_CUDA_OOM",
    )
    diagnostics = {
        "classification": "GENERATION_CUDA_OOM",
        "prompt_tokens": 4000,
        "requested_output_tokens": 1500,
        "gpu": {"device": "NVIDIA T4", "total_mb": 15360, "peak_allocated_mb": 15000},
    }

    _, first = compute.record_failure(
        plan.id,
        worker_id="worker-t4",
        classification="GENERATION_CUDA_OOM",
        message="CUDA out of memory",
        diagnostics=diagnostics,
        attempt=1,
        final=False,
    )
    _, second = compute.record_failure(
        plan.id,
        worker_id="worker-t4",
        classification="GENERATION_CUDA_OOM",
        message="CUDA out of memory again",
        diagnostics=diagnostics,
        attempt=2,
        final=False,
    )

    assert first is not None and second is not None
    assert first.id == second.id
    assert second.root_failure_id == first.root_failure_id
    assert second.state == IncidentState.RETRYING
    assert len(second.attempts) == 2
    database.close()


def test_compute_api_returns_live_overview(client) -> None:
    response = client.get("/compute")

    assert response.status_code == 200
    body = response.json()
    assert body["policy"]["preset"] == "BALANCED"
    assert body["summary"]["online_workers"] == 0
    assert body["active_plans"] == []
    assert body["incidents"] == []
