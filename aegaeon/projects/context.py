from __future__ import annotations

import re
from pathlib import Path


class RepositoryContext:
    """Focused context retrieval using paths, text matches, and Python imports."""

    ignored_directories = {
        ".git",
        ".next",
        ".venv",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "__pycache__",
    }
    ignored_suffixes = {
        ".7z",
        ".bin",
        ".dll",
        ".exe",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".lock",
        ".pdf",
        ".png",
        ".pyc",
        ".svg",
        ".webp",
        ".zip",
    }
    sensitive_names = {".env", "credentials.json", "id_rsa", "id_ed25519"}

    def __init__(self, repo: Path, maximum_file_bytes: int = 100_000) -> None:
        self.repo = repo.resolve()
        self.maximum_file_bytes = maximum_file_bytes

    def tree(self) -> list[str]:
        files: list[str] = []
        for path in self.repo.rglob("*"):
            if not path.is_file() or self._ignored(path):
                continue
            files.append(path.relative_to(self.repo).as_posix())
        return sorted(files)

    def search(self, terms: list[str], limit: int = 12) -> list[Path]:
        scored: list[tuple[int, str, Path]] = []
        lowered = [term.lower() for term in terms if len(term) > 2]
        for relative in self.tree():
            path = self.repo / relative
            if path.stat().st_size > self.maximum_file_bytes:
                continue
            score = sum(term in relative.lower() for term in lowered) * 5
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            score += sum(min(5, content.lower().count(term)) for term in lowered)
            if score:
                scored.append((score, relative, path))
        return [path for _, _, path in sorted(scored, reverse=True)[:limit]]

    def render(self, task_text: str, maximum_characters: int = 60_000) -> str:
        tree = self.tree()
        terms = sorted(set(re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", task_text.lower())))
        selected = self.search(terms, limit=16)
        if not selected:
            preferred = {
                "README.md",
                "pyproject.toml",
                "package.json",
                "requirements.txt",
                "app/main.py",
                "src/main.ts",
                "src/App.tsx",
            }
            selected = [self.repo / item for item in tree if item in preferred]

        sections = ["FILE TREE\n" + "\n".join(tree[:2_000])]
        remaining = maximum_characters - len(sections[0])
        for path in selected:
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            relative = path.relative_to(self.repo).as_posix()
            allowance = min(len(content), max(0, remaining - len(relative) - 30))
            if allowance <= 0:
                break
            sections.append(f"FILE: {relative}\n{content[:allowance]}")
            remaining -= allowance + len(relative) + 30
        return "\n\n".join(sections)

    def imports(self, path: Path) -> set[str]:
        content = path.read_text(encoding="utf-8")
        return set(re.findall(r"^(?:from|import)\s+([\w.]+)", content, re.MULTILINE))

    def _ignored(self, path: Path) -> bool:
        relative = path.relative_to(self.repo)
        if any(part in self.ignored_directories for part in relative.parts):
            return True
        if path.name.lower() in self.sensitive_names:
            return True
        return path.suffix.lower() in self.ignored_suffixes
