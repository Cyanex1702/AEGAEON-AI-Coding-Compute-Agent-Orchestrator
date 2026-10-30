"""Portable accepted source; restored reports remain historical claims."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from uuid import uuid4

from .bundles import read_archive, write_archive
from .plans import validate_plan
from .workspace import MAX_FILE, MAX_FILES, digest, excluded, safe_file, snapshot, tree_digest


def remaining_plan(spec: dict, accepted: list[str]) -> dict | None:
    spec = validate_plan(spec)
    if (
        not isinstance(accepted, list)
        or any(not isinstance(x, str) for x in accepted)
        or len(set(accepted)) != len(accepted)
    ):
        raise ValueError("Invalid accepted-step list")
    steps = {step["id"]: step for step in spec["steps"]}
    if not set(accepted) <= set(steps):
        raise ValueError("Unknown accepted step")
    if any(not set(steps[key]["depends_on"]) <= set(accepted) for key in accepted):
        raise ValueError("Accepted steps are missing dependencies")
    pending = [copy.deepcopy(s) for s in spec["steps"] if s["id"] not in accepted]
    if not pending:
        return None
    for step in pending:
        step["depends_on"] = [key for key in step["depends_on"] if key not in accepted]
    required = {key for step in pending for key in step["requirements"]}
    return validate_plan(
        {
            "goal": spec["goal"],
            "steps": pending,
            "requirements": [r for r in spec["requirements"] if r["id"] in required],
        }
    )


def export_checkpoint(
    root: Path, spec: dict, accepted: list[str], history: list, output: Path, *, title: str
) -> dict:
    spec = validate_plan(spec)
    remaining_plan(spec, accepted)
    files = snapshot(root)
    manifest = {
        "kind": "checkpoint",
        "version": 1,
        "id": "checkpoint-" + uuid4().hex[:12],
        "title": title[:200],
        "created": time.time(),
        "spec": spec,
        "accepted_steps": accepted,
        "history": history,
        "files": files,
        "source_digest": tree_digest(files),
        "notice": "Accepted source only. Imported history is unverified; "
        "Git history, unaccepted attempts and dependencies are not included.",
    }
    entries = {"manifest.json": json.dumps(manifest, ensure_ascii=False).encode("utf-8")}
    entries.update({"source/" + name: safe_file(root, name).read_bytes() for name in files})
    write_archive(output, entries)
    read_checkpoint(output)
    return manifest


def read_checkpoint(bundle: Path) -> tuple[dict, dict[str, bytes]]:
    entries = read_archive(bundle)
    manifest = json.loads(entries.get("manifest.json", b"{}"))
    if (
        not isinstance(manifest, dict)
        or manifest.get("kind") != "checkpoint"
        or manifest.get("version") != 1
    ):
        raise ValueError("Unsupported checkpoint format")
    if not isinstance(manifest.get("id"), str) or not re.fullmatch(
        r"checkpoint-[a-f0-9]{12}", manifest["id"]
    ):
        raise ValueError("Invalid checkpoint identity")
    spec = validate_plan(manifest.get("spec"))
    accepted = manifest.get("accepted_steps")
    pending = remaining_plan(spec, accepted)
    files = manifest.get("files")
    if not isinstance(files, dict) or not 1 <= len(files) <= MAX_FILES:
        raise ValueError("Invalid checkpoint source list")
    if set(entries) != {"manifest.json", *("source/" + name for name in files)}:
        raise ValueError("Checkpoint has missing or unexpected entries")
    seen = set()
    for name, checksum in files.items():
        from .workspace import valid_path

        valid_path(name)
        if excluded(name) or name.casefold() in seen:
            raise ValueError("Checkpoint contains excluded or case-colliding source")
        seen.add(name.casefold())
        data = entries["source/" + name]
        if len(data) > MAX_FILE or checksum != digest(data):
            raise ValueError("Checkpoint source checksum/size mismatch")
        if b"\0" in data:
            raise ValueError("Checkpoint contains binary source")
        data.decode("utf-8")
    for name in seen:
        if any("/".join(name.split("/")[:i]) in seen for i in range(1, len(name.split("/")))):
            raise ValueError("Checkpoint file and directory paths collide")
    if manifest.get("source_digest") != tree_digest(files):
        raise ValueError("Checkpoint source digest mismatch")
    history = manifest.get("history")
    if (
        not isinstance(history, list)
        or len(history) > 64
        or any(not isinstance(item, dict) for item in history)
        or len(json.dumps(history).encode()) > 400_000
    ):
        raise ValueError("Checkpoint historical evidence exceeds limits")
    title = manifest.get("title")
    if not isinstance(title, str) or not 1 <= len(title) <= 200:
        raise ValueError("Invalid checkpoint title")
    # Ignore unknown fields, especially filesystem paths, state and executable run flags.
    clean = {
        key: manifest[key] for key in ("kind", "version", "id", "title", "files", "source_digest")
    }
    clean.update(spec=spec, accepted_steps=accepted, history=history, remaining=pending)
    return clean, entries


def inspect_checkpoint(bundle: Path) -> dict:
    manifest, _ = read_checkpoint(bundle)
    return {
        "id": manifest["id"],
        "title": manifest["title"],
        "goal": manifest["spec"]["goal"],
        "files": list(manifest["files"]),
        "accepted_steps": manifest["accepted_steps"],
        "remaining_steps": [s["id"] for s in manifest["remaining"]["steps"]]
        if manifest["remaining"]
        else [],
        "history": manifest["history"],
        "notice": "Reports are imported history, not fresh local verification. "
        "Restore creates a new Git project; it does not run declared checks.",
    }


def _git_input(root: Path, args: list[str], data: bytes = b"", *, allow_failure=False) -> str:
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    result = subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=",
            "-c",
            "core.autocrlf=false",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "user.name=AEGAEON Lite",
            "-c",
            "user.email=lite@localhost",
            "-C",
            str(root),
            *args,
        ],
        input=data,
        capture_output=True,
        timeout=60,
        env=env,
    )
    if result.returncode:
        if allow_failure:
            return ""
        raise ValueError(
            "Git checkpoint restore failed: " + result.stderr.decode(errors="replace")[:800]
        )
    return result.stdout.decode("utf-8").strip()


def restore_source(bundle: Path, destination: Path) -> dict:
    manifest, entries = read_checkpoint(bundle)
    if not shutil.which("git"):
        raise ValueError("Git is required to restore a checkpoint")
    destination = destination.expanduser().absolute()
    if destination.exists() or destination.is_symlink():
        raise ValueError("Restore requires a new folder; existing folders are never overwritten")
    if not destination.parent.is_dir():
        raise ValueError("Create the parent folder first")
    if (
        _git_input(destination.parent, ["rev-parse", "--is-inside-work-tree"], allow_failure=True)
        == "true"
    ):
        raise ValueError("Choose a destination outside an existing Git repository")
    destination.mkdir(exist_ok=False)
    try:
        for name in manifest["files"]:
            path = safe_file(destination, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(entries["source/" + name])
        _git_input(destination, ["init", "--object-format=sha1", "--template=", "-b", "main"])
        _git_input(destination, ["config", "core.autocrlf", "false"])
        names = sorted(manifest["files"])
        # Plumbing stores raw text, avoiding attributes, filters, ignored-file loss and hooks.
        objects = _git_input(
            destination,
            ["hash-object", "-w", "--stdin-paths", "--no-filters"],
            ("\n".join(names) + "\n").encode(),
        ).splitlines()
        if len(objects) != len(names) or any(not re.fullmatch(r"[a-f0-9]{40}", x) for x in objects):
            raise ValueError("Git returned an invalid source object list")
        index = b"".join(
            f"100644 {obj}\t{name}\0".encode() for name, obj in zip(names, objects, strict=True)
        )
        _git_input(destination, ["update-index", "-z", "--index-info"], index)
        tree = _git_input(destination, ["write-tree"])
        commit = _git_input(
            destination, ["commit-tree", tree, "-m", "AEGAEON Lite: restored checkpoint"]
        )
        _git_input(destination, ["update-ref", "refs/heads/main", commit])
        if snapshot(destination) != manifest["files"]:
            raise ValueError("Restored source differs from the validated checkpoint")
    except Exception as exc:
        raise ValueError(
            f"Restore stopped. Newly created folder retained at {destination}: {exc}"
        ) from exc
    return {"root": str(destination.resolve()), "commit": commit, "manifest": manifest}
