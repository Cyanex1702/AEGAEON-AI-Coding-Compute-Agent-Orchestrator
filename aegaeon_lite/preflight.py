"""Inspect local command prerequisites without executing project code."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from .checks import check_argv, validate_checks
from .workspace import safe_file


def inspect_checks(root: Path, checks: list[dict]) -> dict:
    validate_checks(checks)
    items = []
    for check in checks:
        item = {
            "id": check["id"],
            "argv": check["argv"],
            "cwd": check.get("cwd", "."),
            "status": "tools_found",
            "issues": [],
        }
        cwd = root if item["cwd"] == "." else safe_file(root, item["cwd"])
        if not cwd.is_dir():
            item["issues"].append("Working directory is missing: " + item["cwd"])
        program = check_argv(check)[0]
        if not program.strip():
            location = None
        elif Path(program).is_absolute():
            location = program if Path(program).is_file() else None
        elif "/" in program or "\\" in program:
            location = str(cwd / program) if (cwd / program).is_file() else None
        else:
            location = shutil.which(program)
        if not location:
            item["issues"].append("Executable not found: " + program)
        elif os.name != "nt" and not os.access(location, os.X_OK):
            item["issues"].append("Executable permission is missing: " + program)
        item["executable"] = location
        if item["issues"]:
            item["status"] = "needs_setup"
        items.append(item)
    return {
        "status": "no_checks"
        if not checks
        else "needs_setup"
        if any(item["issues"] for item in items)
        else "tools_found",
        "checks": items,
        "message": "Declared checks were not executed. Tool discovery does not verify "
        "dependencies, GPU availability, model quality, or command success.",
    }
