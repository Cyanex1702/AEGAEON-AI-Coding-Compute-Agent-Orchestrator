from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from aegaeon.artifacts.manager import ArtifactStorageError
from aegaeon.failures import (
    NON_RETRYABLE_FAILURES,
    ClassifiedFailure,
    FailureCode,
    normalize_failure_code,
    retry_strategy_for_failure,
)
from aegaeon.models.structured_output import (
    PORTABLE_JSON_PARSER_SOURCE,
    parse_json_object,
    parse_model_response,
    recover_completed_envelope,
)
from aegaeon.orchestration.service import DeterministicLeadEngineer
from aegaeon.orchestrator.orchestrator import Orchestrator
from aegaeon.portable import artifact_filename, sanitize_filename, validate_portable_filename
from aegaeon.protocol.schemas import ArtifactPayload
from aegaeon.security.redaction import redact_diagnostics
from aegaeon.workers.notebooks import ColabNotebookGenerator, NotebookSpec
from worker.model_runtime import generate_transformers_text


class FakeTensor:
    def __init__(self, values: list[list[int]]) -> None:
        self.values = values

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.values), len(self.values[0]))

    def __getitem__(self, index: int) -> list[int]:
        return self.values[index]


class FakeBatchEncoding(dict[str, FakeTensor]):
    def __init__(self) -> None:
        super().__init__(
            input_ids=FakeTensor([[1, 2, 3]]),
            attention_mask=FakeTensor([[1, 1, 1]]),
        )
        self.device: str | None = None

    def to(self, device: str) -> FakeBatchEncoding:
        self.device = device
        return self


class FakeTokenizer:
    eos_token_id = 99

    def __init__(self) -> None:
        self.inputs = FakeBatchEncoding()
        self.template_kwargs: dict[str, Any] = {}
        self.decoded: list[int] = []

    def apply_chat_template(self, messages: list[dict[str, str]], **kwargs: Any) -> Any:
        assert messages
        self.template_kwargs = kwargs
        return self.inputs

    def decode(self, generated: list[int], *, skip_special_tokens: bool) -> str:
        assert skip_special_tokens is True
        self.decoded = generated
        return "generated continuation"


class FakeModel:
    device = "cuda:0"

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    def generate(self, **kwargs: Any) -> FakeTensor:
        self.kwargs = kwargs
        return FakeTensor([[1, 2, 3, 10, 11]])


def test_batch_encoding_generation_preserves_structured_inputs_and_telemetry() -> None:
    tokenizer = FakeTokenizer()
    model = FakeModel()

    result = generate_transformers_text(
        tokenizer,
        model,
        [{"role": "user", "content": "Build it"}],
        64,
    )

    assert tokenizer.template_kwargs["return_dict"] is True
    assert tokenizer.template_kwargs["return_tensors"] == "pt"
    assert tokenizer.inputs.device == "cuda:0"
    assert model.kwargs["input_ids"] is tokenizer.inputs["input_ids"]
    assert model.kwargs["attention_mask"] is tokenizer.inputs["attention_mask"]
    assert model.kwargs["max_new_tokens"] == 64
    assert model.kwargs["min_new_tokens"] == 32
    assert result.prompt_tokens == 3
    assert result.generated_tokens == 2
    assert tokenizer.decoded == [10, 11]
    assert result.text == "generated continuation"


def test_generated_notebook_uses_batch_encoding_and_portable_artifact_name() -> None:
    notebook = ColabNotebookGenerator._notebook(
        NotebookSpec(
            project_id="project-test",
            worker_name="worker-1",
            model_id="Qwen/Qwen2.5-Coder-7B-Instruct",
            quantization="4bit",
            controller_url="https://controller.example",
            minimum_vram_mb=12_000,
        )
    )
    source = "\n".join(
        "".join(cell.get("source", []))
        for cell in notebook["cells"]  # type: ignore[index]
    )
    assert "return_dict=True" in source
    assert 'prompt_length = int(inputs["input_ids"].shape[-1])' in source
    assert "output = model.generate(**inputs, **generation)" in source
    assert '{"role":"assistant","content":text}' not in source
    assert "render_request(payload, last_error)" in source
    assert '"do_sample":False' in source
    assert '"min_new_tokens":min(32, maximum_new_tokens)' in source
    assert "recover_completed_envelope(text)" in source
    assert "truncated_output_recovered" in source
    assert "exactly one small, complete file" in source
    assert '"OUTPUT_TRUNCATED":"REDUCE_SCOPE_OR_SPLIT"' in source
    assert "inputs.shape[-1]" not in source
    assert 'portable_artifact_filename(job["task_id"])' in source
    assert "contract_proposals" in source
    assert "ord(char) < 32" in source
    assert "parse_model_response(text)" in source
    assert "AEGAEON_RESPONSE_V1" in source
    assert "\\x00-\\x1f" not in source
    assert "SESSION_ID = secrets.token_urlsafe(18)" in source
    assert '"session_id":SESSION_ID' in source
    assert '"protocol_version":2' in source
    assert 'job_message("job.ack", job)' in source
    assert 'terminal["result_hash"] = terminal_hash(terminal)' in source
    assert '"progress_percent":STATE["progress"]' in source
    assert "OUTBOX" in source
    assert "from websockets.exceptions import ConnectionClosed" in source
    assert "ping_interval=30, ping_timeout=180" in source
    assert "error.code == 4009" in source
    assert "Stopping this duplicate connection" in source
    assert "worker credential is invalid, expired, or revoked" in source
    for index, cell in enumerate(notebook["cells"]):  # type: ignore[index]
        if cell["cell_type"] != "code":
            continue
        cell_source = "".join(cell.get("source", []))
        assert "\x00" not in cell_source
        compile(cell_source, f"generated-cell-{index}", "exec")


def test_structured_output_repairs_literal_controls_and_model_wrappers() -> None:
    result = parse_json_object(
        'Here is the result:\n```json\n{"summary":"ok","files":[{"path":"app.py",'
        '"content":"print(1)\nprint(2)\t"}]}\n```'
    )
    assert result["files"][0]["content"] == "print(1)\nprint(2)\t"


def test_portable_notebook_parser_matches_controller_recovery() -> None:
    namespace: dict[str, Any] = {
        "json": __import__("json"),
        "re": __import__("re"),
    }
    exec(PORTABLE_JSON_PARSER_SOURCE, namespace)
    result = namespace["parse_json_object"](
        "preface {'summary': 'ok', 'files': [], 'changes': [],} trailing"
    )
    assert result["summary"] == "ok"


def test_file_envelope_keeps_source_code_out_of_json_serialization() -> None:
    result = parse_model_response(
        "AEGAEON_RESPONSE_V1\n"
        "SUMMARY: working calculator\n"
        "<<<FILE:src/calculator.js>>>\n"
        'const config = {"mode": "scientific"};\n'
        'const path = "C:\\\\display";\n'
        "<<<END_FILE>>>\n"
        "<<<FILE:index.html>>>\n<div>Calculator</div>\n<<<END_FILE>>>\n"
        "<<<END_RESPONSE>>>"
    )
    assert result["summary"] == "working calculator"
    assert result["files"][0]["content"].startswith('const config = {"mode"')
    assert result["files"][1]["path"] == "index.html"
    assert result["changes"] == []
    assert result["test_commands"] == []


def test_file_envelope_rejects_truncated_file_instead_of_applying_partial_code() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        parse_model_response("AEGAEON_RESPONSE_V1\n<<<FILE:app.py>>>\nprint('unfinished')\n")


def test_capped_output_recovery_keeps_closed_files_and_drops_partial_tail() -> None:
    recovered = recover_completed_envelope(
        "preface that must be removed\n"
        "AEGAEON_RESPONSE_V1\n"
        "SUMMARY: bounded shard\n"
        "<<<FILE:pyproject.toml>>>\n[project]\nname='demo'\n<<<END_FILE>>>\n"
        "<<<FILE:unfinished.py>>>\nprint('partial')"
    )

    assert recovered is not None
    assert recovered.startswith("AEGAEON_RESPONSE_V1")
    assert "unfinished.py" not in recovered
    assert recovered.endswith("<<<END_RESPONSE>>>")
    assert parse_model_response(recovered)["files"] == [
        {"path": "pyproject.toml", "content": "[project]\nname='demo'\n"}
    ]


def test_structured_generation_recovery_is_enforced_as_one_file() -> None:
    for failure in (
        FailureCode.INVALID_JSON.value,
        FailureCode.INVALID_MODEL_OUTPUT.value,
        FailureCode.OUTPUT_TRUNCATED.value,
    ):
        assert (
            Orchestrator._generation_file_limit(
                previous_code=failure,
                memory_failure=False,
                attempt=3,
                intelligence=100,
                maximum_new_tokens=3_072,
            )
            == 1
        )


def test_explicit_markdown_filename_blocks_are_recovered_as_a_last_resort() -> None:
    result = parse_model_response("app.py\n```python\nprint('ok')\n```")
    assert result["files"] == [{"path": "app.py", "content": "print('ok')\n"}]


def test_colloquial_calculator_prompt_produces_specific_canonical_contract() -> None:
    lead = DeterministicLeadEngineer()
    prompt = (
        "Want u to build me a advance calculator with a modern ui and should contain "
        "all the features needed for advanced math"
    )
    blueprint = lead.create_blueprint(prompt, lead.analyze_project(prompt))
    assert blueprint["project"] == "Calculator"
    assert blueprint["services"] == [
        {
            "name": "CalculatorService",
            "methods": [
                "evaluateExpression",
                "calculateScientificFunction",
                "manageMemory",
                "manageHistory",
                "convertAngleMode",
            ],
        }
    ]
    assert "ScientificPanel" in blueprint["ui"]["components"]
    assert blueprint["state"]["angleMode"] == "degrees|radians"


@pytest.mark.parametrize(
    ("filename", "accepted"),
    [
        ("foundation.json", True),
        ("project:run:foundation.json", False),
        ("../secret.json", False),
        (r"C:\temp\file.json", False),
        ("foo/bar.json", False),
        (r"foo\bar.json", False),
        ("CON.json", False),
        ("normal-artifact_123.json", True),
    ],
)
def test_portable_artifact_filename_validation(filename: str, accepted: bool) -> None:
    if accepted:
        assert validate_portable_filename(filename) == filename
        assert ArtifactPayload(type="json", filename=filename).filename == filename
    else:
        with pytest.raises(ValueError, match="INVALID_ARTIFACT_FILENAME"):
            validate_portable_filename(filename)
        with pytest.raises(ValidationError, match="INVALID_ARTIFACT_FILENAME"):
            ArtifactPayload(type="json", filename=filename)


def test_worker_sanitizes_internal_ids_without_losing_structured_identity() -> None:
    internal = "project-97d4615994:run-a2e289b63830:foundation"
    assert artifact_filename(internal) == ("project-97d4615994_run-a2e289b63830_foundation.json")
    assert sanitize_filename("CON") == "_CON"


class RecordingEvents:
    def __init__(self) -> None:
        self.items: list[tuple[str, dict[str, Any]]] = []

    async def publish(self, event_type: str, message: str, **kwargs: Any) -> None:
        self.items.append((event_type, {"message": message, **kwargs}))


class FlakyArtifacts:
    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls: list[object] = []

    def create(self, project_id: str, task_id: str, kind: str, name: str, content: object) -> Any:
        self.calls.append(content)
        if len(self.calls) <= self.failures:
            raise ArtifactStorageError("artifact write failed: simulated controller error")
        return SimpleNamespace(id="artifact-ok", filename=name, type=kind)


def test_controller_storage_retries_same_result_without_model_regeneration() -> None:
    orchestrator = object.__new__(Orchestrator)
    artifacts = FlakyArtifacts(failures=2)
    orchestrator.artifacts = artifacts
    orchestrator.events = RecordingEvents()
    generated_payload = {"files": {"app.py": "print('ok')"}}

    record = asyncio.run(
        orchestrator._store_artifact_with_retry(
            "project-test",
            "task-test",
            "source_bundle",
            "foundation.json",
            generated_payload,
        )
    )

    assert record.id == "artifact-ok"
    assert len(artifacts.calls) == 3
    assert all(item is generated_payload for item in artifacts.calls)
    assert [item[0] for item in orchestrator.events.items] == [
        "artifact.storage.retrying",
        "artifact.storage.retrying",
    ]


def test_exhausted_controller_storage_failure_is_non_retryable_generation_failure() -> None:
    orchestrator = object.__new__(Orchestrator)
    orchestrator.artifacts = FlakyArtifacts(failures=3)
    orchestrator.events = RecordingEvents()

    with pytest.raises(ClassifiedFailure) as raised:
        asyncio.run(
            orchestrator._store_artifact_with_retry(
                "project-test", "task-test", "json", "foundation.json", "{}"
            )
        )

    assert raised.value.code == FailureCode.ARTIFACT_STORAGE_FAILURE
    assert raised.value.diagnostics["retry_strategy"] == "RETRY_CONTROLLER_STORAGE_ONLY"


@pytest.mark.parametrize(
    ("message", "code", "strategy", "retryable"),
    [
        ("tokenizer vocabulary mismatch", FailureCode.TOKENIZER_ERROR, "DO_NOT_RETRY", False),
        ("CUDA out of memory", FailureCode.CUDA_OOM, "REDUCE_CONTEXT_OR_MODEL", True),
        (
            "invalid model JSON response",
            FailureCode.INVALID_JSON,
            "REGENERATE_WITH_FAILURE_EVIDENCE",
            True,
        ),
        (
            "network interrupted while uploading",
            FailureCode.NETWORK_INTERRUPTION,
            "REASSIGN_WORKER",
            True,
        ),
        (
            "artifact write failed: disk unavailable",
            FailureCode.ARTIFACT_STORAGE_FAILURE,
            "DO_NOT_REGENERATE",
            False,
        ),
        (
            "Git integration conflict on main",
            FailureCode.INTEGRATION_CONFLICT,
            "REPLAN_FROM_CURRENT_HEAD",
            True,
        ),
    ],
)
def test_failures_have_distinct_classifications_and_retry_strategies(
    message: str,
    code: FailureCode,
    strategy: str,
    retryable: bool,
) -> None:
    classified = normalize_failure_code("", message)
    assert classified == code
    assert retry_strategy_for_failure(classified) == strategy
    assert (classified not in NON_RETRYABLE_FAILURES) is retryable


def test_code_safe_parser_failures_are_not_misreported_as_json_errors() -> None:
    assert (
        normalize_failure_code("", "invalid model response: file envelope is incomplete")
        == FailureCode.INVALID_MODEL_OUTPUT
    )
    assert (
        retry_strategy_for_failure(FailureCode.INVALID_MODEL_OUTPUT)
        == "REGENERATE_WITH_FAILURE_EVIDENCE"
    )


def test_secret_redaction_preserves_safe_generation_token_telemetry() -> None:
    safe = redact_diagnostics(
        {
            "api_token": "sensitive",
            "authorization": "Bearer sensitive-value",
            "prompt_tokens": 128,
            "generated_tokens": 64,
            "requested_output_tokens": 512,
        }
    )

    assert safe["api_token"] == "[REDACTED]"
    assert safe["authorization"] == "[REDACTED]"
    assert safe["prompt_tokens"] == 128
    assert safe["generated_tokens"] == 64
    assert safe["requested_output_tokens"] == 512
