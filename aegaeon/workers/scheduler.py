from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from aegaeon.protocol.schemas import JobRequirements


@dataclass(slots=True)
class ScheduleDecision:
    worker_id: str
    score: float
    reason: list[str]


class WorkerCapacityStatus(StrEnum):
    WORKER_AVAILABLE = "WORKER_AVAILABLE"
    COMPATIBLE_WORKER_BUSY = "COMPATIBLE_WORKER_BUSY"
    WORKER_OFFLINE = "WORKER_OFFLINE"
    NO_COMPATIBLE_WORKER_EXISTS = "NO_COMPATIBLE_WORKER_EXISTS"


@dataclass(slots=True)
class ScheduleAssessment:
    status: WorkerCapacityStatus
    decision: ScheduleDecision | None = None
    compatible_count: int = 0
    available_count: int = 0
    busy_count: int = 0
    offline_count: int = 0


class CapabilityScheduler:
    """Filters workers by hard requirements, then ranks idle/cached capacity."""

    def __init__(self, telemetry_max_age_seconds: int = 30) -> None:
        self.telemetry_max_age_seconds = telemetry_max_age_seconds

    def _telemetry_is_fresh(self, worker: dict[str, Any]) -> bool:
        heartbeat = worker.get("last_heartbeat")
        if heartbeat is None:
            return True
        if not isinstance(heartbeat, datetime):
            return False
        if heartbeat.tzinfo is None:
            heartbeat = heartbeat.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - heartbeat).total_seconds()
        return 0 <= age <= self.telemetry_max_age_seconds

    @staticmethod
    def _memory_snapshot(worker: dict[str, Any]) -> tuple[int, int]:
        """Return the best single-device total/free VRAM, never an invalid aggregate."""
        hardware = worker.get("hardware", {})
        gpu = hardware.get("gpu", {})
        memory = hardware.get("memory", {})
        gpus = hardware.get("gpus") or []
        available = [item for item in gpus if item.get("available", True)]
        if available:
            best = max(
                available,
                key=lambda item: (
                    int(item.get("free_mb", 0)),
                    int(item.get("vram_mb", 0)),
                ),
            )
            return int(best.get("vram_mb", 0)), int(best.get("free_mb", 0))
        total = int(gpu.get("vram_mb", 0))
        free = int(memory.get("vram_free_mb") or gpu.get("free_mb") or 0) or max(
            0, total - int(worker.get("vram_used_mb", 0))
        )
        return total, free

    def select(
        self, workers: list[dict[str, Any]], requirements: JobRequirements
    ) -> ScheduleDecision | None:
        decisions: list[ScheduleDecision] = []
        for worker in workers:
            if (
                requirements.preferred_worker_id
                and worker.get("id") != requirements.preferred_worker_id
            ):
                continue
            if worker.get("status") not in {"online", "idle"}:
                continue
            if not self._telemetry_is_fresh(worker):
                continue
            capabilities = set(worker.get("capabilities", []))
            if not set(requirements.capabilities).issubset(capabilities):
                continue
            hardware = worker.get("hardware", {})
            gpu = hardware.get("gpu", {})
            memory = hardware.get("memory", {})
            total_vram, free_vram = self._memory_snapshot(worker)
            available_ram = int(
                memory.get("ram_available_mb") or hardware.get("ram_available_mb", 0)
            )
            if requirements.gpu and not gpu.get("available"):
                continue
            if total_vram < requirements.minimum_vram_mb:
                continue
            if free_vram < requirements.minimum_free_vram_mb:
                continue
            if hardware.get("ram_mb", 0) < requirements.minimum_ram_mb:
                continue
            if available_ram < requirements.minimum_available_ram_mb:
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
            if free_vram:
                score += min(10, free_vram / 2048)
                reason.append(f"{free_vram // 1024} GB free VRAM")
            decisions.append(ScheduleDecision(worker["id"], round(score, 1), reason))
        return max(decisions, key=lambda item: item.score, default=None)

    def assess(
        self, workers: list[dict[str, Any]], requirements: JobRequirements
    ) -> ScheduleAssessment:
        compatible = [worker for worker in workers if self._compatible(worker, requirements)]
        available = [
            worker for worker in compatible if worker.get("status") in {"online", "idle", "ready"}
        ]
        busy = [worker for worker in compatible if worker.get("status") == "busy"]
        offline = [worker for worker in compatible if worker.get("status") == "offline"]
        decision = self.select(available, requirements)
        if decision:
            status = WorkerCapacityStatus.WORKER_AVAILABLE
        elif busy:
            status = WorkerCapacityStatus.COMPATIBLE_WORKER_BUSY
        elif offline:
            status = WorkerCapacityStatus.WORKER_OFFLINE
        else:
            status = WorkerCapacityStatus.NO_COMPATIBLE_WORKER_EXISTS
        return ScheduleAssessment(
            status=status,
            decision=decision,
            compatible_count=len(compatible),
            available_count=len(available),
            busy_count=len(busy),
            offline_count=len(offline),
        )

    def _compatible(self, worker: dict[str, Any], requirements: JobRequirements) -> bool:
        if (
            requirements.preferred_worker_id
            and worker.get("id") != requirements.preferred_worker_id
        ):
            return False
        if not self._telemetry_is_fresh(worker):
            return False
        capabilities = set(worker.get("capabilities", []))
        if not set(requirements.capabilities).issubset(capabilities):
            return False
        hardware = worker.get("hardware", {})
        gpu = hardware.get("gpu", {})
        memory = hardware.get("memory", {})
        total_vram, free_vram = self._memory_snapshot(worker)
        available_ram = int(memory.get("ram_available_mb") or hardware.get("ram_available_mb", 0))
        if requirements.gpu and not gpu.get("available"):
            return False
        if total_vram < requirements.minimum_vram_mb:
            return False
        if free_vram < requirements.minimum_free_vram_mb:
            return False
        if hardware.get("ram_mb", 0) < requirements.minimum_ram_mb:
            return False
        if available_ram < requirements.minimum_available_ram_mb:
            return False
        ready_models = [
            item
            for item in worker.get("models", [])
            if item.get("loaded") and item.get("status", "ready") == "ready"
        ]
        if requirements.model and not any(
            item.get("id") == requirements.model for item in ready_models
        ):
            return False
        if requirements.runtime and not any(
            item.get("runtime") == requirements.runtime for item in ready_models
        ):
            return False
        if requirements.quantization and not any(
            item.get("quantization") == requirements.quantization for item in ready_models
        ):
            return False
        if requirements.task_category:
            best = max(
                (
                    float(item.get("task_scores", {}).get(requirements.task_category, 0))
                    for item in ready_models
                ),
                default=0,
            )
            if best < requirements.minimum_model_score:
                return False
        return True
