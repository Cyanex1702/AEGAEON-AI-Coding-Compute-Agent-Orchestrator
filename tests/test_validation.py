from __future__ import annotations

import json

import pytest

from aegaeon_lite import notebook
from aegaeon_lite.validation import PROBE_REPLY, coding_benchmark, probe_model, validate_model


class ScriptedModel:
    """Simulated decisions; fixture checks and the session host are real."""

    def __init__(self, fix=True):
        self.fix = fix
        self.calls = 0

    def complete(self, messages):
        if messages[0]["content"].startswith("Return exactly"):
            return json.dumps(PROBE_REPLY)
        if messages[0]["content"].startswith("Review"):
            return json.dumps({"summary": "Inspected", "unresolved": []})
        self.calls += 1
        if self.calls == 1:
            return json.dumps({"action": "read", "path": "calc.py"})
        if self.calls == 2 and self.fix:
            observation = json.loads(messages[-1]["content"])
            return json.dumps({"action": "edit", "path": "calc.py",
                               "expected_sha256": observation["sha256"],
                               "old": "return a - b", "new": "return a + b"})
        return json.dumps({"action": "finish", "summary": "Done", "unresolved": []})


def test_fixed_probe_and_no_benchmark_by_default(tmp_path):
    report = validate_model(ScriptedModel())
    assert report["status"] == "passed"
    assert report["probe"]["status"] == "passed"
    assert report["benchmark"]["status"] == "not_run"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("reply", ["not JSON", "[]", '{}', '{"action":"finish"}'])
def test_unusable_model_output_fails_probe(reply):
    class Model:
        def complete(self, messages):
            return reply
    assert probe_model(Model())["status"] == "failed"


def test_endpoint_failure_does_not_create_benchmark_or_leak_error(tmp_path):
    class Model:
        def complete(self, messages):
            raise RuntimeError("secret-key-do-not-log")
    target = tmp_path / "benchmark"
    report = validate_model(Model(), benchmark_dir=target)
    assert report["status"] == "failed" and not target.exists()
    assert report["benchmark"]["status"] == "not_run"
    assert "secret-key" not in json.dumps(report)


def test_real_fixture_execution_with_simulated_correct_edit(tmp_path):
    report = validate_model(ScriptedModel(), benchmark_dir=tmp_path / "bench")
    assert report["status"] == "passed", report
    benchmark = report["benchmark"]
    assert benchmark["baseline_check"] == "failed"
    assert benchmark["final_check"] == "passed"
    assert benchmark["protected_tests_unchanged"]


def test_finish_claim_without_fix_cannot_pass_benchmark(tmp_path):
    report = coding_benchmark(ScriptedModel(fix=False), tmp_path / "bench")
    assert report["status"] == "failed"
    assert report["final_check"] == "failed"


def test_benchmark_never_overwrites_existing_folder(tmp_path):
    sentinel = tmp_path / "keep.txt"
    sentinel.write_text("keep")
    with pytest.raises(FileExistsError):
        coding_benchmark(ScriptedModel(), tmp_path)
    assert sentinel.read_text() == "keep"
    assert not (tmp_path / "calc.py").exists()


def test_invalid_notebook_context_rejected_before_gpu_or_download(tmp_path, monkeypatch):
    def forbidden():
        raise AssertionError("GPU must not be touched for invalid context")
    monkeypatch.setattr(notebook, "gpu_info", forbidden)
    with pytest.raises(ValueError, match="context"):
        notebook.setup_model(tmp_path / "runtime", context=512)
    assert not (tmp_path / "runtime").exists()
