from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from aegaeon.protocol.schemas import ExecutionResult


class SubprocessRunner:
    """Development-only command sandbox restricted to an allowlist and managed roots."""

    allowed_commands = {"python", "pytest", "ruff", "mypy", "npm", "npx", "tsc"}

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
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=safe_cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout)
        except TimeoutError:
            timed_out = True
            process.kill()
            stdout, stderr = await process.communicate()
        return ExecutionResult(
            command=" ".join(command),
            exit_code=process.returncode if process.returncode is not None else -1,
            stdout=stdout.decode(errors="replace")[: self.maximum_output],
            stderr=stderr.decode(errors="replace")[: self.maximum_output],
            duration_seconds=round(time.perf_counter() - started, 3),
            timed_out=timed_out,
        )
