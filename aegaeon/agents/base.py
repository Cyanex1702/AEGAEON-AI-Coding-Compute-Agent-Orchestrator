from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Agent(ABC):
    role: str

    @abstractmethod
    async def run(self, instructions: str, context: dict[str, Any]) -> dict[str, Any]:
        pass


class PlannerAgent(Agent):
    role = "planner"


class CoderAgent(Agent):
    role = "coder"


class ReviewerAgent(Agent):
    role = "reviewer"


class IntegratorAgent(Agent):
    role = "integrator"
