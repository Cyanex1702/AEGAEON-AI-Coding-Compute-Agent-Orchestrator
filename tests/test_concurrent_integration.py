from __future__ import annotations

import pytest

from aegaeon.database.session import Database
from aegaeon.projects.integration import IntegrationConflict
from aegaeon.projects.manager import ProjectManager
from aegaeon.protocol.schemas import ProjectCreate


def manager_with_project(tmp_path) -> tuple[Database, ProjectManager, str]:
    database = Database(f"sqlite:///{tmp_path / 'projects.db'}")
    database.create_all()
    manager = ProjectManager(database, tmp_path / "projects")
    project = manager.create(
        ProjectCreate(prompt="Build a tested Python service with independent modules")
    )
    return database, manager, project.id


def test_non_overlapping_concurrent_changes_rebase_cleanly(tmp_path) -> None:
    database, manager, project_id = manager_with_project(tmp_path)
    base = manager.current_revision(project_id)
    manager.integrate_files(project_id, "module-a", "add module A", {"src/a.py": "A = 1\n"}, base)
    manager.integrate_files(project_id, "module-b", "add module B", {"src/b.py": "B = 2\n"}, base)
    repo = manager.repo_path(project_id)
    assert (repo / "src" / "a.py").is_file()
    assert (repo / "src" / "b.py").is_file()
    database.close()


def test_overlapping_concurrent_changes_raise_without_corrupting_repo(tmp_path) -> None:
    database, manager, project_id = manager_with_project(tmp_path)
    base = manager.current_revision(project_id)
    manager.integrate_files(
        project_id,
        "readme-a",
        "change readme A",
        {"README.md": "# Service\n\nImplementation A.\n"},
        base,
    )

    with pytest.raises(IntegrationConflict) as captured:
        manager.integrate_files(
            project_id,
            "readme-b",
            "change readme B",
            {"README.md": "# Service\n\nImplementation B.\n"},
            base,
        )

    assert captured.value.paths == ["README.md"]
    assert (manager.repo_path(project_id) / "README.md").read_text(encoding="utf-8") == (
        "# Service\n\nImplementation A.\n"
    )
    database.close()
