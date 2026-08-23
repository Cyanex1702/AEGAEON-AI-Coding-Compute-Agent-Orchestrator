from aegaeon.protocol.schemas import JobRequirements
from aegaeon.workers.scheduler import CapabilityScheduler


def test_scheduler_filters_and_scores_workers() -> None:
    workers = [
        {
            "id": "cpu-small",
            "status": "online",
            "capabilities": ["python"],
            "hardware": {"ram_mb": 2048, "gpu": {"available": False, "vram_mb": 0}},
            "models": [],
        },
        {
            "id": "gpu-idle",
            "status": "online",
            "capabilities": ["python", "git", "cuda"],
            "hardware": {"ram_mb": 32768, "gpu": {"available": True, "vram_mb": 16384}},
            "models": [{"id": "code-7b", "loaded": True}],
            "cpu_percent": 10,
            "vram_used_mb": 1024,
            "current_task": None,
        },
    ]
    decision = CapabilityScheduler().select(
        workers,
        JobRequirements(
            gpu=True,
            minimum_vram_mb=8000,
            minimum_ram_mb=4096,
            capabilities=["python", "git"],
            model="code-7b",
        ),
    )
    assert decision is not None
    assert decision.worker_id == "gpu-idle"
    assert "Required model already loaded" in decision.reason


def test_scheduler_returns_none_without_required_capability() -> None:
    workers = [
        {
            "id": "worker",
            "status": "online",
            "capabilities": ["python"],
            "hardware": {"ram_mb": 4096, "gpu": {}},
        }
    ]
    assert CapabilityScheduler().select(workers, JobRequirements(capabilities=["node"])) is None
