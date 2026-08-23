import pytest

from aegaeon.protocol.schemas import JobAssign, JobRequirements
from worker.executor import WorkerJobExecutor


@pytest.mark.asyncio
async def test_worker_job_uses_disposable_workspace() -> None:
    job = JobAssign(
        job_id="job-test",
        project_id="project-test",
        task_id="task-test",
        agent_role="coder",
        requirements=JobRequirements(capabilities=["python"]),
        instructions="Implement calculator",
        payload={
            "prompt": "Build a calculator with add subtract multiply divide",
            "stage": "implementation",
        },
    )
    result = await WorkerJobExecutor().execute(job)
    assert result.type == "job.completed"
    assert result.result["temporary_workspace_removed"] is True
    assert "app/main.py" in result.artifacts[0].files
