from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class VerificationCommand:
    name: str
    category: Literal["dependency", "test", "typecheck", "lint", "build"]
    command: list[str]


class TestCommandDetector:
    """Builds an ordered dependency + verification plan from repository-native config."""

    def dependency_commands(
        self, repo: Path, *, container: bool = False
    ) -> list[VerificationCommand]:
        commands: list[VerificationCommand] = []
        npm = "npm" if container else "npm.cmd" if os.name == "nt" else "npm"
        if (repo / "package-lock.json").is_file():
            commands.append(
                VerificationCommand(
                    "npm clean install",
                    "dependency",
                    [npm, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
                )
            )
        elif (repo / "pnpm-lock.yaml").is_file():
            commands.append(
                VerificationCommand(
                    "pnpm frozen install",
                    "dependency",
                    ["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"],
                )
            )
        elif (repo / "yarn.lock").is_file():
            commands.append(
                VerificationCommand(
                    "yarn immutable install",
                    "dependency",
                    ["yarn", "install", "--immutable", "--ignore-scripts"],
                )
            )
        elif (repo / "package.json").is_file():
            commands.append(
                VerificationCommand(
                    "npm install (no lockfile)",
                    "dependency",
                    [npm, "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                )
            )

        venv_python = (
            repo / ".venv" / "bin" / "python"
            if container
            else repo / ".venv" / "Scripts" / "python.exe"
            if os.name == "nt"
            else repo / ".venv" / "bin" / "python"
        )
        if (repo / "requirements.txt").is_file() or self._pyproject_is_installable(repo):
            commands.append(
                VerificationCommand(
                    "create isolated Python environment",
                    "dependency",
                    ["python3" if container else sys.executable, "-m", "venv", ".venv"],
                )
            )
            if (repo / "requirements.txt").is_file():
                install = [str(venv_python), "-m", "pip", "install", "-r", "requirements.txt"]
            else:
                install = [str(venv_python), "-m", "pip", "install", "."]
            commands.append(
                VerificationCommand("install Python dependencies", "dependency", install)
            )
        return commands

    def checks(self, repo: Path, *, container: bool = False) -> list[VerificationCommand]:
        commands: list[VerificationCommand] = []
        venv_python = (
            repo / ".venv" / "bin" / "python"
            if container
            else repo / ".venv" / "Scripts" / "python.exe"
            if os.name == "nt"
            else repo / ".venv" / "bin" / "python"
        )
        python = (
            str(venv_python)
            if venv_python.is_file()
            else "python3"
            if container
            else sys.executable
        )
        if (repo / "tests").is_dir() or (repo / "pytest.ini").is_file():
            commands.append(VerificationCommand("pytest", "test", [python, "-m", "pytest", "-q"]))
        elif (repo / "pyproject.toml").is_file() and self._pyproject_mentions_pytest(repo):
            commands.append(VerificationCommand("pytest", "test", [python, "-m", "pytest", "-q"]))

        package_path = repo / "package.json"
        if package_path.is_file():
            package = json.loads(package_path.read_text(encoding="utf-8"))
            scripts = package.get("scripts", {})
            npm = "npm" if container else "npm.cmd" if os.name == "nt" else "npm"
            for script, category in (
                ("test", "test"),
                ("typecheck", "typecheck"),
                ("lint", "lint"),
                ("build", "build"),
            ):
                if script not in scripts:
                    continue
                command = (
                    [npm, "test", "--", "--runInBand"] if script == "test" else [npm, "run", script]
                )
                commands.append(VerificationCommand(script, category, command))
        return commands

    def detect(self, repo: Path) -> tuple[list[list[str]], list[str]]:
        checks = self.checks(repo)
        notes: list[str] = []
        if (repo / "package.json").is_file() and not (repo / "node_modules").is_dir():
            notes.append("DEPENDENCY_FAILURE: install dependencies before running Node checks")
        return [item.command for item in checks], notes

    @staticmethod
    def _pyproject_is_installable(repo: Path) -> bool:
        path = repo / "pyproject.toml"
        if not path.is_file():
            return False
        try:
            content = path.read_text(encoding="utf-8").lower()
        except OSError:
            return False
        return "[project]" in content or "[tool.poetry]" in content

    @staticmethod
    def _pyproject_mentions_pytest(repo: Path) -> bool:
        try:
            return "pytest" in (repo / "pyproject.toml").read_text(encoding="utf-8").lower()
        except OSError:
            return False
