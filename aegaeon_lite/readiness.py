"""Read-only app prerequisite report; no project access or network requests."""

from __future__ import annotations

import platform
import shutil
import sys

from . import __version__


def setup_report() -> dict:
    python_ok = sys.version_info >= (3, 11)
    git_ok = shutil.which("git") is not None
    return {
        "kind": "aegaeon-setup",
        "schema": 1,
        "app_version": __version__,
        "os": platform.system(),
        "python_version": platform.python_version(),
        "checks": [
            {
                "id": "python",
                "status": "found" if python_ok else "missing",
                "detail": "Python 3.11 or later is required.",
            },
            {
                "id": "git",
                "status": "found" if git_ok else "missing",
                "detail": "Git must be available to the app process.",
            },
        ],
        "status": "tools_found" if python_ok and git_ok else "needs_setup",
        "model": "not_checked",
        "cloud_runtime": "not_checked",
        "notice": "Read-only discovery: no commands, project files, model requests or "
        "installations. Tool discovery is not proof of model or project readiness. "
        "This report excludes paths, source, credentials and machine names.",
    }
