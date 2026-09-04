from __future__ import annotations

import asyncio
import os
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import psutil

from aegaeon.execution.sandbox import SandboxSession
from aegaeon.protocol.schemas import ExecutionResult


class SubprocessRunner:
    """Development executor with clean environments, bounded output, and tree cleanup."""

    allowed_commands = {
        "python",
        "python3",
        "pytest",
        "ruff",
        "mypy",
        "npm",
        "npx",
        "tsc",
        "pnpm",
        "yarn",
    }
    inherited_environment = {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "LANG",
        "LC_ALL",
    }

    def __init__(self, managed_root: Path, timeout: int = 120, maximum_output: int = 200_000):
        self.managed_root = managed_root.resolve()
        self.timeout = timeout
        self.maximum_output = maximum_output

    async def run(self, command: list[str], cwd: Path) -> ExecutionResult:
        if not command:
            raise ValueError("command is required")
        executable_name = Path(command[0]).stem.lower()
        if executable_name not in self.allowed_commands:
            raise ValueError(f"command is not allowlisted: {executable_name}")
        safe_cwd = cwd.resolve()
        if not safe_cwd.is_relative_to(self.managed_root):
            raise ValueError("working directory is outside the managed project root")

        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix=".aegaeon-runtime-", dir=safe_cwd.parent) as temp:
            env = {
                key: value
                for key, value in os.environ.items()
                if key.upper() in self.inherited_environment
            }
            env.update(
                {
                    "HOME": temp,
                    "USERPROFILE": temp,
                    "TMP": temp,
                    "TEMP": temp,
                    "PIP_CACHE_DIR": str(Path(temp) / "pip-cache"),
                    "NPM_CONFIG_CACHE": str(Path(temp) / "npm-cache"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "PYTHONNOUSERSITE": "1",
                    "CI": "1",
                }
            )
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=safe_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                creationflags=creationflags,
                start_new_session=os.name != "nt",
            )
            stdout_task = asyncio.create_task(self._read_bounded(process.stdout))
            stderr_task = asyncio.create_task(self._read_bounded(process.stderr))
            timed_out = False
            try:
                await asyncio.wait_for(process.wait(), timeout=self.timeout)
            except TimeoutError:
                timed_out = True
                self._terminate_tree(process.pid)
                await process.wait()
            except asyncio.CancelledError:
                self._terminate_tree(process.pid)
                await process.wait()
                stdout_task.cancel()
                stderr_task.cancel()
                raise
            stdout, _ = await stdout_task
            stderr, _ = await stderr_task

        return ExecutionResult(
            command=" ".join(command),
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=round(time.perf_counter() - started, 3),
            timed_out=timed_out,
        )

    async def run_isolated(
        self,
        command: list[str],
        sandbox: SandboxSession,
        *,
        network: bool,
    ) -> ExecutionResult:
        if sandbox.runtime and sandbox.image and sandbox.policy.enforced:
            return await self._run_container(
                command,
                sandbox.workspace,
                sandbox.runtime,
                sandbox.image,
                network=network,
                policy=sandbox.policy,
            )
        return await self.run(command, sandbox.workspace)

    async def _run_container(
        self,
        command: list[str],
        cwd: Path,
        runtime: str,
        image: str,
        *,
        network: bool,
        policy: object,
    ) -> ExecutionResult:
        if not command:
            raise ValueError("command is required")
        executable_name = Path(command[0]).stem.lower()
        if executable_name not in self.allowed_commands:
            raise ValueError(f"command is not allowlisted: {executable_name}")
        safe_cwd = cwd.resolve()
        if not safe_cwd.is_relative_to(self.managed_root):
            raise ValueError("working directory is outside the managed project root")
        normalized = self._container_command(command, safe_cwd)
        user = str(os.getuid()) if hasattr(os, "getuid") else "10001"
        limits = policy
        container_name = f"aegaeon-runner-{uuid4().hex}"
        docker_command = [
            runtime,
            "run",
            "--rm",
            "--name",
            container_name,
            "--network",
            "bridge" if network else "none",
            "--memory",
            f"{getattr(limits, 'memory_limit_mb', 2048)}m",
            "--cpus",
            str(getattr(limits, "cpu_limit", 2)),
            "--pids-limit",
            str(getattr(limits, "process_limit", 128)),
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=256m",
            "--user",
            user,
            "--mount",
            f"type=bind,source={safe_cwd},target=/workspace",
            "--workdir",
            "/workspace",
            "--env",
            "HOME=/tmp",
            "--env",
            "CI=1",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            image,
            *normalized,
        ]
        started = time.perf_counter()
        process = await asyncio.create_subprocess_exec(
            *docker_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={
                key: value
                for key, value in os.environ.items()
                if key.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR"}
            },
        )
        stdout_task = asyncio.create_task(self._read_bounded(process.stdout))
        stderr_task = asyncio.create_task(self._read_bounded(process.stderr))
        timed_out = False
        try:
            await asyncio.wait_for(process.wait(), timeout=self.timeout)
        except TimeoutError:
            timed_out = True
            self._terminate_tree(process.pid)
            await process.wait()
            await self._remove_container(runtime, container_name)
        except asyncio.CancelledError:
            self._terminate_tree(process.pid)
            await process.wait()
            await asyncio.shield(self._remove_container(runtime, container_name))
            stdout_task.cancel()
            stderr_task.cancel()
            raise
        stdout, _ = await stdout_task
        stderr, _ = await stderr_task
        return ExecutionResult(
            command=" ".join(command),
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout,
            stderr=stderr,
            duration_seconds=round(time.perf_counter() - started, 3),
            timed_out=timed_out,
        )

    @staticmethod
    async def _remove_container(runtime: str, container_name: str) -> None:
        try:
            cleanup = await asyncio.create_subprocess_exec(
                runtime,
                "rm",
                "--force",
                container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(cleanup.wait(), timeout=10)
        except (OSError, TimeoutError):
            return

    @staticmethod
    def _container_command(command: list[str], workspace: Path) -> list[str]:
        normalized: list[str] = []
        for index, value in enumerate(command):
            path = Path(value)
            if index == 0 and path.stem.lower() == "npm":
                normalized.append("npm")
            elif index == 0 and path.is_absolute() and path.is_relative_to(workspace):
                relative = path.relative_to(workspace)
                if ".venv" in relative.parts:
                    normalized.append("/workspace/.venv/bin/python")
                else:
                    normalized.append("/workspace/" + relative.as_posix())
            elif index == 0 and path.is_absolute() and path.name.lower().startswith("python"):
                normalized.append("python3")
            else:
                normalized.append(value)
        return normalized

    async def _read_bounded(self, stream: asyncio.StreamReader | None) -> tuple[str, bool]:
        if stream is None:
            return "", False
        chunks: list[bytes] = []
        retained = 0
        truncated = False
        while chunk := await stream.read(8192):
            remaining = self.maximum_output - retained
            if remaining > 0:
                selected = chunk[:remaining]
                chunks.append(selected)
                retained += len(selected)
            if len(chunk) > max(0, remaining):
                truncated = True
        text = b"".join(chunks).decode(errors="replace")
        if truncated:
            text += "\n[AEGAEON truncated additional process output]"
        return text, truncated

    @staticmethod
    def _terminate_tree(pid: int) -> None:
        try:
            parent = psutil.Process(pid)
        except psutil.Error:
            return
        processes = parent.children(recursive=True) + [parent]
        for process in reversed(processes):
            try:
                process.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(processes, timeout=2)
        for process in alive:
            try:
                process.kill()
            except psutil.Error:
                pass
