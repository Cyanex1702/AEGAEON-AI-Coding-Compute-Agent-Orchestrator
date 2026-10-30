from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

MAX_FILE = 512_000
MAX_TOTAL = 20_000_000
MAX_FILES = 500
IGNORED = {
    ".aegaeon-app",
    ".git",
    ".aegaeon",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".next",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".idea",
}
SECRET_SUFFIXES = {".pem", ".key", ".p12", ".pfx", ".sqlite", ".db", ".zip", ".gguf"}
RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_digest(files: dict[str, str]) -> str:
    return digest(json.dumps(files, sort_keys=True, separators=(",", ":")).encode())


def valid_path(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ValueError("invalid relative path")
    if "\\" in value or value.startswith("/") or re.search(r'[\x00-\x1f:<>"|?*]', value):
        raise ValueError("path must be a portable relative POSIX path")
    parts = value.split("/")
    if any(p in {"", ".", ".."} or p.endswith((".", " ")) for p in parts):
        raise ValueError("path contains traversal or ambiguous components")
    if any(p.split(".")[0].upper() in RESERVED for p in parts):
        raise ValueError("path contains a Windows reserved name")
    return value


def excluded(value: str) -> bool:
    parts = value.split("/")
    name = parts[-1].lower()
    return (
        any(p.lower() in IGNORED for p in parts)
        or name.startswith(".env")
        or Path(name).suffix in SECRET_SUFFIXES
        or any(word in name for word in ("credential", "secret", "id_rsa", "id_ed25519"))
    )


def safe_file(root: Path, value: str) -> Path:
    valid_path(value)
    if excluded(value):
        raise ValueError("internal, sensitive or generated path is unavailable")
    root = root.resolve()
    path = root / value
    current = root
    for part in value.split("/"):
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ValueError("symlink/junction paths are unavailable")
    if not path.resolve().is_relative_to(root):
        raise ValueError("path escapes workspace")
    return path


def git(root: Path, *args: str, check: bool = True) -> str:
    process = subprocess.run(
        ["git", "-c", "core.hooksPath=", "-c", "commit.gpgsign=false", "-C", str(root), *args],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if check and process.returncode:
        raise ValueError(process.stderr.strip() or "Git operation failed")
    if process.returncode:
        return ""
    return process.stdout.strip()


def snapshot(root: Path) -> dict[str, str]:
    root = root.resolve()
    if not root.is_dir():
        raise ValueError("workspace does not exist")
    names: list[str] = []
    if git(root, "rev-parse", "--show-toplevel", check=False):
        if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
            raise ValueError("select the Git repository root")
        names = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard").split(
            "\0"
        )
    else:
        for folder, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = [
                d for d in dirs if d.lower() not in IGNORED and not (Path(folder) / d).is_symlink()
            ]
            names.extend((Path(folder) / f).relative_to(root).as_posix() for f in files)
    result: dict[str, str] = {}
    seen: set[str] = set()
    total = 0
    for name in sorted(n for n in names if n and not excluded(n)):
        path = safe_file(root, name)
        if not path.is_file():
            continue
        if name.casefold() in seen:
            raise ValueError("case-colliding filenames cannot be transferred safely")
        seen.add(name.casefold())
        if path.stat().st_size > MAX_FILE:
            raise ValueError(f"source file exceeds {MAX_FILE} bytes: {name}")
        data = path.read_bytes()
        if b"\0" in data:
            continue
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        total += len(data)
        if len(result) >= MAX_FILES or total > MAX_TOTAL:
            raise ValueError("workspace exceeds transfer limits; select a smaller project")
        result[name] = digest(data)
    return result


def in_scope(path: str, scopes: list[str]) -> bool:
    return any(pattern == "**" or fnmatch.fnmatchcase(path, pattern) for pattern in scopes)


def is_test(path: str) -> bool:
    p = Path(path)
    return (
        any(x in {"test", "tests", "__tests__"} for x in p.parts)
        or p.name.startswith("test_")
        or ".test." in p.name
        or ".spec." in p.name
    )


def save_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)
