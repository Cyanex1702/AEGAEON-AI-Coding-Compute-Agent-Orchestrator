from __future__ import annotations

import difflib
import json
import stat
import zipfile
from pathlib import Path
from uuid import uuid4

from .session import validate_task
from .workspace import (
    MAX_FILE,
    MAX_FILES,
    MAX_TOTAL,
    digest,
    excluded,
    git,
    in_scope,
    is_test,
    safe_file,
    save_json,
    snapshot,
    tree_digest,
    valid_path,
)


def read_archive(path: Path) -> dict[str, bytes]:
    result = {}
    seen: set[str] = set()
    total = 0
    with zipfile.ZipFile(path) as archive:
        if len(archive.infolist()) > MAX_FILES + 10:
            raise ValueError("archive has too many entries")
        for entry in archive.infolist():
            name = valid_path(entry.filename)
            if entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16):
                raise ValueError("archive directory and symlink entries are not accepted")
            if name.casefold() in seen:
                raise ValueError("archive contains duplicate/case-colliding entries")
            seen.add(name.casefold())
            if entry.file_size > MAX_FILE * 2:
                raise ValueError("archive entry too large")
            if entry.file_size > max(1, entry.compress_size) * 200:
                raise ValueError("archive compression ratio exceeds limit")
            total += entry.file_size
            if total > MAX_TOTAL:
                raise ValueError("archive exceeds unpacked limit")
            result[name] = archive.read(entry)
    return result


def validate_manifest(value: object, kind: str) -> dict:
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("kind") != kind:
        raise ValueError("unsupported bundle manifest")
    if not isinstance(value.get("task_id"), str) or not value["task_id"]:
        raise ValueError("missing task identity")
    if not isinstance(value.get("base_commit"), str) or len(value["base_commit"]) != 40:
        raise ValueError("missing base commit")
    files = value.get("files")
    if not isinstance(files, dict) or len(files) > MAX_FILES:
        raise ValueError("invalid source file manifest")
    names: set[str] = set()
    for name, checksum in files.items():
        valid_path(name)
        if excluded(name) or name.casefold() in names:
            raise ValueError("manifest includes unavailable or case-colliding path")
        names.add(name.casefold())
        if not isinstance(checksum, str) or len(checksum) != 64:
            raise ValueError("invalid source checksum")
    if value.get("source_digest") != tree_digest(files):
        raise ValueError("source manifest checksum mismatch")
    validate_task(value.get("task"))
    return value


def write_archive(output: Path, entries: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
        # Apply the same limits to generated bundles as to imported ones.
        read_archive(temporary)
        temporary.replace(output)
    finally:
        if temporary.exists():
            temporary.unlink()


def initialize(root: Path) -> str:
    root = root.resolve()
    if git(root, "rev-parse", "HEAD", check=False):
        raise ValueError("repository already has commits; commit your source with Git instead")
    if git(root, "diff", "--cached", "--name-only", check=False):
        raise ValueError("unstage existing files before initializing a source checkpoint")
    if not git(root, "rev-parse", "--show-toplevel", check=False):
        git(root, "init", "-b", "main")
    files = snapshot(root)
    if not files:
        raise ValueError("project has no transferable source files")
    # Only explicitly enumerated source files are staged; secret paths are excluded.
    git(root, "add", "--", *files)
    git(
        root,
        "-c",
        "user.name=AEGAEON Lite",
        "-c",
        "user.email=lite@localhost",
        "commit",
        "-m",
        "AEGAEON Lite: source checkpoint",
    )
    return git(root, "rev-parse", "HEAD")


def export_task(root: Path, task: dict, output: Path) -> dict:
    root = root.resolve()
    validate_task(task)
    base = git(root, "rev-parse", "HEAD")
    files = snapshot(root)
    if not files:
        raise ValueError("no source files to export")
    # Check every included file against HEAD. Uncommitted source cannot be silently lost.
    for name, checksum in files.items():
        blob = git(root, "rev-parse", f"HEAD:{name}", check=False)
        if not blob:
            raise ValueError(f"commit source before export: {name}")
        import subprocess

        data = subprocess.run(
            ["git", "-C", str(root), "show", f"HEAD:{name}"],
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout
        working = safe_file(root, name).read_bytes()
        if digest(data) != checksum and data.replace(b"\r\n", b"\n") != working.replace(
            b"\r\n", b"\n"
        ):
            # Git may normalize CRLF at commit. Transfer hashes still bind the exact
            # working bytes; only the cleanliness comparison tolerates line endings.
            raise ValueError(
                f"working source differs from committed bytes: {name}; commit changes first"
            )
    manifest = {
        "version": 1,
        "kind": "task",
        "task_id": "task-" + uuid4().hex[:12],
        "base_commit": base,
        "files": files,
        "source_digest": tree_digest(files),
        "task": task,
    }
    entries = {"manifest.json": json.dumps(manifest, indent=2).encode()}
    entries.update({"source/" + name: safe_file(root, name).read_bytes() for name in files})
    write_archive(output, entries)
    return manifest


def unpack_task(bundle: Path, destination: Path) -> tuple[Path, dict]:
    entries = read_archive(bundle)
    manifest = validate_manifest(json.loads(entries.get("manifest.json", b"{}")), "task")
    expected = {"manifest.json", *("source/" + n for n in manifest["files"])}
    if set(entries) != expected:
        raise ValueError("bundle contains missing or unexpected files")
    for name, checksum in manifest["files"].items():
        data = entries["source/" + name]
        if len(data) > MAX_FILE or digest(data) != checksum:
            raise ValueError("source file checksum/size mismatch")
        data.decode("utf-8")
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("destination must not already exist")
    destination.mkdir(parents=True)
    root = destination / "project"
    root.mkdir()
    for name in manifest["files"]:
        path = safe_file(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(entries["source/" + name])
    git(root, "init", "-b", "main")
    git(root, "add", ".")
    git(
        root,
        "-c",
        "user.name=AEGAEON Lite",
        "-c",
        "user.email=lite@localhost",
        "commit",
        "-m",
        "AEGAEON Lite: imported source",
    )
    save_json(root / ".aegaeon" / "task-manifest.json", manifest)
    return root, manifest


def pack_result(root: Path, original: dict, checkpoint: dict, output: Path) -> dict:
    validate_manifest(original, "task")
    current = snapshot(root)
    removed = set(original["files"]) - set(current)
    if removed:
        raise ValueError("this MVP does not export deletions")
    changed = {
        name: checksum
        for name, checksum in current.items()
        if original["files"].get(name) != checksum
    }
    task = original["task"]
    if len(changed) > task.get("max_changed_files", 3):
        raise ValueError("result exceeds changed-file budget")
    for name in changed:
        if not in_scope(name, task.get("allowed_paths", ["**"])):
            raise ValueError("result contains changes outside task scope")
        if name in original["files"] and is_test(name) and not task.get("allow_test_edits"):
            raise ValueError("result modifies protected existing tests")
    manifest = {
        **original,
        "kind": "result",
        "result_id": "result-" + uuid4().hex[:12],
        "changed": changed,
        "result_files": current,
        "result_digest": tree_digest(current),
    }
    entries = {
        "manifest.json": json.dumps(manifest, indent=2).encode(),
        "report.json": json.dumps(checkpoint, indent=2).encode(),
    }
    entries.update({"changes/" + name: safe_file(root, name).read_bytes() for name in changed})
    write_archive(output, entries)
    return manifest


def import_result(
    root: Path,
    bundle: Path,
    *,
    apply: bool = False,
    staging_parent: Path | None = None,
    allow_empty: bool = False,
) -> dict:
    root = root.resolve()
    entries = read_archive(bundle)
    manifest = validate_manifest(json.loads(entries.get("manifest.json", b"{}")), "result")
    changed = manifest.get("changed")
    if not isinstance(changed, dict) or len(changed) > manifest["task"].get("max_changed_files", 3):
        raise ValueError("invalid changed-file list")
    if set(entries) != {"manifest.json", "report.json", *("changes/" + n for n in changed)}:
        raise ValueError("unexpected result entries")
    if git(root, "rev-parse", "HEAD") != manifest["base_commit"]:
        raise ValueError("stale result: canonical HEAD changed after task export")
    if snapshot(root) != manifest["files"]:
        raise ValueError("conflict: canonical source changed after task export")
    current = dict(manifest["files"])
    patch = []
    for name, checksum in changed.items():
        path = safe_file(root, name)
        if not in_scope(name, manifest["task"].get("allowed_paths", ["**"])):
            raise ValueError("result changes a path outside task scope")
        if (
            name in manifest["files"]
            and is_test(name)
            and not manifest["task"].get("allow_test_edits")
        ):
            raise ValueError("result changes protected existing tests")
        data = entries["changes/" + name]
        if len(data) > MAX_FILE or digest(data) != checksum:
            raise ValueError("result file checksum/size mismatch")
        after = data.decode("utf-8")
        before = path.read_text(encoding="utf-8") if path.exists() else ""
        patch.extend(
            difflib.unified_diff(
                before.splitlines(True),
                after.splitlines(True),
                fromfile="a/" + name,
                tofile="b/" + name,
            )
        )
        current[name] = checksum
    if manifest.get("result_files") != current or manifest.get("result_digest") != tree_digest(
        current
    ):
        raise ValueError("result source digest mismatch")
    if len({n.casefold() for n in current}) != len(current):
        raise ValueError("result includes case-colliding filenames")
    result_id = manifest.get("result_id")
    if not isinstance(result_id, str) or not result_id.startswith("result-"):
        raise ValueError("missing result identity")
    valid_path(result_id)
    applied = root / ".aegaeon" / "imports" / (result_id + ".json")
    if applied.exists():
        raise ValueError("duplicate result: already imported")
    report = json.loads(entries["report.json"])
    if not isinstance(report, dict):
        raise ValueError("invalid report")
    evidence = "Unverified external report"
    checks = manifest["task"].get("checks", [])
    reports = report.get("checks", [])
    if (
        isinstance(reports, list)
        and checks
        and all(
            any(
                isinstance(r, dict)
                and r.get("id") == c["id"]
                and r.get("status") == "passed"
                and r.get("source_digest") == manifest["result_digest"]
                and r.get("argv") == c["argv"]
                and r.get("cwd", ".") == c.get("cwd", ".")
                for r in reports
            )
            for c in checks
        )
    ):
        evidence = "Notebook reports checks passed; rerun locally to independently verify"
    output = {
        "result_id": result_id,
        "files": list(changed),
        "diff": "".join(patch),
        "evidence": evidence,
        "summary": report.get("summary", ""),
        "applied": False,
    }
    if not apply or (not changed and not allow_empty):
        return output
    # A separate worktree/branch avoids modifying the canonical working copy.
    branch = "aegaeon/" + result_id
    staging = (staging_parent or root / ".aegaeon" / "imports") / result_id
    staging.parent.mkdir(parents=True, exist_ok=True)
    git(root, "worktree", "add", "-b", branch, str(staging), manifest["base_commit"])
    # Git checkout may apply core.autocrlf or .gitattributes. A checkpoint binds
    # exact bytes, including unchanged files; preserve the validated baseline.
    for name in manifest["files"]:
        path = safe_file(staging, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(safe_file(root, name).read_bytes())
    for name in changed:
        path = safe_file(staging, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(entries["changes/" + name])
    if changed:
        git(staging, "add", "--", *changed)
        git(
            staging,
            "-c",
            "user.name=AEGAEON Lite",
            "-c",
            "user.email=lite@localhost",
            "commit",
            "-m",
            "AEGAEON Lite: " + result_id,
        )
    output.update(
        {
            "applied": True,
            "branch": branch,
            "worktree": str(staging),
            "commit": git(staging, "rev-parse", "HEAD"),
        }
    )
    save_json(applied, {k: v for k, v in output.items() if k != "diff"})
    return output


def restore_checkpoint(root: Path, original: dict, bundle: Path) -> dict:
    """Restore downloaded work into a fresh task workspace after runtime loss."""
    entries = read_archive(bundle)
    manifest = validate_manifest(json.loads(entries.get("manifest.json", b"{}")), "result")
    if any(
        manifest.get(k) != original.get(k)
        for k in ("task_id", "base_commit", "files", "source_digest", "task")
    ):
        raise ValueError("checkpoint belongs to a different task")
    if snapshot(root) != original["files"]:
        raise ValueError("restore requires a fresh unmodified task workspace")
    changed = manifest.get("changed")
    if not isinstance(changed, dict) or len(changed) > original["task"].get("max_changed_files", 3):
        raise ValueError("invalid checkpoint changed-file budget")
    if set(entries) != {"manifest.json", "report.json", *("changes/" + n for n in changed)}:
        raise ValueError("invalid checkpoint entries")
    current = dict(original["files"])
    for name, checksum in changed.items():
        safe_file(root, name)
        if not in_scope(name, original["task"].get("allowed_paths", ["**"])):
            raise ValueError("checkpoint changes path outside scope")
        if (
            name in original["files"]
            and is_test(name)
            and not original["task"].get("allow_test_edits")
        ):
            raise ValueError("checkpoint changes protected tests")
        data = entries["changes/" + name]
        if digest(data) != checksum or len(data) > MAX_FILE:
            raise ValueError("checkpoint checksum mismatch")
        data.decode("utf-8")
        current[name] = checksum
    if (
        manifest.get("result_files") != current
        or manifest.get("result_digest") != tree_digest(current)
        or len({n.casefold() for n in current}) != len(current)
    ):
        raise ValueError("checkpoint source digest mismatch")
    report = json.loads(entries["report.json"])
    if not isinstance(report, dict):
        raise ValueError("invalid checkpoint report")
    for name in changed:
        path = safe_file(root, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(entries["changes/" + name])
    return report
