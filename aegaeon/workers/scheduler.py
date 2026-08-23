from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aegaeon.protocol.schemas import JobRequirements


@dataclass(slots=True)
class ScheduleDecision:
    worker_id: str
    score: float
    reason: list[str]


class CapabilityScheduler:
    """Filters workers by hard requirements, then ranks idle/cached capacity."""

    def select(
        self, workers: list[dict[str, Any]], requirements: JobRequirements
    ) -> ScheduleDecision | None:
        decisions: list[ScheduleDecision] = []
        for worker in workers:
            if worker.get("status") not in {"online", "idle"}:
                continue
            capabilities = set(worker.get("capabilities", []))
            if not set(requirements.capabilities).issubset(capabilities):
                continue
            hardware = worker.get("hardware", {})
            gpu = hardware.get("gpu", {})
            if requirements.gpu and not gpu.get("available"):
                continue
            if gpu.get("vram_mb", 0) < requirements.minimum_vram_mb:
                continue
            if hardware.get("ram_mb", 0) < requirements.minimum_ram_mb:
                continue
            models = worker.get("models", [])
            ready_models = [
                item
                for item in models
                if item.get("loaded") and item.get("status", "ready") == "ready"
            ]
            if requirements.model and not any(
                item.get("id") == requirements.model for item in ready_models
            ):
                continue
            if requirements.runtime and not any(
                item.get("runtime") == requirements.runtime for item in ready_models
            ):
                continue
            if requirements.quantization and not any(
                item.get("quantization") == requirements.quantization for item in ready_models
            ):
                continue
            if requirements.task_category:
                best_task_score = max(
                    (
                        float(item.get("task_scores", {}).get(requirements.task_category, 0))
                        for item in ready_models
                    ),
                    default=0,
                )
                if best_task_score < requirements.minimum_model_score:
                    continue
            else:
                best_task_score = 0

            score = 50.0
            reason = ["All requirements satisfied"]
            if not worker.get("current_task"):
                score += 20
                reason.append("Worker idle")
            if requirements.model and any(
                item.get("id") == requirements.model for item in ready_models
            ):
                score += 15
                reason.append("Required model already loaded")
            if best_task_score:
                score += min(15, best_task_score * 0.15)
                reason.append(f"{requirements.task_category} model score: {best_task_score:g}")
            cpu = float(worker.get("cpu_percent", 0))
            score += max(0, 10 - cpu / 10)
            free_vram = max(0, gpu.get("vram_mb", 0) - worker.get("vram_used_mb", 0))
            if free_vram:
                score += min(10, free_vram / 2048)
                reason.append(f"{free_vram // 1024} GB free VRAM")
            decisions.append(ScheduleDecision(worker["id"], round(score, 1), reason))
        return max(decisions, key=lambda item: item.score, default=None)
