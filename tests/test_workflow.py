from __future__ import annotations

import json
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest

from aegaeon_lite.bundles import (
    export_task,
    import_result,
    pack_result,
    read_archive,
    restore_checkpoint,
    unpack_task,
)
from aegaeon_lite.checks import run_check
from aegaeon_lite.session import Session
from aegaeon_lite.workspace import digest, safe_file, snapshot, valid_path


class ScriptedModel:
    def __init__(self, root):
        self.root = root
        self.index = 0

    def complete(self, messages):
        if messages[0]["content"].startswith("Review"):
            return json.dumps(
                {"summary": "The arithmetic fix matches the request.", "unresolved": []}
            )
        self.index += 1
        if self.index == 1:
            return json.dumps({"action": "read", "path": "calc.py"})
        if self.index == 2:
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


def test_notebook_roundtrip_real_checks_and_separate_branch(project, task, tmp_path):
    bundle = tmp_path / "task.zip"
    manifest = export_task(project, task, bundle)
    assert "source/.env" not in read_archive(bundle)
    root, imported = unpack_task(bundle, tmp_path / "notebook")
    result = Session(root, task, ScriptedModel(root), allow_checks=True).run()
    assert result["status"] == "Checks passed"
    output = tmp_path / "result.zip"
    pack_result(root, imported, result, output)
    preview = import_result(project, output)
    assert not preview["applied"] and "return a + b" in preview["diff"]
    applied = import_result(project, output, apply=True)
    assert applied["applied"]
    assert (project / "calc.py").read_text().endswith("return a - b\n")
    assert Path(applied["worktree"]).joinpath("calc.py").read_text().endswith("return a + b\n")
    assert run_check(Path(applied["worktree"]), task["checks"][0], True)["status"] == "passed"
    with pytest.raises(ValueError, match="duplicate"):
        import_result(project, output, apply=True)
    assert manifest["task_id"] == imported["task_id"]


def test_downloaded_checkpoint_survives_runtime_loss(project, task, tmp_path):
    bundle = tmp_path / "task.zip"
    export_task(project, task, bundle)
    root, manifest = unpack_task(bundle, tmp_path / "runtime-one")
    result = Session(root, task, ScriptedModel(root), allow_checks=False).run()
    output = tmp_path / "checkpoint.zip"
    pack_result(root, manifest, result, output)
    fresh, original = unpack_task(bundle, tmp_path / "runtime-two")
    restored = restore_checkpoint(fresh, original, output)
    assert (fresh / "calc.py").read_text().endswith("return a + b\n")
    assert restored["status"] == "Changes proposed"


def test_stale_result_and_checksum_tampering_rejected(project, task, tmp_path):
    bundle = tmp_path / "task.zip"
    export_task(project, task, bundle)
    root, manifest = unpack_task(bundle, tmp_path / "worker")
    result = Session(root, task, ScriptedModel(root)).run()
    output = tmp_path / "result.zip"
    pack_result(root, manifest, result, output)
    entries = read_archive(output)
    entries["changes/calc.py"] = b"tampered\n"
    corrupt = tmp_path / "corrupt.zip"
    with zipfile.ZipFile(corrupt, "w") as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    with pytest.raises(ValueError, match="checksum"):
        import_result(project, corrupt)
    (project / "calc.py").write_text("different source\n")
    with pytest.raises(ValueError, match="conflict"):
        import_result(project, output)


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/escape",
        "a\\b",
        "a/../b",
        "CON.txt",
        "a//b",
        "a./b",
        "a\0b",
        "a:b",
    ],
)
def test_portable_paths_reject_unsafe_names(name):
    with pytest.raises(ValueError):
        valid_path(name)


def test_archive_symlink_and_case_collision_rejected(tmp_path):
    link = tmp_path / "link.zip"
    entry = zipfile.ZipInfo("source/link")
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(link, "w") as archive:
        archive.writestr(entry, "../outside")
    with pytest.raises(ValueError, match="symlink"):
        read_archive(link)
    collision = tmp_path / "case.zip"
    with zipfile.ZipFile(collision, "w") as archive:
        archive.writestr("a.py", "a")
        archive.writestr("A.py", "b")
    with pytest.raises(ValueError, match="case-colliding"):
        read_archive(collision)


def test_tools_protect_tests_scope_stale_edits_and_ask_mode(project, task):
    session = Session(project, task, None)
    with pytest.raises(ValueError, match="outside task scope"):
        session.tool({"action": "edit", "path": "other.py", "old": "", "new": "x"})
    with pytest.raises(ValueError, match="stale edit"):
        session.tool(
            {
                "action": "edit",
                "path": "calc.py",
                "expected_sha256": "x",
                "old": "return a - b",
                "new": "return a + b",
            }
        )
    wide = Session(project, {**task, "allowed_paths": ["**"]}, None)
    with pytest.raises(ValueError, match="protected"):
        wide.tool({"action": "edit", "path": "tests/test_calc.py", "old": "x", "new": "y"})
    readonly = Session(project, {**task, "mode": "ask"}, None)
    with pytest.raises(ValueError, match="cannot edit"):
        readonly.tool({"action": "edit", "path": "calc.py"})
    with pytest.raises(ValueError):
        safe_file(project, ".env")


def test_no_false_pass_when_checks_skipped(project, task):
    result = Session(project, task, ScriptedModel(project), allow_checks=False).run()
    assert result["status"] == "Changes proposed"
    assert all(r["status"] == "not_run" for r in result["checks"])


def test_failed_checks_get_two_repairs_then_stop(project, task):
    class Finisher:
        def complete(self, messages):
            if messages[0]["content"].startswith("Review"):
                return '{"summary":"Still broken","unresolved":["Addition is wrong"]}'
            return '{"action":"finish","summary":"Done","unresolved":[]}'

    session = Session(project, task, Finisher(), allow_checks=True)
    result = session.run()
    assert result["status"] == "Partial"
    assert len([e for e in session.history if e["type"] == "repair"]) == 2
    assert "Required checks still fail" in result["unresolved"]


def test_command_output_is_bounded_and_timeout_is_recorded(project):
    verbose = {"id": "verbose", "argv": ["python", "-c", "print('x' * 100000)"], "timeout": 5}
    report = run_check(project, verbose, True)
    assert report["status"] == "passed" and len(report["output"]) <= 32_000
    slow = {"id": "slow", "argv": ["python", "-c", "import time; time.sleep(30)"], "timeout": 1}
    report = run_check(project, slow, True)
    assert report["status"] == "failed" and report["timed_out"]


def test_model_cannot_invent_check_command(project, task):
    with pytest.raises(ValueError, match="unknown check"):
        Session(project, task, None).tool({"action": "check", "id": "invented"})


def test_uncommitted_source_rejected_at_export(project, task, tmp_path):
    (project / "calc.py").write_text("uncommitted\n")
    with pytest.raises(ValueError, match="differs"):
        export_task(project, task, tmp_path / "task.zip")


def test_source_mutating_check_cannot_pass(project):
    report = run_check(
        project,
        {
            "id": "mutator",
            "argv": [
                "python",
                "-c",
                "from pathlib import Path; Path('calc.py').write_text('changed')",
            ],
        },
        True,
    )
    assert report["status"] == "failed" and report["source_changed"]


def test_new_head_rejects_old_result(project, task, tmp_path):
    bundle = tmp_path / "task.zip"
    export_task(project, task, bundle)
    root, manifest = unpack_task(bundle, tmp_path / "worker")
    checkpoint = Session(root, task, ScriptedModel(root)).run()
    output = tmp_path / "result.zip"
    pack_result(root, manifest, checkpoint, output)
    subprocess.run(
        [
            "git",
            "-C",
            str(project),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@localhost",
            "commit",
            "--allow-empty",
            "-m",
            "new",
        ],
        check=True,
        capture_output=True,
    )
    with pytest.raises(ValueError, match="HEAD changed"):
        import_result(project, output)


def test_file_budget_enforced(project, task):
    session = Session(project, {**task, "allowed_paths": ["**"], "max_changed_files": 1}, None)
    session.tool(
        {"action": "edit", "path": "new.py", "expected_sha256": None, "old": "", "new": "x = 1\n"}
    )
    with pytest.raises(ValueError, match="budget"):
        session.tool({"action": "edit", "path": "second.py", "old": "", "new": "x = 2\n"})
    assert "second.py" not in snapshot(project)


def test_ask_never_runs_declared_checks(project, task):
    class Answer:
        def complete(self, messages):
            return '{"action":"finish","summary":"Addition uses subtraction.","unresolved":[]}'

    session = Session(project, {**task, "mode": "ask"}, Answer(), allow_checks=True)
    result = session.run()
    assert result["status"] == "Answer" and result["checks"] == []
    with pytest.raises(ValueError, match="cannot execute"):
        session.tool({"action": "check", "id": "regression"})


def test_malformed_model_output_stops_without_edits(project, task):
    class Malformed:
        def complete(self, messages):
            return '{"action":"edit","new":'

    before = snapshot(project)
    result = Session(project, task, Malformed()).run()
    assert result["status"] == "Partial" and snapshot(project) == before


def test_restore_rejects_different_task(project, task, tmp_path):
    bundle = tmp_path / "first.zip"
    export_task(project, task, bundle)
    root, manifest = unpack_task(bundle, tmp_path / "one")
    result = Session(root, task, ScriptedModel(root)).run()
    checkpoint = tmp_path / "result.zip"
    pack_result(root, manifest, result, checkpoint)
    other = tmp_path / "second.zip"
    export_task(project, {**task, "request": "Different request"}, other)
    fresh, other_manifest = unpack_task(other, tmp_path / "two")
    with pytest.raises(ValueError, match="different task"):
        restore_checkpoint(fresh, other_manifest, checkpoint)
