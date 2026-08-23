from __future__ import annotations


class IntegrationConflict(RuntimeError):
    """Raised when concurrent source changes cannot be merged without markers."""

    def __init__(self, paths: list[str], detail: str) -> None:
        self.paths = paths
        self.detail = detail
        super().__init__(f"integration conflict in {', '.join(paths)}: {detail}")
