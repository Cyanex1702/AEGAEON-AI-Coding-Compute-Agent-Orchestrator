import subprocess
import sys

import pytest

from aegaeon_lite.preflight import inspect_checks


def test_setup_inspection_never_executes_declared_code(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-exist.txt"

    def forbidden(*args, **kwargs):
        raise AssertionError("Setup inspection executed a process")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    check = {
        "id": "write",
        "argv": [
            "python",
            "-c",
            "from pathlib import Path; Path('must-not-exist.txt').write_text('bad')",
        ],
    }
    report = inspect_checks(tmp_path, [check])
    assert report["status"] == "tools_found"
    assert report["checks"][0]["executable"] == sys.executable
    assert not marker.exists()


def test_missing_tools_and_directories_are_specific(tmp_path):
    result = inspect_checks(
        tmp_path,
        [
            {
                "id": "missing",
                "cwd": "not-created",
                "argv": ["aegaeon-tool-that-does-not-exist-271d5c"],
            }
        ],
    )
    assert result["status"] == "needs_setup"
    assert len(result["checks"][0]["issues"]) == 2
    assert "Working directory" in result["checks"][0]["issues"][0]
    assert "Executable not found" in result["checks"][0]["issues"][1]


def test_empty_setup_and_unsafe_cwd(tmp_path):
    assert inspect_checks(tmp_path, [])["status"] == "no_checks"
    with pytest.raises(ValueError):
        inspect_checks(tmp_path, [{"id": "escape", "cwd": "../outside", "argv": ["python"]}])


def test_absolute_and_relative_missing_executables(tmp_path):
    result = inspect_checks(
        tmp_path,
        [
            {"id": "absolute", "argv": [str(tmp_path / "missing.exe")]},
            {"id": "relative", "argv": ["./bin/missing.exe"]},
        ],
    )
    assert all(item["status"] == "needs_setup" for item in result["checks"])
