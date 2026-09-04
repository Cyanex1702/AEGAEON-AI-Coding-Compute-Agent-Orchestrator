from __future__ import annotations

import argparse
import stat
import zipfile
from pathlib import Path

EXCLUDED_PARTS = {
    ".git",
    ".next",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".runtime",
    ".venv",
    "__pycache__",
    "data",
    "node_modules",
}
EXCLUDED_SUFFIXES = {".db", ".pem", ".key", ".p12", ".pfx", ".pyc"}


def included(path: Path, root: Path, destination: Path) -> bool:
    relative = path.relative_to(root)
    if path.resolve() == destination.resolve():
        return False
    if any(part in EXCLUDED_PARTS or part.startswith(".local-run-") for part in relative.parts):
        return False
    if path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    if path.name == ".env" or path.name.startswith(".env.") and path.name != ".env.example":
        return False
    return path.is_file()


def build_release(root: Path, destination: Path) -> None:
    root = root.resolve()
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = sorted(
        (path for path in root.rglob("*") if included(path, root, destination)),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    with zipfile.ZipFile(
        destination, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in files:
            relative = path.relative_to(root).as_posix()
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(
                info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a deterministic AEGAEON source archive")
    parser.add_argument("destination", type=Path)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    build_release(args.root, args.destination)


if __name__ == "__main__":
    main()
