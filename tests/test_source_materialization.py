import pytest

from aegaeon.protocol.schemas import JobAssign, JobRequirements
from worker.executor import WorkerJobExecutor


@pytest.mark.asyncio
async def test_worker_materializes_typed_source_bundle() -> None:
    job = JobAssign(
        job_id="job-source",
        job_type="source.materialize",
        project_id="project-test",
        task_id="task-test",
        agent_role="coder",
        requirements=JobRequirements(),
        instructions="Materialize model output",
        payload={"label": "implementation", "files": {"src/main.py": "VALUE = 1\n"}},
    )
    result = await WorkerJobExecutor().execute(job)
    assert result.type == "job.completed"
    assert result.artifacts[0].files == {"src/main.py": "VALUE = 1\n"}
    assert result.result["temporary_workspace_removed"] is True


@pytest.mark.asyncio
async def test_worker_rejects_escaping_source_path() -> None:
    job = JobAssign(
        job_id="job-escape",
        job_type="source.materialize",
        project_id="project-test",
        task_id="task-test",
        agent_role="coder",
        requirements=JobRequirements(),
        instructions="Reject unsafe output",
        payload={"files": {"../outside.py": "unsafe\n"}},
    )
    result = await WorkerJobExecutor().execute(job)
    assert result.type == "job.failed"
    assert "unsafe generated path" in (result.error or "")
