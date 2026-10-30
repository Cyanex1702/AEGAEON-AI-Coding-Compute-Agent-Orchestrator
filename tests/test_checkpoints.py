import copy
import json
import zipfile
from pathlib import Path

import pytest
from test_plans import wait_job, wait_plan

from aegaeon_lite.app import DEMO_REQUEST, App, DemoProvider, suggested_checks
from aegaeon_lite.bundles import pack_result, read_archive, unpack_task
from aegaeon_lite.checkpoints import (
    export_checkpoint,
    remaining_plan,
    restore_source,
)
from aegaeon_lite.plans import demo_plan
from aegaeon_lite.session import Session
from aegaeon_lite.workspace import digest, snapshot


def test_portable_checkpoint_resumes_remaining_work_in_fresh_app(tmp_path):
    app = App(tmp_path / "old-app")
    project = app.create_demo()
    spec = demo_plan(DEMO_REQUEST, suggested_checks(Path(project["root"])))
    plan = app.plans.create(project["id"], spec)
    app.plans.approve(plan["id"], 1)
    job = app.plans.launch(plan["id"], "S1", allow_checks=True)
    assert wait_job(app, job["id"])["report"]["status"] == "Checks passed"
    app.plans.accept(job["id"], allow_checks=True)
    accepted = wait_plan(app, plan["id"])
    assert not accepted["error"]
    saved = app.plans.backup(plan["id"])
    token = saved["download"].split("/")[-1]
    # New app has no access to the original project's worktrees or DB.
    fresh = App(tmp_path / "new-app")
    upload = fresh.upload_checkpoint(app.checkpoint_path(token).read_bytes())
    restored = fresh.restore_checkpoint(upload["upload_id"], str(tmp_path / "recovered source"))
    root = Path(restored["project"]["root"])
    assert snapshot(root) == snapshot(Path(accepted["root"]))
    assert not restored["project"]["demo"]
    draft = restored["plan"]
    assert draft["state"] == "draft" and draft["runs"] == {}
    assert [s["id"] for s in draft["spec"]["steps"]] == ["S2"]
    assert draft["spec"]["steps"][0]["depends_on"] == []
    assert draft["recovery"]["accepted_steps"] == ["S1"]
    fresh.plans.approve(draft["id"], 1)
    task = fresh.plans.launch(draft["id"], "S2", notebook=True)
    task_root, manifest = unpack_task(fresh.job_dir(task["id"]) / "task.zip", tmp_path / "worker")
    # Explicitly simulated notebook decisions; real edits, tests and imports.
    report = Session(
        task_root, manifest["task"], DemoProvider(task_root, manifest["task"]), allow_checks=True
    ).run()
    result = tmp_path / "continued.zip"
    pack_result(task_root, manifest, report, result)
    from aegaeon_lite.app import read_archive_bytes

    uploaded = read_archive_bytes(fresh, result.read_bytes())
    fresh.plans.attach(task["id"], uploaded["upload_id"])
    fresh.plans.accept(task["id"], allow_checks=True)
    final = wait_plan(fresh, draft["id"])
    assert final["state"] == "complete", final
    assert "add(2, 3) == 5" in (Path(final["root"]) / "README.md").read_text()
    assert "return a - b" in (Path(project["root"]) / "calc.py").read_text()
    assert App(fresh.directory).plans.detail(draft["id"])["state"] == "complete"


@pytest.fixture
def checkpoint(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "main.py").write_bytes(b"print('hello')\r\n")
    (root / ".env").write_text("TOKEN=excluded")
    spec = demo_plan(DEMO_REQUEST, [])
    path = tmp_path / "checkpoint.zip"
    export_checkpoint(root, spec, [], [], path, title="Example")
    return path, spec


@pytest.mark.parametrize("problem", ["checksum", "traversal", "extra", "case", "graph"])
def test_bad_checkpoint_is_rejected_before_destination_creation(checkpoint, tmp_path, problem):
    path, _ = checkpoint
    entries = read_archive(path)
    manifest = json.loads(entries["manifest.json"])
    if problem == "checksum":
        entries["source/main.py"] = b"tampered"
    elif problem == "traversal":
        entries["../escape"] = b"bad"
    elif problem == "extra":
        entries["unlisted.txt"] = b"bad"
    elif problem == "case":
        entries["source/MAIN.py"] = entries["source/main.py"]
        manifest["files"]["MAIN.py"] = digest(entries["source/MAIN.py"])
    else:
        manifest["accepted_steps"] = ["S2"]
    entries["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    destination = tmp_path / "never-created"
    with pytest.raises(ValueError):
        restore_source(path, destination)
    assert not destination.exists()


def test_restore_never_overwrites_and_preserves_ignored_crlf_source(checkpoint, tmp_path):
    path, spec = checkpoint
    existing = tmp_path / "existing"
    existing.mkdir()
    (existing / "keep.txt").write_text("keep")
    with pytest.raises(ValueError, match="never overwritten"):
        restore_source(path, existing)
    assert (existing / "keep.txt").read_text() == "keep"
    source = tmp_path / "ignored-source"
    source.mkdir()
    (source / ".gitignore").write_text("*\n")
    (source / ".gitattributes").write_text("*.py filter=must-not-run\n")
    (source / "main.py").write_bytes(b"print('exact bytes')\r\n")
    export_checkpoint(source, spec, ["S1", "S2"], [], path, title="Completed")
    restored = restore_source(path, tmp_path / "exact")
    assert restored["manifest"]["remaining"] is None
    assert snapshot(Path(restored["root"])) == snapshot(source)
    assert (Path(restored["root"]) / "main.py").read_bytes().endswith(b"\r\n")


def test_remaining_plan_does_not_mutate_original_or_inherit_run_flags(checkpoint):
    _, spec = checkpoint
    before = copy.deepcopy(spec)
    result = remaining_plan(spec, ["S1"])
    assert spec == before
    assert result["steps"][0]["id"] == "S2"
    assert result["steps"][0]["depends_on"] == []
    assert result["requirements"] == [
        {"id": "R2", "text": "README explains the add function with an example."}
    ]
