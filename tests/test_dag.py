import pytest

from aegaeon.protocol.schemas import PlanTask, TaskState
from aegaeon.tasks.dag import TaskDAG


def task(task_id: str, dependencies: list[str] | None = None) -> PlanTask:
    return PlanTask(
        id=task_id,
        title=task_id,
        description=task_id,
        dependencies=dependencies or [],
        suggested_agent="coder",
    )


def test_dag_readiness_and_blocking() -> None:
    dag = TaskDAG([task("plan"), task("code", ["plan"]), task("test", ["code"])])
    assert [item.id for item in dag.ready({})] == ["plan"]
    assert [item.id for item in dag.ready({"plan": TaskState.COMPLETED})] == ["code"]
    assert [item.id for item in dag.blocked({"code": TaskState.FAILED})] == ["test"]


def test_dag_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="cycle"):
        TaskDAG([task("one", ["two"]), task("two", ["one"])])
