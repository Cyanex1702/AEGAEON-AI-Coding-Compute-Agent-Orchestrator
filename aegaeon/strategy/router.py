from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aegaeon.protocol.schemas import StrategyName


@dataclass(slots=True)
class EscalationPolicy:
    max_retries: int = 3
    allow_larger_model: bool = False
    allow_parallel_attempts: bool = False
    allow_strategy_change: bool = False


@dataclass(slots=True)
class StrategyDecision:
    strategy: StrategyName
    reason: str


class StrategyRouter:
    """MVP router with stable extension points for the four requested strategies."""

    availability = {
        StrategyName.SINGLE_MODEL: {
            "available": True,
            "description": "One model acts as planner, coder, reviewer, and integrator.",
        },
        StrategyName.SWARM: {
            "available": False,
            "description": "Requires multiple compatible model workers; interface only in MVP.",
        },
        StrategyName.SPECIALIZED: {
            "available": False,
            "description": "Specialized multimodal pipeline is reserved for a later release.",
        },
        StrategyName.DISTRIBUTED: {
            "available": False,
            "description": "Distributed inference requires a compatible stable worker group.",
        },
        StrategyName.CUSTOM: {
            "available": False,
            "description": "Visual custom workflows are on the roadmap.",
        },
    }

    def choose(
        self,
        requested: StrategyName,
        workers: list[dict[str, Any]],
        complexity: int = 50,
    ) -> StrategyDecision:
        del complexity
        if requested in {StrategyName.AUTO, StrategyName.SINGLE_MODEL}:
            reason = (
                "A compatible worker is online; one model can power every logical agent."
                if workers
                else "Demo execution is available locally; one model can power every logical agent."
            )
            return StrategyDecision(StrategyName.SINGLE_MODEL, reason)
        availability = self.availability.get(requested, {"available": False})
        if not availability["available"]:
            raise ValueError(str(availability.get("description", "Strategy is unavailable")))
        return StrategyDecision(requested, "Explicit strategy selected")

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "id": StrategyName.AUTO,
                "name": "Auto",
                "available": True,
                "description": "Select the best available execution strategy.",
            },
            *[
                {"id": name, "name": name.value.replace("_", " ").title(), **details}
                for name, details in self.availability.items()
            ],
        ]
