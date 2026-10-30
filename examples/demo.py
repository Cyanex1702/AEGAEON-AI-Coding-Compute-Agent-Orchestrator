"""Offline demonstration: simulated model decisions, real tests and Git transfer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aegaeon_lite.bundles import export_task, import_result, initialize, pack_result, unpack_task
from aegaeon_lite.session import Session
from aegaeon_lite.workspace import digest


class DemonstrationModel:
    def __init__(self, root):
        self.root = root
        self.calls = 0

    def complete(self, messages):
        if messages[0]["content"].startswith("Review"):
            return json.dumps(
                {"summary": "The addition fix matches the request.", "unresolved": []}
            )
        self.calls += 1
        if self.calls == 1:
            return json.dumps({"action": "read", "path": "calc.py"})
        if self.calls == 2:
            return json.dumps(
                {
                    "action": "edit",
                    "path": "calc.py",
                    "expected_sha256": digest((self.root / "calc.py").read_bytes()),
                    "old": "return a - b",
                    "new": "return a + b",
                }
            )
        return json.dumps({"action": "finish", "summary": "Fixed addition.", "unresolved": []})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    root = directory / "canonical"
    root.mkdir()
    (root / "tests").mkdir()
    (root / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
    (root / "tests" / "test_calc.py").write_bytes(
        b"import unittest\nfrom calc import add\n"
        b"class AdditionTests(unittest.TestCase):\n"
        b"    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n"
    )
    initialize(root)
    task = {
        "request": "Fix addition; preserve existing tests.",
        "mode": "fix",
        "allowed_paths": ["calc.py"],
        "checks": [
            {"id": "regression", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"]}
        ],
    }
    bundle = directory / "task.zip"
    export_task(root, task, bundle)
    worker, manifest = unpack_task(bundle, directory / "notebook")
    print("DEMONSTRATION: model actions are simulated; tests and Git operations are real.")
    result = Session(
        worker,
        task,
        DemonstrationModel(worker),
        allow_checks=True,
        on_event=lambda e: print(e["type"]),
    ).run()
    output = directory / "result.zip"
    pack_result(worker, manifest, result, output)
    review = import_result(root, output, apply=True)
    print(
        json.dumps(
            {
                "status": result["status"],
                "check_results": result["checks"],
                "branch": review["branch"],
                "worktree": review["worktree"],
                "canonical_unchanged": "a - b" in (root / "calc.py").read_text(),
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
