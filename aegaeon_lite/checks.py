from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from .workspace import MAX_FILE, safe_file, snapshot, tree_digest


def validate_checks(checks: object) -> list[dict]:
    if not isinstance(checks, list) or len(checks) > 20:
        raise ValueError("checks must be a list of at most 20 entries")
    seen = set()
    for item in checks:
        if not isinstance(item, dict) or set(item) - {"id", "argv", "cwd", "timeout"}:
            raise ValueError("invalid check declaration")
        if not isinstance(item.get("id"), str) or not item["id"] or item["id"] in seen:
            raise ValueError("check IDs must be unique nonempty strings")
        seen.add(item["id"])
        argv = item.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or len(argv) > 30
            or any(not isinstance(a, str) or len(a) > 2000 or "\0" in a for a in argv)
        ):
            raise ValueError("check argv must be a bounded string list")
        cwd = item.get("cwd", ".")
        if cwd != ".":
            from .workspace import valid_path

            valid_path(cwd)
        timeout = item.get("timeout", 120)
        if not isinstance(timeout, int) or not 1 <= timeout <= 300:
            raise ValueError("check timeout must be 1..300 seconds")
    return checks


def load_checks(path: Path | None) -> list[dict]:
    if path is None:
        return []
    if path.stat().st_size > MAX_FILE:
        raise ValueError("check configuration too large")
    return validate_checks(json.loads(path.read_text(encoding="utf-8")))


def check_argv(check: dict) -> list[str]:
    argv = list(check["argv"])
    if argv[0] in {"python", "python3"}:
        argv[0] = sys.executable
    if os.name == "nt" and argv[0] in {"npm", "npx"}:
        argv[0] += ".cmd"
    return argv


def stop_tree(process: subprocess.Popen) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()
    process.wait(timeout=10)


def run_check(root: Path, check: dict, allow: bool, *, cancel_event=None) -> dict:
    if not allow:
        return {
            "id": check["id"],
            "status": "not_run",
            "reason": "check execution needs --allow-checks; a workspace is not a sandbox",
        }
    validate_checks([check])
    cwd = root if check.get("cwd", ".") == "." else safe_file(root, check["cwd"])
    if not cwd.is_dir():
        raise ValueError("check working directory does not exist")
    argv = check_argv(check)
    before = tree_digest(snapshot(root))
    started = time.monotonic()
    env = {
        k: v
        for k, v in os.environ.items()
        if k.upper() in {"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "COMSPEC", "LANG"}
    }
    output = bytearray()

    def drain(stream) -> None:
        while data := stream.read(4096):
            remaining = 32_000 - len(output)
            if remaining > 0:
                output.extend(data[:remaining])

    with tempfile.TemporaryDirectory(prefix="aegaeon-check-") as temporary:
        env.update(
            {
                "HOME": temporary,
                "USERPROFILE": temporary,
                "TEMP": temporary,
                "TMP": temporary,
                "CI": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        try:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=os.name != "nt",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
        except OSError as exc:
            return {
                "id": check["id"],
                "status": "failed",
                "error": str(exc),
                "source_digest": before,
                "argv": check["argv"],
                "cwd": check.get("cwd", "."),
            }
        reader = threading.Thread(target=drain, args=(process.stdout,), daemon=True)
        reader.start()
        timed_out = False
        cancelled = False
        try:
            deadline = time.monotonic() + check.get("timeout", 120)
            while process.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    stop_tree(process)
                    break
                if time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(argv, check.get("timeout", 120))
                try:
                    process.wait(timeout=min(0.2, max(0.01, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    continue
        except subprocess.TimeoutExpired:
            timed_out = True
            stop_tree(process)
        except BaseException:
            stop_tree(process)
            raise
        reader.join(timeout=2)
        if reader.is_alive():
            stop_tree(process)
            reader.join(timeout=2)
        output_incomplete = reader.is_alive()
        # Avoid blocking close while a descendant still holds the output pipe open.
        if process.stdout and not output_incomplete:
            process.stdout.close()
    after = tree_digest(snapshot(root))
    return {
        "id": check["id"],
        "status": "passed"
        if process.returncode == 0
        and not timed_out
        and not cancelled
        and not output_incomplete
        and before == after
        else "failed",
        "exit_code": process.returncode,
        "timed_out": timed_out,
        "cancelled": cancelled,
        "output_incomplete": output_incomplete,
        "source_changed": before != after,
        "source_digest": before,
        "argv": check["argv"],
        "cwd": check.get("cwd", "."),
        "platform": sys.platform,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "output": output.decode("utf-8", errors="replace"),
    }
