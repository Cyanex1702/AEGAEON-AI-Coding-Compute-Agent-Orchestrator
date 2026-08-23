from __future__ import annotations

import asyncio
import json
import operator
import tempfile
from pathlib import Path

from aegaeon.agents.demo import DemoAgentRuntime
from aegaeon.protocol.schemas import (
    ArtifactPayload,
    JobAssign,
    JobResult,
    ModelGeneratePayload,
)
from worker.model_runtime import LocalModelRuntime


class WorkerJobExecutor:
    """Executes typed jobs in disposable directories; arbitrary shell is intentionally absent."""

    def __init__(self, model_runtime: LocalModelRuntime | None = None) -> None:
        self.demo = DemoAgentRuntime()
        self.model_runtime = model_runtime

    async def execute(self, job: JobAssign) -> JobResult:
        try:
            if job.job_type in {"model.generate", "model.repair"}:
                return await self._run_model(job)
            if job.job_type == "demo.generate_project":
                return self._generate_project(job)
            if job.job_type == "source.materialize":
                return self._materialize_source(job)
            if job.job_type == "system.calculate":
                return self._calculate(job)
            return JobResult(
                type="job.failed", job_id=job.job_id, error=f"Unsupported job: {job.job_type}"
            )
        except Exception as exc:
            return JobResult(type="job.failed", job_id=job.job_id, error=str(exc))

    async def _run_model(self, job: JobAssign) -> JobResult:
        if self.model_runtime is None:
            raise RuntimeError("This worker has no local model runtime configured")
        payload = ModelGeneratePayload.model_validate(job.payload)
        change_set = await asyncio.to_thread(self.model_runtime.generate, payload)
        result = self._materialize(job, change_set.as_file_map(), job.task_id.replace(":", "-"))
        result.result.update(
            {
                "model": self.model_runtime.model_id,
                "runtime": self.model_runtime.runtime_name,
                "quantization": self.model_runtime.quantization,
                "summary": change_set.summary,
            }
        )
        return result

    def _generate_project(self, job: JobAssign) -> JobResult:
        prompt = str(job.payload.get("prompt", ""))
        stage = str(job.payload.get("stage", "implementation"))
        return self._materialize(job, self.demo.generate(prompt, stage), stage)

    def _materialize_source(self, job: JobAssign) -> JobResult:
        files = job.payload.get("files")
        if not isinstance(files, dict) or not files or len(files) > 200:
            raise ValueError("source.materialize requires 1 to 200 files")
        if not all(
            isinstance(path, str) and isinstance(content, str) for path, content in files.items()
        ):
            raise ValueError("source files must be a string-to-string mapping")
        total_bytes = sum(len(content.encode("utf-8")) for content in files.values())
        if total_bytes > 2_000_000:
            raise ValueError("source bundle exceeds the 2 MB job limit")
        label = str(job.payload.get("label", "generated"))
        return self._materialize(job, files, label)

    @staticmethod
    def _materialize(job: JobAssign, files: dict[str, str], label: str) -> JobResult:
        with tempfile.TemporaryDirectory(prefix=f"aegaeon-{job.job_id}-") as temporary:
            root = Path(temporary).resolve()
            for relative_name, content in files.items():
                if "\\" in relative_name or relative_name.startswith("."):
                    raise ValueError("unsafe generated path")
                relative = Path(relative_name)
                if relative.is_absolute() or ".." in relative.parts or ".git" in relative.parts:
                    raise ValueError("unsafe generated path")
                target = (root / relative).resolve()
                if not target.is_relative_to(root):
                    raise ValueError("generated path escapes job directory")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
            returned = {
                path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
                for path in root.rglob("*")
                if path.is_file()
            }
        return JobResult(
            type="job.completed",
            job_id=job.job_id,
            artifacts=[
                ArtifactPayload(
                    type="source_bundle", filename=f"{label}-files.json", files=returned
                )
            ],
            result={"files": sorted(returned), "temporary_workspace_removed": True},
        )

    @staticmethod
    def _calculate(job: JobAssign) -> JobResult:
        operations = {
            "add": operator.add,
            "subtract": operator.sub,
            "multiply": operator.mul,
            "divide": operator.truediv,
        }
        operation = str(job.payload.get("operation", "add"))
        if operation not in operations:
            raise ValueError("unsupported calculation")
        a = float(job.payload.get("a", 0))
        b = float(job.payload.get("b", 0))
        value = operations[operation](a, b)
        return JobResult(
            type="job.completed",
            job_id=job.job_id,
            artifacts=[
                ArtifactPayload(
                    type="json",
                    filename="calculation.json",
                    content=json.dumps({"result": value}),
                )
            ],
            result={"value": value},
        )
