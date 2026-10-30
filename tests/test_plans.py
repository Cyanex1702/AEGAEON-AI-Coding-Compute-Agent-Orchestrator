from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from aegaeon_lite.app import DEMO_REQUEST, App, suggested_checks
from aegaeon_lite.bundles import import_result, pack_result, read_archive, unpack_task
from aegaeon_lite.plans import demo_plan, validate_plan
from aegaeon_lite.workspace import git, snapshot, tree_digest


def wait_job(app, identifier):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        value = app.detail(identifier)
        if value["state"] not in {"queued", "running", "cancelling"}:
            return value
        time.sleep(0.05)
    raise AssertionError("Session did not finish")


def wait_plan(app, identifier):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        value = app.plans.detail(identifier)
        if value["state"] not in {"planning", "verifying"}:
            return value
        time.sleep(0.05)
    raise AssertionError("Plan operation did not finish")


@pytest.fixture
def planned(tmp_path):
    app = App(tmp_path / "app")
    project = app.create_demo()
    spec = demo_plan(DEMO_REQUEST, suggested_checks(Path(project["root"])))
    plan = app.plans.create(project["id"], spec)
    return app, project, plan


@pytest.mark.parametrize("issue", ["cycle", "unknown", "duplicate", "uncovered", "bad-task"])
def test_plan_rejects_invalid_graphs_and_tasks(issue):
    spec = demo_plan(DEMO_REQUEST, [])
    if issue == "cycle":
        spec["steps"][0]["depends_on"] = ["S2"]
    elif issue == "unknown":
        spec["steps"][0]["depends_on"] = ["S9"]
    elif issue == "duplicate":
        spec["steps"][1]["id"] = "S1"
    elif issue == "uncovered":
        spec["steps"][1]["requirements"] = ["R1"]
    else:
        spec["steps"][0]["task"]["max_actions"] = 1000
    with pytest.raises(ValueError):
        validate_plan(spec)


def test_approval_revision_dependency_and_stale_source_guards(planned):
    app, project, plan = planned
    with pytest.raises(ValueError, match="Approve"):
        app.plans.launch(plan["id"], "S1")
    revised = app.plans.edit(plan["id"], plan["spec"], 1)
    with pytest.raises(ValueError, match="revision"):
        app.plans.approve(plan["id"], 1)
    app.plans.approve(plan["id"], revised["revision"])
    with pytest.raises(ValueError, match="dependencies"):
        app.plans.launch(plan["id"], "S2")
    with pytest.raises(ValueError, match="Only a draft"):
        app.plans.edit(plan["id"], plan["spec"], revised["revision"])
    (Path(project["root"]) / "README.md").write_text("External change")
    with pytest.raises(ValueError, match="Original project changed"):
        app.plans.launch(plan["id"], "S1")


def test_two_step_local_plan_uses_accepted_source_and_recovers_after_restart(planned):
    app, project, plan = planned
    root = Path(project["root"])
    original = snapshot(root)
    app.plans.approve(plan["id"], 1)
    first = app.plans.launch(plan["id"], "S1", allow_checks=True)
    assert wait_job(app, first["id"])["report"]["status"] == "Checks passed"
    with pytest.raises(ValueError, match="dependencies"):
        app.plans.launch(plan["id"], "S2")
    with pytest.raises(ValueError, match="Enable local checks"):
        app.plans.accept(first["id"])
    app.plans.accept(first["id"], allow_checks=True)
    checkpoint = wait_plan(app, plan["id"])
    assert not checkpoint["error"], checkpoint
    assert checkpoint["spec"]["steps"][0]["state"] == "accepted"
    assert "return a + b" in (Path(checkpoint["root"]) / "calc.py").read_text()
    app = App(app.directory)
    assert app.plans.detail(plan["id"])["commit"] == checkpoint["commit"]
    second = app.plans.launch(plan["id"], "S2", allow_checks=True)
    exported = json.loads(read_archive(app.job_dir(second["id"]) / "task.zip")["manifest.json"])
    assert exported["base_commit"] == checkpoint["commit"]
    assert exported["files"] != original
    assert exported["task"]["plan_context"]["goal"] == plan["spec"]["goal"]
    assert wait_job(app, second["id"])["report"]["status"] == "Checks passed"
    app.plans.accept(second["id"], allow_checks=True)
    final = wait_plan(app, plan["id"])
    assert final["state"] == "complete", final
    assert all(r["reviewed"] for r in final["coverage"])
    assert snapshot(root) == original
    assert "add(2, 3) == 5" in (Path(final["root"]) / "README.md").read_text()
    patch = git(root, "diff", plan["base_commit"], final["commit"])
    assert "return a + b" in patch and "add(2, 3) == 5" in patch
    # The final branch contains both accepted commits; no manual cherry-pick is needed.
    assert git(root, "merge-base", checkpoint["commit"], final["commit"]) == checkpoint["commit"]
    (Path(final["root"]) / "calc.py").write_text("External checkpoint change")
    with pytest.raises(ValueError, match="Accepted checkpoint changed"):
        app.plans.assert_source(app.plans.load(plan["id"]))


def attach_result(app, job, scratch, *, fix=False, forged=False):
    root, manifest = unpack_task(app.job_dir(job["id"]) / "task.zip", scratch)
    if fix:
        (root / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    report = {
        "status": "Checks passed",
        "summary": "External claim",
        "unresolved": [],
        "checks": [],
    }
    if forged:
        report["checks"] = [
            {**check, "status": "passed", "source_digest": tree_digest(snapshot(root))}
            for check in manifest["task"]["checks"]
        ]
    bundle = app.directory / "uploads" / ("upload-" + ("a" if fix else "b") * 12 + ".zip")
    pack_result(root, manifest, report, bundle)
    app.plans.attach(job["id"], bundle.stem)
    return bundle


def test_notebook_claim_does_not_bypass_local_checks_and_retry_keeps_checkpoint(planned, tmp_path):
    app, _, plan = planned
    app.plans.approve(plan["id"], 1)
    exported = app.plans.launch(plan["id"], "S1", notebook=True)
    attach_result(app, exported, tmp_path / "bad", forged=True)
    app.plans.accept(exported["id"], allow_checks=True)
    rejected = wait_plan(app, plan["id"])
    assert "check failed" in rejected["error"]
    assert rejected["commit"] == plan["base_commit"]
    assert not rejected["runs"]["S1"]["accepted"]
    assert rejected["runs"]["S1"]["verification"][0]["status"] == "failed"
    app.plans.abandon(exported["id"])
    retry = app.plans.launch(plan["id"], "S1", notebook=True)
    assert retry["id"] != exported["id"]
    attach_result(app, retry, tmp_path / "good", fix=True)
    app.plans.accept(retry["id"], allow_checks=True)
    accepted = wait_plan(app, plan["id"])
    assert accepted["runs"]["S1"]["accepted"], accepted
    assert accepted["history"][0]["job_id"] == exported["id"]
    with pytest.raises(ValueError, match="replaced"):
        app.plans.accept(exported["id"], allow_checks=True)


def test_notebook_results_are_bound_to_exact_attempt(planned, tmp_path):
    app, _, plan = planned
    app.plans.approve(plan["id"], 1)
    first = app.plans.launch(plan["id"], "S1", notebook=True)
    bundle = attach_result(app, first, tmp_path / "result", fix=True)
    app.plans.abandon(first["id"])
    second = app.plans.launch(plan["id"], "S1", notebook=True)
    with pytest.raises(ValueError, match="different plan step or attempt"):
        app.plans.attach(second["id"], bundle.stem)


def test_manual_acceptance_and_restart_during_verification(planned, tmp_path):
    app, project, original = planned
    spec = copy.deepcopy(original["spec"])
    spec["requirements"] = spec["requirements"][:1]
    spec["steps"] = spec["steps"][:1]
    spec["steps"][0]["task"]["checks"] = []
    plan = app.plans.create(project["id"], spec)
    app.plans.approve(plan["id"], 1)
    job = app.plans.launch(plan["id"], "S1", notebook=True)
    attach_result(app, job, tmp_path / "manual", fix=True)
    with pytest.raises(ValueError, match="manual acceptance"):
        app.plans.accept(job["id"])
    # Simulate process loss after the Git import, before the plan journal records its candidate.
    candidate = import_result(
        Path(project["root"]),
        app.job_dir(job["id"]) / "result.zip",
        apply=True,
        staging_parent=app.plans.folder(plan["id"]) / "worktrees",
        allow_empty=True,
    )
    record = app.plans.load(plan["id"])
    record["state"] = "verifying"
    app.plans.save(record)
    app = App(app.directory)
    assert app.plans.detail(plan["id"])["state"] == "approved"
    app.plans.accept(job["id"], manual=True)
    final = wait_plan(app, plan["id"])
    assert final["state"] == "complete", final
    assert final["runs"]["S1"]["evidence"] == "Human review only"
    assert final["commit"] == candidate["commit"]


def test_cancel_acceptance_keeps_last_checkpoint(planned, tmp_path):
    app, project, original = planned
    spec = copy.deepcopy(original["spec"])
    spec["steps"][0]["task"]["checks"] = [
        {"id": "slow", "argv": ["python", "-c", "import time; time.sleep(30)"], "timeout": 40}
    ]
    plan = app.plans.create(project["id"], spec)
    app.plans.approve(plan["id"], 1)
    job = app.plans.launch(plan["id"], "S1", notebook=True)
    attach_result(app, job, tmp_path / "cancel", fix=True)
    app.plans.accept(job["id"], allow_checks=True)
    app.plans.stop(plan["id"])
    final = wait_plan(app, plan["id"])
    assert final["commit"] == plan["base_commit"]
    assert not final["runs"]["S1"]["accepted"]
    assert final["error"]


def test_empty_result_checks_run_in_isolation_even_when_they_mutate_source(planned, tmp_path):
    app, project, original = planned
    root = Path(project["root"])
    before = snapshot(root)
    spec = copy.deepcopy(original["spec"])
    spec["steps"][0]["task"]["checks"] = [
        {
            "id": "mutator",
            "argv": [
                "python",
                "-c",
                "from pathlib import Path; Path('README.md').write_text('changed by check')",
            ],
            "timeout": 10,
        }
    ]
    plan = app.plans.create(project["id"], spec)
    app.plans.approve(plan["id"], 1)
    job = app.plans.launch(plan["id"], "S1", notebook=True)
    attach_result(app, job, tmp_path / "empty")
    app.plans.accept(job["id"], allow_checks=True)
    result = wait_plan(app, plan["id"])
    assert snapshot(root) == before
    assert not result["runs"]["S1"]["accepted"]
    assert result["runs"]["S1"]["verification"][0]["source_changed"]


def test_generated_drafts_remain_unapproved_and_reject_invented_commands(planned, monkeypatch):
    app, _, plan = planned
    proposed = copy.deepcopy(plan["spec"])

    # Planner output is simulated here; actual provider HTTP behavior has separate tests.
    class Planner:
        def __init__(self, **kwargs):
            pass

        def complete(self, messages):
            return json.dumps(proposed)

    monkeypatch.setattr("aegaeon_lite.plans.ModelProvider", Planner)
    app.plans.generate(plan["id"], 1)
    draft = wait_plan(app, plan["id"])
    assert draft["state"] == "draft" and draft["revision"] == 2 and not draft["error"]
    proposed["steps"][0]["task"]["checks"] = [{"id": "invented", "argv": ["invented"]}]
    app.plans.generate(plan["id"], 2)
    rejected = wait_plan(app, plan["id"])
    assert "invented a check" in rejected["error"]
    assert rejected["revision"] == 2
    assert rejected["spec"] == draft["spec"]


def test_check_policy_validation_and_explicit_empty_semantics():
    original = demo_plan(DEMO_REQUEST, [])
    assert "acceptance_checks" not in validate_plan(original)["steps"][0]
    original["steps"][0]["acceptance_checks"] = []
    assert validate_plan(original)["steps"][0]["acceptance_checks"] == []
    original["steps"][0]["acceptance_checks"] = [{"id": "bad", "argv": "shell string"}]
    with pytest.raises(ValueError):
        validate_plan(original)


def single_step_policy(app, project, original, local):
    spec = copy.deepcopy(original["spec"])
    spec["steps"] = spec["steps"][:1]
    spec["requirements"] = spec["requirements"][:1]
    spec["steps"][0]["task"]["checks"] = [
        {"id": "cloud-gpu", "argv": ["cloud-only-executable-not-installed-here"]}
    ]
    spec["steps"][0]["acceptance_checks"] = local
    plan = app.plans.create(project["id"], spec)
    app.plans.approve(plan["id"], 1)
    return plan


def test_notebook_worker_commands_are_not_run_during_local_acceptance(planned, tmp_path):
    app, project, original = planned
    local = original["spec"]["steps"][0]["task"]["checks"]
    plan = single_step_policy(app, project, original, local)
    report = app.plans.preflight(plan["id"], "S1")
    assert report["status"] == "tools_found"
    assert report["source"] == "accepted_checkpoint"
    job = app.plans.launch(plan["id"], "S1", notebook=True)
    task = json.loads(read_archive(app.job_dir(job["id"]) / "task.zip")["manifest.json"])["task"]
    assert task["checks"][0]["id"] == "cloud-gpu"
    assert "acceptance_checks" not in task
    attach_result(app, job, tmp_path / "cloud", fix=True, forged=True)
    app.plans.accept(job["id"], allow_checks=True)
    final = wait_plan(app, plan["id"])
    assert final["state"] == "complete", final
    run = final["runs"]["S1"]
    assert [c["id"] for c in run["verification"]] == [local[0]["id"]]
    assert run["acceptance_policy"]["worker_checks"][0]["id"] == "cloud-gpu"
    assert "return a - b" in (Path(project["root"]) / "calc.py").read_text()


def test_failing_local_policy_blocks_a_passing_worker_report(planned, tmp_path):
    app, project, original = planned
    plan = single_step_policy(
        app,
        project,
        original,
        [{"id": "local-failure", "argv": ["python", "-c", "raise SystemExit(3)"]}],
    )
    job = app.plans.launch(plan["id"], "S1", notebook=True)
    attach_result(app, job, tmp_path / "failure", fix=True, forged=True)
    app.plans.accept(job["id"], allow_checks=True)
    final = wait_plan(app, plan["id"])
    assert not final["runs"]["S1"]["accepted"]
    assert final["runs"]["S1"]["verification"][0]["exit_code"] == 3
    assert final["commit"] == original["base_commit"]


def test_no_local_policy_requires_manual_review_even_with_worker_claims(planned, tmp_path):
    app, project, original = planned
    plan = single_step_policy(app, project, original, [])
    job = app.plans.launch(plan["id"], "S1", notebook=True)
    attach_result(app, job, tmp_path / "human", fix=True, forged=True)
    with pytest.raises(ValueError, match="manual acceptance"):
        app.plans.accept(job["id"], allow_checks=True)
    app.plans.accept(job["id"], manual=True)
    final = wait_plan(app, plan["id"])
    assert final["state"] == "complete", final
    assert final["runs"]["S1"]["verification"] == []
    assert final["runs"]["S1"]["evidence"] == "Human review only"


def test_setup_failure_can_be_retried_without_reimporting_result(planned, tmp_path):
    app, project, original = planned
    plan = single_step_policy(
        app,
        project,
        original,
        [{"id": "needs-folder", "cwd": "runtime-empty", "argv": ["python", "-c", "pass"]}],
    )
    job = app.plans.launch(plan["id"], "S1", notebook=True)
    attach_result(app, job, tmp_path / "setup", fix=True, forged=True)
    app.plans.accept(job["id"], allow_checks=True)
    failed = wait_plan(app, plan["id"])
    assert "needs setup" in failed["error"]
    run = failed["runs"]["S1"]
    assert run["preflight"]["status"] == "needs_setup"
    assert run["verification"] == []
    assert not run["accepted"]
    (Path(run["candidate"]["worktree"]) / "runtime-empty").mkdir()
    report = app.plans.preflight(plan["id"], "S1")
    assert report["source"] == "review_candidate" and report["status"] == "tools_found"
    app.plans.accept(job["id"], allow_checks=True)
    final = wait_plan(app, plan["id"])
    assert final["state"] == "complete", final
    assert final["runs"]["S1"]["candidate"]["commit"] == run["candidate"]["commit"]


def test_planner_cannot_replace_user_acceptance_commands(planned, monkeypatch):
    app, _, original = planned
    policy = [{"id": "approved-local", "argv": ["python", "-c", "pass"]}]
    edited = app.plans.edit_step_checks(
        original["id"], "S1", 1, original["spec"]["steps"][0]["task"]["checks"], policy
    )
    proposed = copy.deepcopy(edited["spec"])
    proposed["steps"][0]["acceptance_checks"] = [
        {"id": "not-authorized", "argv": ["must-not-be-added"]}
    ]

    class Planner:
        def __init__(self, **kwargs):
            pass

        def complete(self, messages):
            return json.dumps(proposed)

    monkeypatch.setattr("aegaeon_lite.plans.ModelProvider", Planner)
    app.plans.generate(original["id"], edited["revision"])
    draft = wait_plan(app, original["id"])
    assert not draft["error"]
    assert all(step["acceptance_checks"] == policy for step in draft["spec"]["steps"])
