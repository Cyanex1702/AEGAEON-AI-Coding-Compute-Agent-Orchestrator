"""Explicit endpoint probe and a tiny, repeatable coding benchmark.

The probe sends only a fixed prompt. The benchmark runs generated code and fixed
tests in a new folder; it is opt-in and is not a security sandbox or quality score.
"""

from __future__ import annotations

import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

from . import __version__
from .checks import run_check
from .session import Session
from .workspace import digest, save_json

PROBE_REPLY = {"action": "finish", "summary": "AEGAEON_READY", "unresolved": []}
SOURCE = "def add(a, b):\n    return a - b\n"
TESTS = """import unittest
from calc import add

class AdditionTests(unittest.TestCase):
    def test_positive(self):
        self.assertEqual(add(2, 3), 5)
    def test_negative(self):
        self.assertEqual(add(-2, -3), -5)
    def test_zero(self):
        self.assertEqual(add(0, 7), 7)
    def test_fraction(self):
        self.assertAlmostEqual(add(0.25, 0.5), 0.75)
"""
CHECK = {
    "id": "addition-regression",
    "argv": ["python", "-m", "unittest", "discover", "-s", "tests"],
    "timeout": 30,
    "cwd": ".",
}


def probe_model(provider) -> dict:
    """A valid /models list alone does not establish usable action JSON."""
    started = time.monotonic()
    try:
        reply = json.loads(
            provider.complete(
                [
                    {
                        "role": "system",
                        "content": "Return exactly the requested JSON object, no fences.",
                    },
                    {"role": "user", "content": "Return exactly: " + json.dumps(PROBE_REPLY)},
                ]
            )
        )
        passed = reply == PROBE_REPLY
        detail = (
            "Fixed action JSON matched." if passed else "Model returned unexpected action JSON."
        )
    except (OSError, ValueError, RuntimeError, TimeoutError):
        # Avoid persisting response content, request data or credentials in reports.
        passed = False
        detail = "Model request failed or returned invalid JSON. Check endpoint, alias and context."
    return {
        "status": "passed" if passed else "failed",
        "detail": detail,
        "seconds": round(time.monotonic() - started, 3),
        "notice": "Fixed prompt only; no project source or commands sent. "
        "Passing this probe does not establish coding quality.",
    }


def coding_benchmark(provider, destination: Path) -> dict:
    """Execute a built-in fixture only, never an existing user project."""
    destination = destination.absolute()
    if not destination.parent.is_dir():
        raise ValueError("benchmark destination needs an existing parent")
    destination.mkdir(exist_ok=False)
    (destination / "tests").mkdir()
    (destination / "calc.py").write_text(SOURCE, encoding="utf-8")
    test_file = destination / "tests/test_calc.py"
    test_file.write_text(TESTS, encoding="utf-8")
    test_digest = digest(test_file.read_bytes())
    before = run_check(destination, CHECK, True)
    started = time.monotonic()
    checkpoint = Session(
        destination,
        {
            "request": "Fix add in calc.py so it adds numbers correctly. "
            "Inspect source first. Preserve tests. Run addition-regression when ready.",
            "mode": "fix",
            "allowed_paths": ["calc.py"],
            "checks": [dict(CHECK)],
            "max_actions": 10,
            "max_changed_files": 1,
            "wall_seconds": 300,
        },
        provider,
        allow_checks=True,
    ).run()
    after = run_check(destination, CHECK, True)
    unchanged_tests = test_file.is_file() and digest(test_file.read_bytes()) == test_digest
    passed = (
        before["status"] == "failed"
        and after["status"] == "passed"
        and unchanged_tests
        and checkpoint["status"] == "Checks passed"
        and not checkpoint["unresolved"]
    )
    return {
        "status": "passed" if passed else "failed",
        "fixture": "addition-v1",
        "seconds": round(time.monotonic() - started, 3),
        "baseline_check": before["status"],
        "final_check": after["status"],
        "protected_tests_unchanged": unchanged_tests,
        "session_status": checkpoint["status"],
        "unresolved_count": len(checkpoint["unresolved"]),
        "notice": "Four fixed regression cases and one bounded session. "
        "This is smoke evidence, not a general coding-quality score. "
        "Generated code executed in a directory, not an OS sandbox.",
    }


def validate_model(provider, *, benchmark_dir: Path | None = None) -> dict:
    report = {
        "kind": "aegaeon-model-validation",
        "schema": 1,
        "app_version": __version__,
        "created": datetime.now(UTC).isoformat(),
        "environment": {"os": platform.system(), "python": platform.python_version()},
        "configuration": {
            "model": getattr(provider, "model", "unknown"),
            "context_tokens": getattr(provider, "context_tokens", None),
        },
        "probe": probe_model(provider),
        "benchmark": {"status": "not_run", "reason": "Not requested."},
    }
    if benchmark_dir is not None:
        if report["probe"]["status"] == "passed":
            report["benchmark"] = coding_benchmark(provider, benchmark_dir)
        else:
            report["benchmark"] = {"status": "not_run", "reason": "Endpoint probe failed."}
    report["status"] = (
        "passed"
        if report["probe"]["status"] == "passed"
        and (benchmark_dir is None or report["benchmark"]["status"] == "passed")
        else "failed"
    )
    return report


def save_report(path: Path, report: dict) -> None:
    save_json(path, report)
