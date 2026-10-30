"""Loopback-only, buildless desktop workspace served by Python's standard library."""

from __future__ import annotations

import hmac
import json
import mimetypes
import re
import secrets
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
import zipfile
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from . import __version__
from .bundles import export_task, import_result, initialize, pack_result, read_archive, unpack_task
from .models import Models
from .plans import DOC_REQUEST, Plans, demo_plan
from .provider import ModelProvider
from .session import Session, validate_task
from .workspace import digest, git, safe_file, save_json, snapshot

WEB = Path(__file__).with_name("web")
ACTIVE = {"queued", "running", "cancelling"}
DEMO_REQUEST = (
    "Fix add in calc.py so it adds numbers correctly. Preserve the existing regression tests."
)


def suggested_checks(root: Path) -> list[dict]:
    result = []
    for relative in (".", "backend", "frontend", "web", "server"):
        folder = root / relative
        if not folder.is_dir():
            continue
        if list((folder / "tests").glob("test_*.py")):
            metadata = ""
            for name in ("pyproject.toml", "requirements.txt"):
                path = folder / name
                if path.is_file() and path.stat().st_size < 100_000:
                    metadata += path.read_text(encoding="utf-8", errors="replace")
            argv = (
                ["python", "-m", "pytest", "-q"]
                if "pytest" in metadata
                else ["python", "-m", "unittest", "discover", "-s", "tests"]
            )
            result.append({"id": relative + ":tests", "argv": argv, "cwd": relative, "timeout": 60})
        package = folder / "package.json"
        if package.is_file() and package.stat().st_size < 100_000:
            try:
                scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts", {})
            except (ValueError, AttributeError):
                continue
            for script in ("test", "typecheck", "lint", "build"):
                if script in scripts:
                    result.append(
                        {
                            "id": relative + ":" + script,
                            "argv": ["npm", "run", script],
                            "cwd": relative,
                            "timeout": 120,
                        }
                    )
    return result


class DemoProvider:
    """Explicit offline demonstration; never a fallback for a real model."""

    def __init__(self, root: Path, task: dict | None = None):
        self.root = root
        self.calls = 0
        self.documentation = bool(task and task["request"] == DOC_REQUEST)

    def complete(self, messages: list[dict]) -> str:
        time.sleep(0.6)
        if messages[0]["content"].startswith("Review"):
            return json.dumps(
                {
                    "summary": "The fixed demo change matches the requested behavior.",
                    "unresolved": [],
                }
            )
        self.calls += 1
        if self.calls == 1:
            action = {"action": "read", "path": "README.md" if self.documentation else "calc.py"}
        elif self.calls == 2:
            action = {
                "action": "edit",
                "path": "README.md" if self.documentation else "calc.py",
                "expected_sha256": digest(
                    (self.root / ("README.md" if self.documentation else "calc.py")).read_bytes()
                ),
                "old": (self.root / "README.md").read_text(encoding="utf-8")
                if self.documentation
                else "return a - b",
                "new": "# Calculator demo\n\n`add(a, b)` returns the sum. "
                "Example: `add(2, 3) == 5`.\n"
                if self.documentation
                else "return a + b",
            }
        else:
            action = {
                "action": "finish",
                "summary": "Documented addition and preserved the implementation and tests."
                if self.documentation
                else "Fixed addition and preserved regression tests.",
                "unresolved": [],
            }
        return json.dumps(action)


class App:
    def __init__(self, directory: Path):
        self.directory = directory.resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.db = self.directory / "app.sqlite"
        self.lock = threading.RLock()
        self.models = Models(self.directory)
        self.cancel_events: dict[str, threading.Event] = {}
        self.token = secrets.token_urlsafe(32)
        self.settings = {"endpoint": "http://127.0.0.1:8080/v1", "model": "local", "context": 8192}
        self.ui = {"welcome_dismissed": False}
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS projects(
                    id TEXT PRIMARY KEY, title TEXT, root TEXT UNIQUE, demo INTEGER, created REAL);
                CREATE TABLE IF NOT EXISTS jobs(
                    id TEXT PRIMARY KEY, project_id TEXT, state TEXT, engine TEXT,
                    task TEXT, report TEXT, error TEXT, created REAL);
                CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY, value TEXT);
            """)
            db.execute(
                "UPDATE jobs SET state='interrupted', error='App restarted. "
                "Any saved result remains available.' "
                "WHERE state IN ('queued','running','cancelling')"
            )
            row = db.execute("SELECT value FROM config WHERE id=1").fetchone()
            if row:
                self.settings.update(json.loads(row["value"]))
            row = db.execute("SELECT value FROM config WHERE id=2").fetchone()
            if row:
                self.ui.update(json.loads(row["value"]))
        self.plans = Plans(self)

    def connect(self):
        connection = sqlite3.connect(self.db, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def project(self, project_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if row is None:
            raise ValueError("Project not found")
        return dict(row)

    def job(self, job_id: str) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise ValueError("Session not found")
        return dict(row)

    def state(self) -> dict:
        with self.connect() as db:
            projects = [dict(r) for r in db.execute("SELECT * FROM projects ORDER BY created DESC")]
            has_active_job = (
                db.execute(
                    "SELECT 1 FROM jobs WHERE state IN ('queued','running','cancelling') LIMIT 1"
                ).fetchone()
                is not None
            )
            jobs = [
                dict(r) for r in db.execute("SELECT * FROM jobs ORDER BY created DESC LIMIT 100")
            ]
        for job in jobs:
            job["task"] = json.loads(job["task"])
            job["report"] = json.loads(job["report"]) if job["report"] else None
            job["has_result"] = (self.job_dir(job["id"]) / "result.zip").is_file()
        return {
            "version": __version__,
            "projects": projects,
            "jobs": jobs,
            "settings": self.settings,
            "ui": dict(self.ui),
            "active": getattr(self, "probing", False) or has_active_job
            or any(p["state"] in {"planning", "verifying"} for p in self.plans.list()),
        }

    def register(self, root: Path, *, demo: bool = False) -> dict:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise ValueError("Choose an existing project folder")
        if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
            raise ValueError("Choose the Git repository root")
        files = snapshot(root)
        if not files:
            raise ValueError("Project contains no supported text source files")
        with self.connect() as db:
            row = db.execute("SELECT * FROM projects WHERE root=?", (str(root),)).fetchone()
            if row:
                return dict(row)
            identifier = "project-" + uuid4().hex[:12]
            db.execute(
                "INSERT INTO projects VALUES(?,?,?,?,?)",
                (identifier, root.name, str(root), int(demo), time.time()),
            )
        return self.project(identifier)

    def create_demo(self) -> dict:
        root = self.directory / "projects" / ("calculator-" + uuid4().hex[:6])
        root.mkdir(parents=True)
        (root / "tests").mkdir()
        (root / "calc.py").write_bytes(b"def add(a, b):\n    return a - b\n")
        (root / "README.md").write_bytes(
            b"# Calculator demo\n\nFix addition without changing the regression tests.\n"
        )
        (root / "tests" / "test_calc.py").write_bytes(
            b"import unittest\nfrom calc import add\n\nclass AdditionTests(unittest.TestCase):\n"
            b"    def test_positive(self):\n        self.assertEqual(add(2, 3), 5)\n"
            b"    def test_negative(self):\n        self.assertEqual(add(-2, 1), -1)\n"
        )
        initialize(root)
        return self.register(root, demo=True)

    def checkpoint_path(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"backup-[a-f0-9]{12}", identifier):
            raise ValueError("Invalid checkpoint upload identifier")
        return self.directory / "checkpoints" / (identifier + ".zip")

    def upload_checkpoint(self, data):
        from .checkpoints import inspect_checkpoint

        identifier = "backup-" + uuid4().hex[:12]
        path = self.checkpoint_path(identifier)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
        return {"upload_id": identifier, **inspect_checkpoint(path)}

    def restore_checkpoint(self, identifier, destination):
        from .checkpoints import restore_source

        with self.lock:
            if self.state()["active"]:
                raise ValueError("Wait for the active operation before restoring")
            restored = restore_source(self.checkpoint_path(identifier), Path(destination))
            manifest = restored["manifest"]
            root = Path(restored["root"])
            recovery = {
                "checkpoint_id": manifest["id"],
                "goal": manifest["spec"]["goal"],
                "accepted_steps": manifest["accepted_steps"],
                "history": manifest["history"],
                "notice": "Imported source and historical claims; no checks were run. "
                "Remaining work is a new draft and needs your review and approval.",
            }
            save_json(root / ".aegaeon" / "recovery.json", recovery)
            project = self.register(root)  # Imported code is never labeled as the trusted demo.
            plan = None
            if manifest["remaining"]:
                plan = self.plans.create(project["id"], manifest["remaining"])
                record = self.plans.load(plan["id"])
                record["recovery"] = recovery
                self.plans.save(record)
                plan = self.plans.view(record)
            return {"project": project, "plan": plan, "recovery": recovery}

    def job_dir(self, job_id: str) -> Path:
        # IDs originate in the DB, never from a filesystem path supplied by the browser.
        if not job_id.startswith("job-") or len(job_id) != 16 or not job_id[4:].isalnum():
            raise ValueError("Invalid session identifier")
        return self.directory / "jobs" / job_id

    def update_job(self, job_id: str, *, state: str, report=None, error: str = "") -> None:
        with self.lock, self.connect() as db:
            db.execute(
                "UPDATE jobs SET state=?, report=?, error=? WHERE id=?",
                (state, json.dumps(report) if report is not None else None, error, job_id),
            )

    def append_event(self, job_id: str, event: dict) -> None:
        with self.lock:
            with (self.job_dir(job_id) / "events.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({**event, "time": time.time()}) + "\n")

    def detail(self, job_id: str) -> dict:
        job = self.job(job_id)
        directory = self.job_dir(job_id)
        events = directory / "events.jsonl"
        job["events"] = []
        if events.is_file():
            with self.lock:
                lines = events.read_text(encoding="utf-8").splitlines()
            job["events"] = [json.loads(line) for line in lines[-100:]]
        job["report"] = json.loads(job["report"]) if job["report"] else None
        job["task"] = json.loads(job["task"])
        job["diff"] = ""
        result = directory / "result.zip"
        if result.is_file():
            try:
                preview = import_result(Path(self.project(job["project_id"])["root"]), result)
                job["diff"] = preview["diff"]
            except ValueError as exc:
                # Still show saved session diff when the canonical base is stale or imported.
                job["import_notice"] = str(exc)
                patch = directory / "changes.patch"
                if patch.is_file():
                    job["diff"] = patch.read_text(encoding="utf-8")
        applied = directory / "branch.json"
        job["branch"] = json.loads(applied.read_text()) if applied.is_file() else None
        reference = directory / "plan.json"
        job["plan"] = json.loads(reference.read_text()) if reference.is_file() else None
        return job

    def export(self, project_id: str, task: dict, *, source_root=None, plan_ref=None) -> dict:
        validate_task(task)
        project = self.project(project_id)
        identifier = "job-" + uuid4().hex[:12]
        directory = self.job_dir(identifier)
        directory.mkdir(parents=True)
        export_task(source_root or Path(project["root"]), task, directory / "task.zip")
        if plan_ref:
            (directory / "plan.json").write_text(json.dumps(plan_ref), encoding="utf-8")
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?)",
                (
                    identifier,
                    project_id,
                    "exported",
                    "notebook",
                    json.dumps(task),
                    None,
                    "",
                    time.time(),
                ),
            )
        return {"id": identifier, "download": "/download/" + identifier + "/task"}

    def start(
        self,
        project_id: str,
        task: dict,
        *,
        allow_checks: bool = False,
        source_root=None,
        plan_ref=None,
    ) -> dict:
        validate_task(task)
        with self.lock:
            if self.state()["active"]:
                raise ValueError("One session is already active. Wait or cancel it first.")
            project = self.project(project_id)
            allowed_demo = {DEMO_REQUEST, DOC_REQUEST} if plan_ref else {DEMO_REQUEST}
            if project["demo"] and (
                task.get("mode") != "fix" or task["request"] not in allowed_demo
            ):
                raise ValueError(
                    "The offline demo is a fixed addition example. Open a real project "
                    "for your own requests, Q&A, or testing tasks."
                )
            exported = self.export(project_id, task, source_root=source_root, plan_ref=plan_ref)
            identifier = exported["id"]
            engine = "demo" if project["demo"] else "model"
            with self.connect() as db:
                db.execute(
                    "UPDATE jobs SET state='queued', engine=? WHERE id=?", (engine, identifier)
                )
            cancellation = threading.Event()
            self.cancel_events[identifier] = cancellation
            settings = dict(self.settings)
            worker = threading.Thread(
                target=self.run_job,
                args=(identifier, cancellation, settings, allow_checks),
                daemon=True,
            )
            worker.start()
            return {"id": identifier}

    def run_job(
        self, identifier: str, cancellation: threading.Event, settings: dict, allow_checks: bool
    ) -> None:
        directory = self.job_dir(identifier)
        try:
            job = self.job(identifier)
            self.update_job(identifier, state="running")
            root, manifest = unpack_task(directory / "task.zip", directory / "workspace")
            provider = (
                DemoProvider(root, manifest["task"])
                if job["engine"] == "demo"
                else ModelProvider(
                    base_url=settings["endpoint"],
                    model=settings["model"],
                    context_tokens=settings["context"],
                    output_tokens=min(2048, settings["context"] // 4),
                    thinking=settings.get("thinking", False),
                )
            )
            session = Session(
                root,
                manifest["task"],
                provider,
                allow_checks=allow_checks,
                cancel_event=cancellation,
                on_event=lambda e: self.append_event(identifier, e),
            )
            report = session.run()
            pack_result(root, manifest, report, directory / "result.zip")
            (directory / "changes.patch").write_text(session.patch, encoding="utf-8")
            self.update_job(
                identifier,
                state="cancelled" if cancellation.is_set() else "finished",
                report=report,
            )
        except Exception as exc:
            self.append_event(identifier, {"type": "app.error", "message": str(exc)})
            self.update_job(identifier, state="failed", error=str(exc))
        finally:
            with self.lock:
                self.cancel_events.pop(identifier, None)

    def cancel(self, job_id: str) -> dict:
        with self.lock:
            cancellation = self.cancel_events.get(job_id)
            if cancellation is None or self.job(job_id)["state"] not in ACTIVE:
                raise ValueError("Session is not active")
            cancellation.set()
            self.update_job(job_id, state="cancelling")
        return {"state": "cancelling", "message": "Waiting for the active model request to return"}

    def save_settings(self, settings: dict) -> dict:
        if type(settings.get("context")) is not int:
            raise ValueError("context must be a whole number from 1024 to 32768")
        validated = ModelProvider(
            base_url=settings["endpoint"],
            model=settings["model"],
            context_tokens=int(settings["context"]),
            output_tokens=min(2048, int(settings["context"]) // 4),
            thinking=settings.get("thinking", False),
        )
        value = {
            "endpoint": validated.base_url,
            "model": validated.model,
            "context": validated.context_tokens,
            "thinking": validated.thinking,
        }
        with self.lock, self.connect() as db:
            db.execute("INSERT OR REPLACE INTO config VALUES(1,?)", (json.dumps(value),))
            self.settings = value
        return value

    def model_status(self) -> dict:
        provider = ModelProvider(base_url=self.settings["endpoint"])
        request = urllib.request.Request(provider.base_url + "/models")
        try:

            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, req, fp, code, msg, headers, newurl):
                    return None

            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            with opener.open(request, timeout=2) as response:
                raw = response.read(100_001)
                if len(raw) > 100_000:
                    raise ValueError("model list exceeds limit")
                data = json.loads(raw)
            entries = data.get("data") if isinstance(data, dict) else None
            if not isinstance(entries, list) or any(
                not isinstance(m, dict) or not isinstance(m.get("id"), str) for m in entries
            ):
                raise ValueError("invalid model list")
            models = [m["id"] for m in entries][:20]
            connected = any(m["id"] == self.settings["model"] for m in entries)
            return {"connected": connected, "reachable": True, "models": models,
                    "message": "Model alias available; use the saved-connection test to probe it."
                    if connected else "Server reached, but the saved model alias is not listed. "
                    "Check Model settings."}
        except (OSError, ValueError, AttributeError, TypeError):
            return {
                "connected": False,
                "message": "Start your local model server or export a notebook task",
            }

    def save_welcome(self, value: object) -> dict:
        if not isinstance(value, bool):
            raise ValueError("welcome_dismissed must be boolean")
        with self.lock, self.connect() as db:
            self.ui = {"welcome_dismissed": value}
            db.execute("INSERT OR REPLACE INTO config VALUES(2,?)", (json.dumps(self.ui),))
        return dict(self.ui)

    def probe_model(self) -> dict:
        from .validation import probe_model

        with self.lock:
            if self.state()["active"]:
                raise ValueError("Wait for the active operation before probing the model")
            settings = dict(self.settings)
            self.probing = True
        try:
            return probe_model(ModelProvider(
                base_url=settings["endpoint"], model=settings["model"],
                context_tokens=settings["context"],
                output_tokens=min(512, settings["context"] // 4), timeout=30,
                thinking=settings.get("thinking", False),
            ))
        finally:
            with self.lock:
                self.probing = False


def make_server(app: App, port: int = 8787) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(
            self,
            status: int,
            body: bytes,
            content_type: str,
            *,
            cookie=False,
            filename: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header(
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self' "
                "'nonce-" + app.token + "'; style-src 'self'; connect-src 'self'; "
                "img-src 'self' data:; frame-ancestors 'none'",
            )
            if cookie:
                self.send_header(
                    "Set-Cookie",
                    f"aegaeon_session_{self.server.server_port}=" + app.token
                    + "; HttpOnly; SameSite=Strict; Path=/",
                )
            if filename:
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
            self.end_headers()
            self.wfile.write(body)

        def json_response(self, value, status=200):
            self.respond(status, json.dumps(value).encode(), "application/json; charset=utf-8")

        def authenticated(self) -> bool:
            supplied = self.headers.get("X-Aegaeon-Token", "")
            if not supplied:
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                    name = f"aegaeon_session_{self.server.server_port}"
                    supplied = cookie[name].value if name in cookie else ""
                except Exception:
                    return False
            return hmac.compare_digest(supplied, app.token)

        def handle_request(self, method: str) -> None:
            host = self.headers.get("Host", "")
            port_number = self.server.server_address[1]
            if host not in {f"127.0.0.1:{port_number}", f"localhost:{port_number}"}:
                self.json_response({"error": "Loopback host required"}, 403)
                return
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + host:
                self.json_response({"error": "Cross-origin requests are not accepted"}, 403)
                return
            url = urllib.parse.urlparse(self.path)
            path = url.path
            query = urllib.parse.parse_qs(url.query)
            try:
                if path == "/" and method == "GET":
                    html = (WEB / "index.html").read_text(encoding="utf-8")
                    self.respond(
                        200,
                        html.replace("__TOKEN__", app.token).encode(),
                        "text/html; charset=utf-8",
                        cookie=True,
                    )
                    return
                if method == "GET" and path in {
                    "/app.js", "/style.css", "/theme.js", "/theme.css", "/logo.png"
                }:
                    file = WEB / path[1:]
                    self.respond(
                        200, file.read_bytes(), mimetypes.guess_type(file)[0] or "text/plain"
                    )
                    return
                if not self.authenticated():
                    self.json_response({"error": "Open the local app before using its API"}, 401)
                    return
                if method == "GET":
                    if path == "/api/state":
                        self.json_response(app.state())
                    elif path == "/api/model":
                        self.json_response(app.model_status())
                    elif path == "/api/setup":
                        from .readiness import setup_report

                        self.json_response(setup_report())
                    elif path == "/api/models":
                        self.json_response(app.models.listing())
                    elif path == "/api/project":
                        project = app.project(query.get("id", [""])[0])
                        root = Path(project["root"])
                        self.json_response(
                            {
                                **project,
                                "files": list(snapshot(root)),
                                "checks": suggested_checks(root),
                                "commit": git(root, "rev-parse", "--short", "HEAD"),
                                "recovery": json.loads(
                                    (root / ".aegaeon" / "recovery.json").read_text(
                                        encoding="utf-8"
                                    )
                                )
                                if (root / ".aegaeon" / "recovery.json").is_file()
                                else None,
                            }
                        )
                    elif path == "/api/file":
                        root = Path(app.project(query.get("id", [""])[0])["root"])
                        name = query.get("path", [""])[0]
                        if name not in snapshot(root):
                            raise ValueError("File is not in the supported source set")
                        self.json_response(
                            {
                                "path": name,
                                "content": safe_file(root, name).read_text(encoding="utf-8")[
                                    :60_000
                                ],
                            }
                        )
                    elif path == "/api/job":
                        self.json_response(app.detail(query.get("id", [""])[0]))
                    elif path == "/api/plans":
                        self.json_response(app.plans.list(query.get("project_id", [None])[0]))
                    elif path == "/api/plan":
                        self.json_response(app.plans.detail(query.get("id", [""])[0]))
                    elif path == "/api/plan/preflight":
                        self.json_response(
                            app.plans.preflight(
                                query.get("id", [""])[0], query.get("step_id", [""])[0]
                            )
                        )
                    elif path == "/api/plan/patch":
                        plan = app.plans.detail(query.get("id", [""])[0])
                        root = Path(app.project(plan["project_id"])["root"])
                        self.json_response(
                            {
                                "diff": git(
                                    root, "diff", plan["base_commit"], plan["commit"], "--"
                                ),
                                "commit": plan["commit"],
                                "worktree": plan["root"],
                                "branch": git(Path(plan["root"]), "branch", "--show-current"),
                            }
                        )
                    elif path.startswith("/download-checkpoint/"):
                        identifier = path.removeprefix("/download-checkpoint/")
                        self.respond(
                            200,
                            app.checkpoint_path(identifier).read_bytes(),
                            "application/zip",
                            filename="checkpoint.zip",
                        )
                    elif path.startswith("/download/"):
                        _, _, identifier, artifact = path.split("/")
                        app.job(identifier)
                        if artifact not in {"task", "result"}:
                            raise ValueError("Unknown download")
                        self.respond(
                            200,
                            (app.job_dir(identifier) / (artifact + ".zip")).read_bytes(),
                            "application/zip",
                            filename=artifact + ".zip",
                        )
                    else:
                        self.json_response({"error": "Not found"}, 404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 25_000_000:
                    raise ValueError("Request body is empty or too large")
                raw = self.rfile.read(length)
                if path == "/api/upload":
                    entries = read_archive_bytes(app, raw)
                    self.json_response(entries)
                    return
                if path == "/api/checkpoint/upload":
                    self.json_response(app.upload_checkpoint(raw))
                    return
                if length > 100_000:
                    raise ValueError("JSON request exceeds limit")
                body = json.loads(raw)
                if not isinstance(body, dict):
                    raise ValueError("Request must be an object")
                if path == "/api/models/download":
                    if body.get("confirm_download") is not True:
                        raise ValueError("Confirm the model and download size first")
                    self.json_response(app.models.start(body["id"]), 202)
                elif path == "/api/models/cancel":
                    self.json_response(app.models.cancel())
                elif path == "/api/models/command":
                    self.json_response(app.models.command(body["id"]))
                elif path == "/api/projects":
                    self.json_response(app.register(Path(body["path"])), 201)
                elif path == "/api/demo":
                    self.json_response(app.create_demo(), 201)
                elif path == "/api/export":
                    self.json_response(app.export(body["project_id"], body["task"]), 201)
                elif path == "/api/run":
                    self.json_response(
                        app.start(
                            body["project_id"],
                            body["task"],
                            allow_checks=body.get("allow_checks") is True,
                        ),
                        202,
                    )
                elif path == "/api/cancel":
                    self.json_response(app.cancel(body["job_id"]))
                elif path == "/api/settings":
                    self.json_response(app.save_settings(body))
                elif path == "/api/welcome":
                    self.json_response(app.save_welcome(body.get("welcome_dismissed")))
                elif path == "/api/model/probe":
                    self.json_response(app.probe_model())
                elif path == "/api/plan/create":
                    if body.get("demo") is True:
                        project = app.project(body["project_id"])
                        if not project["demo"]:
                            raise ValueError("The demo plan requires the calculator demo")
                        value = app.plans.create(
                            project["id"],
                            demo_plan(DEMO_REQUEST, suggested_checks(Path(project["root"]))),
                        )
                    elif "spec" in body:
                        value = app.plans.create(body["project_id"], body["spec"])
                    else:
                        value = app.plans.draft(
                            body["project_id"], body["goal"], body.get("checks", [])
                        )
                    self.json_response(value, 201)
                elif path == "/api/plan/edit":
                    self.json_response(app.plans.edit(body["id"], body["spec"], body["revision"]))
                elif path == "/api/checkpoint/export":
                    self.json_response(app.plans.backup(body["id"]))
                elif path == "/api/checkpoint/restore":
                    self.json_response(
                        app.restore_checkpoint(body["upload_id"], body["destination"]), 201
                    )
                elif path == "/api/plan/checks":
                    self.json_response(
                        app.plans.edit_step_checks(
                            body["id"],
                            body["step_id"],
                            body["revision"],
                            body["worker_checks"],
                            body["acceptance_checks"],
                        )
                    )
                elif path == "/api/plan/generate":
                    self.json_response(app.plans.generate(body["id"], body["revision"]), 202)
                elif path == "/api/plan/approve":
                    self.json_response(app.plans.approve(body["id"], body["revision"]))
                elif path == "/api/plan/run":
                    self.json_response(
                        app.plans.launch(
                            body["id"],
                            body["step_id"],
                            notebook=body.get("notebook") is True,
                            allow_checks=body.get("allow_checks") is True,
                        ),
                        202,
                    )
                elif path == "/api/plan/accept":
                    self.json_response(
                        app.plans.accept(
                            body["job_id"],
                            allow_checks=body.get("allow_checks") is True,
                            manual=body.get("manual") is True,
                        ),
                        202,
                    )
                elif path == "/api/plan/stop":
                    self.json_response(app.plans.stop(body["id"]))
                elif path == "/api/plan/abandon":
                    self.json_response(app.plans.abandon(body["job_id"]))
                elif path == "/api/plan/attach":
                    self.json_response(app.plans.attach(body["job_id"], body["upload_id"]))
                elif path == "/api/import":
                    identifier = body.get("upload_id", "")
                    if (
                        not identifier.startswith("upload-")
                        or len(identifier) != 19
                        or not identifier[7:].isalnum()
                    ):
                        raise ValueError("Invalid upload identifier")
                    project = app.project(body["project_id"])
                    self.json_response(
                        import_result(
                            Path(project["root"]),
                            app.directory / "uploads" / (identifier + ".zip"),
                            apply=body.get("apply") is True,
                        )
                    )
                elif path == "/api/apply":
                    job = app.job(body["job_id"])
                    if (app.job_dir(job["id"]) / "plan.json").is_file():
                        raise ValueError(
                            "Accept this step from its plan so dependencies "
                            "and checkpoints stay consistent"
                        )
                    with app.lock:
                        result = import_result(
                            Path(app.project(job["project_id"])["root"]),
                            app.job_dir(job["id"]) / "result.zip",
                            apply=True,
                        )
                        (app.job_dir(job["id"]) / "branch.json").write_text(json.dumps(result))
                    self.json_response(result)
                else:
                    self.json_response({"error": "Not found"}, 404)
            except (ValueError, OSError, KeyError, TypeError, zipfile.BadZipFile) as exc:
                self.json_response({"error": str(exc)}, 400)

        def do_GET(self):
            self.handle_request("GET")

        def do_POST(self):
            self.handle_request("POST")

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def read_archive_bytes(app: App, data: bytes) -> dict:
    identifier = "upload-" + uuid4().hex[:12]
    directory = app.directory / "uploads"
    directory.mkdir(exist_ok=True)
    path = directory / (identifier + ".zip")
    path.write_bytes(data)
    entries = read_archive(path)
    manifest = json.loads(entries.get("manifest.json", b"{}"))
    if not isinstance(manifest, dict) or manifest.get("kind") != "result":
        raise ValueError("Upload a result.zip generated by a notebook session")
    return {"upload_id": identifier, "kind": "result"}


def serve(directory: Path, port: int = 8787, *, demo: bool = False, open_browser=False) -> None:
    app = App(directory)
    if demo and not app.state()["projects"]:
        app.create_demo()
    server = make_server(app, port)
    print(f"AEGAEON Lite {__version__}: http://127.0.0.1:{server.server_address[1]}", flush=True)
    if open_browser:
        import webbrowser

        webbrowser.open(f"http://127.0.0.1:{server.server_address[1]}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        for cancellation in list(app.cancel_events.values()):
            cancellation.set()
        for cancellation in list(app.plans.cancellations.values()):
            cancellation.set()
        server.server_close()
