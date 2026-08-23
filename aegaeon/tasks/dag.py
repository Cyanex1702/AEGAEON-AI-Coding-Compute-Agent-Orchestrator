from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Iterable

from aegaeon.protocol.schemas import PlanTask, TaskState


class TaskDAG:
    """Validated task graph with readiness and cycle detection."""

    def __init__(self, tasks: Iterable[PlanTask]) -> None:
        self.tasks = {task.id: task for task in tasks}
        self._validate()

    def _validate(self) -> None:
        incoming = {task_id: 0 for task_id in self.tasks}
        outgoing: dict[str, list[str]] = defaultdict(list)
        for task in self.tasks.values():
            for dependency in task.dependencies:
                if dependency not in self.tasks:
                    raise ValueError(f"unknown dependency {dependency}")
                incoming[task.id] += 1
                outgoing[dependency].append(task.id)
        queue = deque(task_id for task_id, count in incoming.items() if count == 0)
        visited = 0
        while queue:
            current = queue.popleft()
            visited += 1
            for dependent in outgoing[current]:
                incoming[dependent] -= 1
                if incoming[dependent] == 0:
                    queue.append(dependent)
        if visited != len(self.tasks):
            raise ValueError("task graph contains a cycle")

    def ready(self, states: dict[str, TaskState | str]) -> list[PlanTask]:
        complete = {
            task_id for task_id, state in states.items() if TaskState(state) == TaskState.COMPLETED
        }
        return [
            task
            for task in self.tasks.values()
            if TaskState(states.get(task.id, TaskState.PENDING))
            in {TaskState.PENDING, TaskState.READY, TaskState.RETRYING}
            and set(task.dependencies).issubset(complete)
        ]

    def blocked(self, states: dict[str, TaskState | str]) -> list[PlanTask]:
        failed = {
            task_id
            for task_id, state in states.items()
            if TaskState(state) in {TaskState.FAILED, TaskState.BLOCKED, TaskState.CANCELLED}
        }
        return [
            task
            for task in self.tasks.values()
            if TaskState(states.get(task.id, TaskState.PENDING)) == TaskState.PENDING
            and bool(set(task.dependencies) & failed)
        ]
