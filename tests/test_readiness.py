import io
import json
from types import SimpleNamespace

import pytest

from aegaeon_lite.app import App
from aegaeon_lite.cli import main
from aegaeon_lite.readiness import setup_report


def test_setup_discovery_does_not_execute_or_connect(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Discovery must not run code or connect to a model")
    monkeypatch.setattr("subprocess.run", forbidden)
    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    monkeypatch.setattr("aegaeon_lite.readiness.shutil.which", lambda name: "C:/private/git.exe")
    report = setup_report()
    assert report["status"] == "tools_found"
    assert report["model"] == report["cloud_runtime"] == "not_checked"
    assert "C:/private" not in json.dumps(report)


def test_missing_git_is_actionable_and_cli_saves_report(tmp_path, monkeypatch):
    monkeypatch.setattr("aegaeon_lite.readiness.shutil.which", lambda name: None)
    output = tmp_path / "setup.json"
    assert main(["doctor", "--output", str(output)]) == 2
    report = json.loads(output.read_text())
    assert report["status"] == "needs_setup"
    assert next(c for c in report["checks"] if c["id"] == "git")["status"] == "missing"


def test_welcome_preference_survives_restart_and_settings_changes(tmp_path):
    app = App(tmp_path)
    assert not app.state()["ui"]["welcome_dismissed"]
    app.save_welcome(True)
    app.save_settings({"endpoint": "http://127.0.0.1:8080/v1", "model": "local", "context": 4096})
    restored = App(tmp_path)
    assert restored.state()["ui"]["welcome_dismissed"]
    assert restored.settings["context"] == 4096
    restored.save_welcome(False)
    assert not App(tmp_path).state()["ui"]["welcome_dismissed"]


@pytest.mark.parametrize("value", [1, "true", None, {}, []])
def test_welcome_only_accepts_boolean(tmp_path, value):
    with pytest.raises(ValueError, match="boolean"):
        App(tmp_path).save_welcome(value)


@pytest.mark.parametrize("data", [{"data": None}, {"data": [None]}, {"data": [{"id": 1}]}, []])
def test_malformed_model_list_is_reported_without_crashing(tmp_path, monkeypatch, data):
    opener = SimpleNamespace(open=lambda *a, **k: io.BytesIO(json.dumps(data).encode()))
    monkeypatch.setattr("urllib.request.build_opener", lambda *a: opener)
    assert App(tmp_path).model_status()["connected"] is False


def test_model_list_requires_saved_alias(tmp_path, monkeypatch):
    data = {"data": [{"id": "other"}]}
    opener = SimpleNamespace(open=lambda *a, **k: io.BytesIO(json.dumps(data).encode()))
    monkeypatch.setattr("urllib.request.build_opener", lambda *a: opener)
    app = App(tmp_path)
    report = app.model_status()
    assert report["reachable"] and not report["connected"]
    assert "alias" in report["message"]
    data["data"].append({"id": "local"})
    assert app.model_status()["connected"]
