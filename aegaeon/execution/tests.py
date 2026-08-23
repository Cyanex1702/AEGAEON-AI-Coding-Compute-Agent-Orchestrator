from __future__ import annotations

import json
import os
import sys
from pathlib import Path


class TestCommandDetector:
    """Selects safe verification commands from repository-native configuration."""

    def detect(self, repo: Path) -> tuple[list[list[str]], list[str]]:
        commands: list[list[str]] = []
        notes: list[str] = []
        if (repo / "tests").is_dir() or (repo / "pytest.ini").is_file():
            commands.append([sys.executable, "-m", "pytest", "-q"])
        elif (repo / "pyproject.toml").is_file() and self._pyproject_mentions_pytest(repo):
            commands.append([sys.executable, "-m", "pytest", "-q"])

        package_path = repo / "package.json"
        if package_path.is_file():
            package = json.loads(package_path.read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
            npm = "npm.cmd" if os.name == "nt" else "npm"
            if not (repo / "node_modules").is_dir():
                notes.append(
                    "Node verification skipped because dependencies are not installed "
                    "in the generated repository. AEGAEON never installs untrusted "
                    "dependencies automatically."
                )
            elif "test" in scripts:
                commands.append([npm, "test", "--", "--runInBand"])
            elif "build" in scripts:
                commands.append([npm, "run", "build"])
        return commands, notes

    @staticmethod
    def _pyproject_mentions_pytest(repo: Path) -> bool:
        try:
            return "pytest" in (repo / "pyproject.toml").read_text(encoding="utf-8").lower()
        except OSError:
            return False
