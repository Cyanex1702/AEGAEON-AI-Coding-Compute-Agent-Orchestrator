from __future__ import annotations

import asyncio
import json
import operator
import tempfile
import time
import traceback
from collections.abc import Awaitable, Callable
from pathlib import Path

from aegaeon.agents.demo import DemoAgentRuntime
from aegaeon.failures import FailureCode, normalize_failure_code
from aegaeon.portable import sanitize_filename
from aegaeon.protocol.schemas import (
    ArtifactPayload,
    JobAssign,
    JobResult,
    ModelGeneratePayload,
    WorkerFailureDiagnostics,
)
from worker.model_runtime import LocalModelRuntime


class WorkerJobExecutor:
    """Executes typed jobs in disposable directories; arbitrary shell is intentionally absent."""

    def __init__(self, model_runtime: LocalModelRuntime | None = None) -> None:
        self.demo = DemoAgentRuntime()
        self.model_runtime = model_runtime

    async def execute(
        self,
        job: JobAssign,
        log: Callable[[str, str, dict[str, object]], Awaitable[None]] | None = None,
    ) -> JobResult:
        started = time.monotonic()
        stage = "QUEUED"
        try:
            await self._emit(log, stage, f"Starting {job.job_type}")
            if job.job_type in {"model.generate", "model.repair"}:
                stage = "GENERATION"
                return await self._run_model(job, log)
            stage = "UPLOAD"
            if job.job_type == "demo.generate_project":
                return self._generate_project(job)
            if job.job_type == "source.materialize":
                return self._materialize_source(job)
            if job.job_type == "system.calculate":
                return self._calculate(job)
            raise ValueError(f"Unsupported job: {job.job_type}")
        except Exception as exc:
            message = str(exc).strip() or repr(exc)
            classification = normalize_failure_code(type(exc).__name__, message)
            if classification == FailureCode.UNKNOWN:
                classification = (
                    FailureCode.MODEL_RUNTIME_ERROR
                    if stage == "GENERATION"
                    else FailureCode.DEPENDENCY_FAILURE
                    if isinstance(exc, (ImportError, ModuleNotFoundError))
                    else FailureCode.MODEL_RUNTIME_ERROR
                )
            if classification == FailureCode.CUDA_OOM and stage == "GENERATION":
                classification = FailureCode.GENERATION_CUDA_OOM
            retry_strategy = {
                FailureCode.CUDA_OOM: "REDUCE_CONTEXT_OR_MODEL",
                FailureCode.MODEL_LOAD_CUDA_OOM: "RETRY_WITH_LOWER_MEMORY_CONFIGURATION",
                FailureCode.GENERATION_CUDA_OOM: "ADAPTIVE_MEMORY_RECOVERY",
                FailureCode.TOKENIZER_ERROR: "DO_NOT_RETRY",
                FailureCode.INVALID_JSON: "REQUEST_STRUCTURED_CORRECTION",
                FailureCode.WORKER_TIMEOUT: "REASSIGN_WORKER",
                FailureCode.NETWORK_INTERRUPTION: "RECONNECT_AND_REASSIGN",
            }.get(classification, "MANUAL_REVIEW")
            diagnostics = WorkerFailureDiagnostics(
                error_type=type(exc).__name__,
                message=message,
                error_repr=repr(exc),
                traceback=traceback.format_exc(),
                stage=stage,
                classification=classification.value,
                elapsed_seconds=round(time.monotonic() - started, 3),
                requested_output_tokens=(
                    int(job.payload.get("constraints", {}).get("maximum_new_tokens", 0)) or None
                ),
                gpu=self._gpu_diagnostics(),
                retry_strategy=retry_strategy,
                recent_stage_history=[{"stage": stage, "message": message}],
            )
            await self._emit(
                log,
                stage,
                f"{diagnostics.error_type}: {message}",
                {"classification": classification.value},
            )
            return JobResult(
                type="job.failed",
                job_id=job.job_id,
                error=f"{diagnostics.error_type}: {message}",
                diagnostics=diagnostics,
            )

    @staticmethod
    async def _emit(
        log: Callable[[str, str, dict[str, object]], Awaitable[None]] | None,
        stage: str,
        message: str,
        telemetry: dict[str, object] | None = None,
    ) -> None:
        if log is not None:
            await log(stage, message, telemetry or {})

    @staticmethod
    def _gpu_diagnostics() -> dict[str, object]:
        try:
            import torch

            if not torch.cuda.is_available():
                return {"available": False}
            device = torch.cuda.current_device()
            return {
                "available": True,
                "device": torch.cuda.get_device_name(device),
                "allocated_mb": round(torch.cuda.memory_allocated(device) / 1_048_576, 2),
                "reserved_mb": round(torch.cuda.memory_reserved(device) / 1_048_576, 2),
                "peak_allocated_mb": round(torch.cuda.max_memory_allocated(device) / 1_048_576, 2),
            }
        except Exception as exc:
            return {"available": False, "telemetry_error": repr(exc)}

    async def _run_model(
        self,
        job: JobAssign,
        log: Callable[[str, str, dict[str, object]], Awaitable[None]] | None,
    ) -> JobResult:
        if self.model_runtime is None:
            raise RuntimeError("This worker has no local model runtime configured")
        await self._emit(log, "PROMPT_PREPARATION", "Validating generation payload")
        payload = ModelGeneratePayload.model_validate(job.payload)
        await self._emit(
            log,
            "GENERATION",
            f"Running {self.model_runtime.model_id}",
            {"requested_output_tokens": payload.constraints.maximum_new_tokens},
        )
        change_set = await asyncio.to_thread(self.model_runtime.generate, payload)
        await self._emit(
            log,
            "VALIDATION",
            f"Validated {len(change_set.files)} generated file(s)",
            {"file_count": len(change_set.files)},
        )
        result = self._materialize(job, change_set.as_file_map(), sanitize_filename(job.task_id))
        result.result.update(
            {
                "model": self.model_runtime.model_id,
                "telemetry": getattr(self.model_runtime, "last_metrics", {}),
                "runtime": self.model_runtime.runtime_name,
                "quantization": self.model_runtime.quantization,
                "summary": change_set.summary,
                "changes": [item.model_dump(mode="json") for item in change_set.changes],
                "test_commands": change_set.test_commands,
                "notes": change_set.notes,
                "contract_proposals": [
                    item.model_dump(mode="json") for item in change_set.contract_proposals
                ],
                "risks": change_set.risks,
                "blocked_by": change_set.blocked_by,
                "acceptance_evidence": change_set.acceptance_evidence,
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
