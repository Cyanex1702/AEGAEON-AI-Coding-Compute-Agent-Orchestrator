from __future__ import annotations

from enum import StrEnum
from typing import Any


class FailureCode(StrEnum):
    INVALID_JSON = "INVALID_JSON"
    INVALID_MODEL_OUTPUT = "INVALID_MODEL_OUTPUT"
    RESULT_INTEGRITY_FAILURE = "RESULT_INTEGRITY_FAILURE"
    OUTPUT_TRUNCATED = "OUTPUT_TRUNCATED"
    OUTPUT_TOO_LARGE = "OUTPUT_TOO_LARGE"
    UNSAFE_PATH = "UNSAFE_PATH"
    INVALID_ARTIFACT_FILENAME = "INVALID_ARTIFACT_FILENAME"
    ARTIFACT_STORAGE_FAILURE = "ARTIFACT_STORAGE_FAILURE"
    CONTROLLER_POST_PROCESSING_FAILURE = "CONTROLLER_POST_PROCESSING_FAILURE"
    INTEGRATION_CONFLICT = "INTEGRATION_CONFLICT"
    NETWORK_INTERRUPTION = "NETWORK_INTERRUPTION"
    ARTIFACT_PATH_ERROR = "ARTIFACT_PATH_ERROR"
    ARTIFACT_WRITE_ERROR = "ARTIFACT_WRITE_ERROR"
    CONTRACT_VIOLATION = "CONTRACT_VIOLATION"
    CONTRACT_PROPOSAL_REQUIRED = "CONTRACT_PROPOSAL_REQUIRED"
    CONTRACT_PROPOSAL_REJECTED = "CONTRACT_PROPOSAL_REJECTED"
    CONTRACT_PROPOSAL_INVALID = "CONTRACT_PROPOSAL_INVALID"
    CONTEXT_TOO_LARGE = "CONTEXT_TOO_LARGE"
    CONTRACT_MATERIALIZATION_FAILURE = "CONTRACT_MATERIALIZATION_FAILURE"
    STALE_CONTRACT_VERSION = "STALE_CONTRACT_VERSION"
    STALE_BASE_COMMIT = "STALE_BASE_COMMIT"
    GIT_ERROR = "GIT_ERROR"
    CUDA_OOM = "CUDA_OOM"
    MODEL_LOAD_CUDA_OOM = "MODEL_LOAD_CUDA_OOM"
    GENERATION_CUDA_OOM = "GENERATION_CUDA_OOM"
    TOKENIZER_ERROR = "TOKENIZER_ERROR"
    MODEL_RUNTIME_ERROR = "MODEL_RUNTIME_ERROR"
    WORKER_DISCONNECTED = "WORKER_DISCONNECTED"
    WORKER_TIMEOUT = "WORKER_TIMEOUT"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    BUILD_FAILURE = "BUILD_FAILURE"
    TYPECHECK_FAILURE = "TYPECHECK_FAILURE"
    NO_COMPATIBLE_WORKER = "NO_COMPATIBLE_WORKER"
    WORKER_BUSY = "WORKER_BUSY"
    UNKNOWN = "UNKNOWN"


_ALIASES = {
    "oom": FailureCode.CUDA_OOM,
    "timeout": FailureCode.WORKER_TIMEOUT,
    "dependency_failure": FailureCode.DEPENDENCY_FAILURE,
    "validation_failure": FailureCode.INVALID_JSON,
    "inference_failure": FailureCode.MODEL_RUNTIME_ERROR,
    "worker_failure": FailureCode.MODEL_RUNTIME_ERROR,
    "artifact_storage_failure": FailureCode.ARTIFACT_STORAGE_FAILURE,
    "invalid_artifact_filename": FailureCode.INVALID_ARTIFACT_FILENAME,
    "integration_conflict": FailureCode.INTEGRATION_CONFLICT,
}


class ClassifiedFailure(RuntimeError):
    def __init__(self, code: FailureCode, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.diagnostics = diagnostics or {}


def normalize_failure_code(value: object, message: str = "") -> FailureCode:
    raw = str(value or "").strip()
    if raw in FailureCode._value2member_map_:
        return FailureCode(raw)
    if raw.lower() in _ALIASES:
        return _ALIASES[raw.lower()]
    lowered = message.lower()
    if "invalid_artifact_filename" in lowered or "reserved windows device" in lowered:
        return FailureCode.INVALID_ARTIFACT_FILENAME
    if "artifact write failed" in lowered or "artifact directory creation failed" in lowered:
        return FailureCode.ARTIFACT_STORAGE_FAILURE
    if "protected contract path" in lowered or "contract proposal required" in lowered:
        return FailureCode.CONTRACT_PROPOSAL_REQUIRED
    if "stale contract" in lowered:
        return FailureCode.STALE_CONTRACT_VERSION
    if "contract materialization" in lowered:
        return FailureCode.CONTRACT_MATERIALIZATION_FAILURE
    if "contract proposal" in lowered and "rejected" in lowered:
        return FailureCode.CONTRACT_PROPOSAL_REJECTED
    if "contract" in lowered and "violation" in lowered:
        return FailureCode.CONTRACT_VIOLATION
    if "integration conflict" in lowered or "merge produced conflict" in lowered:
        return FailureCode.INTEGRATION_CONFLICT
    if "network" in lowered and ("interrupt" in lowered or "disconnect" in lowered):
        return FailureCode.NETWORK_INTERRUPTION
    if "model_load_cuda_oom" in lowered:
        return FailureCode.MODEL_LOAD_CUDA_OOM
    if "generation_cuda_oom" in lowered:
        return FailureCode.GENERATION_CUDA_OOM
    if "prompt_memory_limit" in lowered or "context_too_large" in lowered:
        return FailureCode.CONTEXT_TOO_LARGE
    if "out of memory" in lowered or "cuda oom" in lowered:
        return FailureCode.CUDA_OOM
    if "tokenizer" in lowered:
        return FailureCode.TOKENIZER_ERROR
    if "truncat" in lowered or "finish_reason" in lowered and "length" in lowered:
        return FailureCode.OUTPUT_TRUNCATED
    if "too large" in lowered or "result limit" in lowered:
        return FailureCode.OUTPUT_TOO_LARGE
    if "unsafe" in lowered or "escapes" in lowered:
        return FailureCode.UNSAFE_PATH
    if "json" in lowered:
        return FailureCode.INVALID_JSON
    if "model response" in lowered or "model output" in lowered or "validation" in lowered:
        return FailureCode.INVALID_MODEL_OUTPUT
    if "disconnect" in lowered:
        return FailureCode.WORKER_DISCONNECTED
    if "timeout" in lowered or "timed out" in lowered:
        return FailureCode.WORKER_TIMEOUT
    return FailureCode.UNKNOWN


def retry_strategy_for_failure(code: FailureCode) -> str:
    """Return the controller action without conflating generation and persistence retries."""
    if code in {
        FailureCode.INVALID_ARTIFACT_FILENAME,
        FailureCode.ARTIFACT_STORAGE_FAILURE,
        FailureCode.CONTROLLER_POST_PROCESSING_FAILURE,
    }:
        return "DO_NOT_REGENERATE"
    if code == FailureCode.CONTRACT_PROPOSAL_REQUIRED:
        return "REVIEW_CONTRACT_PROPOSAL"
    if code == FailureCode.CONTRACT_PROPOSAL_REJECTED:
        return "CREATE_CONTRACT_REPAIR_TASK"
    if code == FailureCode.CONTRACT_MATERIALIZATION_FAILURE:
        return "REPAIR_CONTROLLER_MATERIALIZATION"
    if code == FailureCode.STALE_CONTRACT_VERSION:
        return "REFRESH_CONTRACT_CONTEXT"
    if code == FailureCode.CONTRACT_VIOLATION:
        return "REPAIR_AGAINST_CANONICAL_CONTRACT"
    if code == FailureCode.TOKENIZER_ERROR:
        return "DO_NOT_RETRY"
    if code in {
        FailureCode.NETWORK_INTERRUPTION,
        FailureCode.WORKER_DISCONNECTED,
        FailureCode.WORKER_TIMEOUT,
    }:
        return "REASSIGN_WORKER"
    if code in {
        FailureCode.CUDA_OOM,
        FailureCode.CONTEXT_TOO_LARGE,
        FailureCode.MODEL_LOAD_CUDA_OOM,
        FailureCode.GENERATION_CUDA_OOM,
    }:
        return "REDUCE_CONTEXT_OR_MODEL"
    if code == FailureCode.INTEGRATION_CONFLICT:
        return "REPLAN_FROM_CURRENT_HEAD"
    if code in {FailureCode.INVALID_JSON, FailureCode.INVALID_MODEL_OUTPUT}:
        return "REGENERATE_WITH_FAILURE_EVIDENCE"
    return "REGENERATE_WITH_FAILURE_EVIDENCE"


NON_RETRYABLE_FAILURES = {
    FailureCode.UNSAFE_PATH,
    FailureCode.INVALID_ARTIFACT_FILENAME,
    FailureCode.ARTIFACT_STORAGE_FAILURE,
    FailureCode.CONTROLLER_POST_PROCESSING_FAILURE,
    FailureCode.TOKENIZER_ERROR,
    FailureCode.NO_COMPATIBLE_WORKER,
    FailureCode.CONTRACT_VIOLATION,
    FailureCode.CONTRACT_PROPOSAL_REQUIRED,
    FailureCode.CONTRACT_PROPOSAL_REJECTED,
    FailureCode.CONTRACT_PROPOSAL_INVALID,
    FailureCode.CONTRACT_MATERIALIZATION_FAILURE,
    FailureCode.STALE_CONTRACT_VERSION,
}
