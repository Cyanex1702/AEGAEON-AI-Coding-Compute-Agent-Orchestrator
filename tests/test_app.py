from __future__ import annotations

import http.cookiejar
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from aegaeon_lite.app import DEMO_REQUEST, App, make_server, suggested_checks
from aegaeon_lite.checks import run_check
from aegaeon_lite.session import Session
from aegaeon_lite.workspace import digest


@pytest.fixture
def app_server(tmp_path):
    app = App(tmp_path / "app")
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"

    def request(route, value=None, *, authenticated=True, headers=None, raw=None):
        body = raw if raw is not None else json.dumps(value).encode() if value is not None else None
        supplied = {"X-Aegaeon-Token": app.token} if authenticated else {}
        req = urllib.request.Request(
            base + route, data=body, headers={**supplied, **(headers or {})}
        )
        with urllib.request.urlopen(req, timeout=60) as response:
            data = response.read()
            return (
                json.loads(data)
                if response.headers.get_content_type() == "application/json"
                else data
            )

    yield app, request
    server.shutdown()
    server.server_close()
    thread.join(timeout=5)


def test_static_app_and_authenticated_api(app_server):
    _, request = app_server
    assert b"What are we working on?" in request("/", authenticated=False)
    assert b"AEGAEON_TOKEN" in request("/app.js", authenticated=False)
    assert b"aegaeon-theme" in request("/theme.js", authenticated=False)
    assert b'color-scheme:dark' in request("/theme.css", authenticated=False)
    assert request("/api/state")["version"] == "2.0.0"
    with pytest.raises(urllib.error.HTTPError) as error:
        request("/api/state", authenticated=False)
    assert error.value.code == 401
    for headers in ({"Origin": "https://evil.example"}, {"Host": "evil.example"}):
        with pytest.raises(urllib.error.HTTPError) as error:
            request("/api/demo", {}, headers=headers)
        assert error.value.code == 403


def test_model_manager_requires_auth_and_explicit_download_choice(app_server, monkeypatch):
    app, request = app_server
    with pytest.raises(urllib.error.HTTPError) as error:
        request("/api/models", authenticated=False)
    assert error.value.code == 401
    monkeypatch.setattr("aegaeon_lite.models.hardware", lambda: {"gpus": []})
    assert len(request("/api/models")["models"]) == 4
    with pytest.raises(urllib.error.HTTPError) as error:
        request("/api/models/download", {"id": "0"})
    assert error.value.code == 400
    monkeypatch.setattr(app.models, "start",
                        lambda identifier: {"status": "selected", "id": identifier})
    assert request("/api/models/download", {"id": "0", "confirm_download": True})["id"] == "0"


def test_native_thinking_setting_is_explicit_and_persists(app_server):
    app, request = app_server
    settings = {"endpoint": "http://127.0.0.1:8080/v1", "model": "local", "context": 4096}
    assert request("/api/settings", {**settings, "thinking": True})["thinking"] is True
    assert App(app.directory).settings["thinking"] is True
    with pytest.raises(urllib.error.HTTPError):
        request("/api/settings", {**settings, "thinking": "high"})
    assert app.settings["thinking"] is True


def test_setup_and_welcome_api(app_server):
    app, request = app_server
    for path, value in [("/api/setup", None), ("/api/welcome", {"welcome_dismissed": True})]:
        with pytest.raises(urllib.error.HTTPError) as error:
            request(path, value, authenticated=False)
        assert error.value.code == 401
    report = request("/api/setup")
    assert report["kind"] == "aegaeon-setup"
    assert report["model"] == "not_checked"
    assert not request("/api/state")["ui"]["welcome_dismissed"]
    request("/api/welcome", {"welcome_dismissed": True})
    assert request("/api/state")["ui"]["welcome_dismissed"]
    assert App(app.directory).state()["ui"]["welcome_dismissed"]
    with pytest.raises(urllib.error.HTTPError) as error:
        request("/api/welcome", {"welcome_dismissed": "yes"})
    assert error.value.code == 400


def test_multiple_app_ports_do_not_overwrite_each_others_cookie(tmp_path):
    servers = [make_server(App(tmp_path / str(i)), 0) for i in range(2)]
    threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in servers]
    for thread in threads:
        thread.start()
    client = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    addresses = [f"http://127.0.0.1:{s.server_port}" for s in servers]
    try:
        for address in addresses:
            with client.open(address) as response:
                assert response.status == 200
        for address in addresses:
            with client.open(address + "/api/state") as response:
                assert response.status == 200
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=3)


@pytest.mark.parametrize("bad", [
    {"context": float("inf")}, {"context": float("nan")}, {"context": True},
    {"model": {}}, {"endpoint": "http://localhost:wrong/v1"},
])
def test_invalid_model_settings_do_not_drop_connection_or_persist(app_server, bad):
    app, request = app_server
    original = dict(app.settings)
    settings = {"endpoint": "http://127.0.0.1:8080/v1", "model": "local", "context": 8192, **bad}
    with pytest.raises(urllib.error.HTTPError) as error:
        request("/api/settings", settings)
    assert error.value.code == 400
    assert app.settings == original
    assert request("/api/state")["settings"] == original


def test_model_probe_auth_and_active_guard(app_server, monkeypatch):
    app, request = app_server
    with pytest.raises(urllib.error.HTTPError) as error:
        request("/api/model/probe", {}, authenticated=False)
    assert error.value.code == 401
    monkeypatch.setattr(
        "aegaeon_lite.validation.probe_model", lambda provider: {"status": "passed"}
    )
    assert request("/api/model/probe", {})["status"] == "passed"
    assert not app.state()["active"]
    app.probing = True
    with pytest.raises(urllib.error.HTTPError) as error:
        request("/api/model/probe", {})
    assert error.value.code == 400
    app.probing = False


def test_demo_http_workflow_and_branch_review(app_server):
    app, request = app_server
    project = request("/api/demo", {})
    info = request("/api/project?id=" + project["id"])
    task = {
        "request": DEMO_REQUEST,
        "mode": "fix",
        "allowed_paths": ["calc.py"],
        "checks": info["checks"],
    }
    exported = request("/api/export", {"project_id": project["id"], "task": task})
    assert request(exported["download"]).startswith(b"PK")
    job = request("/api/run", {"project_id": project["id"], "task": task, "allow_checks": True})
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        detail = request("/api/job?id=" + job["id"])
        if detail["state"] in {"finished", "failed"}:
            break
        time.sleep(0.1)
    assert detail["state"] == "finished", detail
    assert detail["report"]["status"] == "Checks passed"
    assert detail["engine"] == "demo"
    assert "return a + b" in detail["diff"]
    assert any(e["type"] == "review" for e in detail["events"])
    assert "return a - b" in (Path(project["root"]) / "calc.py").read_text()
    result = request(f"/download/{job['id']}/result")
    uploaded = request("/api/upload", raw=result)
    preview = request(
        "/api/import", {"upload_id": uploaded["upload_id"], "project_id": project["id"]}
    )
    assert preview["files"] == ["calc.py"] and not preview["applied"]
    applied = request("/api/apply", {"job_id": job["id"]})
    assert applied["applied"] and applied["branch"].startswith("aegaeon/")
    assert request("/api/job?id=" + job["id"])["branch"]["commit"] == applied["commit"]
    assert app.state()["active"] is False


def test_invalid_upload_and_remote_endpoint_are_rejected(app_server):
    _, request = app_server
    with pytest.raises(urllib.error.HTTPError) as error:
        request("/api/upload", raw=b"not a zip")
    assert error.value.code == 400
    with pytest.raises(urllib.error.HTTPError) as error:
        request(
            "/api/settings",
            {"endpoint": "https://external.example/v1", "model": "x", "context": 8192},
        )
    assert error.value.code == 400


def test_app_recovers_interrupted_sessions(tmp_path):
    app = App(tmp_path / "app")
    project = app.create_demo()
    value = app.export(project["id"], {"request": "Explain source", "mode": "ask", "checks": []})
    app.update_job(value["id"], state="running")
    restored = App(tmp_path / "app")
    assert restored.job(value["id"])["state"] == "interrupted"
    assert restored.state()["active"] is False


def test_cancelled_model_response_cannot_edit_source(project, task):
    cancellation = threading.Event()
    before = (project / "calc.py").read_bytes()

    class CancelWhileGenerating:
        def complete(self, messages):
            cancellation.set()
            return json.dumps(
                {
                    "action": "edit",
                    "path": "calc.py",
                    "expected_sha256": digest(before),
                    "old": "return a - b",
                    "new": "return a + b",
                }
            )

    result = Session(project, task, CancelWhileGenerating(), cancel_event=cancellation).run()
    assert result["status"] == "Cancelled"
    assert (project / "calc.py").read_bytes() == before


def test_cancellation_terminates_an_active_check(project):
    cancellation = threading.Event()
    timer = threading.Timer(0.4, cancellation.set)
    timer.start()
    try:
        result = run_check(
            project,
            {"id": "slow", "argv": ["python", "-c", "import time; time.sleep(30)"], "timeout": 30},
            True,
            cancel_event=cancellation,
        )
    finally:
        timer.cancel()
    assert result["cancelled"] and result["status"] == "failed"
    assert result["elapsed_seconds"] < 10


def test_nested_check_suggestions_honor_script_names(tmp_path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "package.json").write_text(
        json.dumps({"scripts": {"test": "node --test", "build": "vite build"}})
    )
    checks = suggested_checks(tmp_path)
    assert checks[0]["cwd"] == "frontend"
    assert checks[0]["argv"] == ["npm", "run", "test"]
    assert "--runInBand" not in checks[0]["argv"]


def test_demo_does_not_pretend_to_answer_arbitrary_requests(tmp_path):
    app = App(tmp_path / "app")
    project = app.create_demo()
    with pytest.raises(ValueError, match="fixed addition example"):
        app.start(project["id"], {"request": "Build an unrelated game", "mode": "fix"})
    assert not app.state()["jobs"]


def test_plan_http_draft_approval_and_notebook_export(app_server):
    app, request = app_server
    project = request("/api/demo", {})
    plan = request("/api/plan/create", {"project_id": project["id"], "demo": True})
    assert plan["state"] == "draft" and len(plan["spec"]["steps"]) == 2
    assert request("/api/plans?project_id=" + project["id"])[0]["id"] == plan["id"]
    with pytest.raises(urllib.error.HTTPError):
        request("/api/plan/run", {"id": plan["id"], "step_id": "S1"})
    request("/api/plan/approve", {"id": plan["id"], "revision": 1})
    job = request("/api/plan/run", {"id": plan["id"], "step_id": "S1", "notebook": True})
    assert request(job["download"]).startswith(b"PK")
    assert request("/api/job?id=" + job["id"])["plan"]["plan_id"] == plan["id"]
    with pytest.raises(urllib.error.HTTPError) as error:
        request("/api/apply", {"job_id": job["id"]})
    assert error.value.code == 400
    request("/api/plan/abandon", {"job_id": job["id"]})
    assert app.job(job["id"])["state"] == "abandoned"


def test_active_operation_is_not_hidden_by_history_limit(tmp_path):
    app = App(tmp_path / "app")
    with app.connect() as db:
        db.executemany(
            "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
            [
                (
                    f"job-{index:012x}",
                    "unused",
                    "running" if index == 0 else "exported",
                    "notebook",
                    '{"request":"example"}',
                    None,
                    "",
                    index,
                )
                for index in range(102)
            ],
        )
    value = app.state()
    assert len(value["jobs"]) == 100
    assert value["active"] is True


def test_check_policy_editor_and_read_only_setup_api(app_server):
    _, request = app_server
    project = request("/api/demo", {})
    plan = request("/api/plan/create", {"project_id": project["id"], "demo": True})
    body = {
        "id": plan["id"],
        "step_id": "S1",
        "revision": 1,
        "worker_checks": [{"id": "cloud", "argv": ["cloud-gpu-only-command"]}],
        "acceptance_checks": [{"id": "local", "argv": ["python", "-c", "pass"]}],
    }
    saved = request("/api/plan/checks", body)
    assert saved["revision"] == 2
    setup = request(f"/api/plan/preflight?id={plan['id']}&step_id=S1")
    assert setup["status"] == "tools_found"
    assert setup["checks"][0]["id"] == "local"
    assert setup["source"] == "accepted_checkpoint"
    with pytest.raises(urllib.error.HTTPError):
        request("/api/plan/checks", body)
    body["revision"] = 2
    request("/api/plan/approve", {"id": plan["id"], "revision": 2})
    with pytest.raises(urllib.error.HTTPError):
        request("/api/plan/checks", body)
    with pytest.raises(urllib.error.HTTPError) as error:
        request(f"/api/plan/preflight?id={plan['id']}&step_id=S1", authenticated=False)
    assert error.value.code == 401


def test_checkpoint_http_round_trip_requires_new_destination(app_server):
    app, request = app_server
    project = request("/api/demo", {})
    plan = request("/api/plan/create", {"project_id": project["id"], "demo": True})
    saved = request("/api/checkpoint/export", {"id": plan["id"]})
    checkpoint = request(saved["download"])
    uploaded = request("/api/checkpoint/upload", raw=checkpoint)
    assert len(uploaded["files"]) == 3 and uploaded["remaining_steps"] == ["S1", "S2"]
    destination = app.directory / "restored source"
    result = request(
        "/api/checkpoint/restore",
        {"upload_id": uploaded["upload_id"], "destination": str(destination)},
    )
    assert result["plan"]["state"] == "draft"
    assert result["project"]["demo"] == 0
    assert request("/api/project?id=" + result["project"]["id"])["recovery"]
    with pytest.raises(urllib.error.HTTPError) as error:
        request(
            "/api/checkpoint/restore",
            {"upload_id": uploaded["upload_id"], "destination": str(destination)},
        )
    assert error.value.code == 400
    with pytest.raises(urllib.error.HTTPError):
        request("/api/checkpoint/upload", raw=b"invalid zip")
