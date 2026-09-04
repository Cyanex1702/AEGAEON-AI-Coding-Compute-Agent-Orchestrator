from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SandboxPolicy:
    network_for_verification: bool = False
    secrets_available: bool = False
    memory_limit_mb: int = 2_048
    cpu_limit: float = 2.0
    process_limit: int = 128
    enforced: bool = False


@dataclass(frozen=True, slots=True)
class SandboxSession:
    workspace: Path
    backend: str
    policy: SandboxPolicy
    warning: str | None = None
    runtime: str | None = None
    image: str | None = None


class DisposableVerificationSandbox:
    """Creates a fresh workspace per attempt and exposes its effective isolation level.

    The local backend is explicitly unisolated: it copies only source inputs, inherits no
    application secrets through SubprocessRunner, bounds output/time, and kills process trees,
    but it is not a security boundary. Model mode refuses it unless the operator opts in.
    """

    ignored = {
        ".git",
        ".next",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "node_modules",
        "__pycache__",
        "AEGAEON",
        ".env",
        ".env.*",
        "*.pem",
        "*.key",
        "*.p12",
        "*.pfx",
    }

    def __init__(self, managed_root: Path) -> None:
        self.managed_root = managed_root.resolve()
        self.container_runtime = self._container_runtime()
        self.container_image = os.getenv("AEGAEON_RUNNER_IMAGE", "aegaeon-runner:latest")
        self.container_available = self._container_ready()

    @contextmanager
    def session(self, repo: Path) -> Iterator[SandboxSession]:
        canonical = repo.resolve()
        if not canonical.is_relative_to(self.managed_root):
            raise ValueError("repository is outside the managed project root")
        with tempfile.TemporaryDirectory(
            prefix=".aegaeon-verification-", dir=canonical.parent
        ) as temporary:
            workspace = Path(temporary) / "workspace"
            shutil.copytree(
                canonical,
                workspace,
                ignore=shutil.ignore_patterns(*self.ignored),
            )
            container_ready = self.container_available
            yield SandboxSession(
                workspace=workspace,
                backend=self.container_runtime if container_ready else "local_unisolated",
                policy=SandboxPolicy(enforced=container_ready),
                warning=None
                if container_ready
                else (
                    "SECURITY: container isolation is unavailable. Verification used a "
                    "disposable copy but generated code was not OS-isolated."
                ),
                runtime=self.container_runtime if container_ready else None,
                image=self.container_image if container_ready else None,
            )

    def _container_ready(self) -> bool:
        if not self.container_runtime:
            return False
        try:
            inspected = subprocess.run(
                [self.container_runtime, "image", "inspect", self.container_image],
                check=False,
                capture_output=True,
                timeout=5,
            )
            return inspected.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    @staticmethod
    def _container_runtime() -> str | None:
        configured = os.getenv("AEGAEON_CONTAINER_RUNTIME", "").strip()
        if configured:
            return shutil.which(configured)
        return shutil.which("docker") or shutil.which("podman")
