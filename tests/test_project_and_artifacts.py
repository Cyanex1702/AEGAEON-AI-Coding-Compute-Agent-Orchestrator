from pathlib import Path

from aegaeon.artifacts.manager import ArtifactManager
from aegaeon.database.session import Database
from aegaeon.projects.manager import ProjectManager
from aegaeon.protocol.schemas import ProjectCreate


def test_project_creation_patch_and_artifact(settings) -> None:
    database = Database(settings.database_url)
    database.create_all()
    manager = ProjectManager(database, settings.projects_dir)
    project = manager.create(
        ProjectCreate(prompt="Build a typed FastAPI calculator with automated tests")
    )
    repo = Path(project.workspace_path)
    assert (repo / ".git").is_dir()
    assert (repo.parent / "AEGAEON" / "history.json").is_file()

    modified, patch = manager.integrate_files(
        project.id, "task-001", "add app", {"app/main.py": "VALUE = 4\n"}
    )
    assert modified == ["app/main.py"]
    assert "VALUE = 4" in patch

    artifacts = ArtifactManager(database, settings.projects_dir)
    artifact = artifacts.create(project.id, "task-001", "git_patch", "task.patch", patch)
    assert artifact.size_bytes > 0
    assert len(artifact.checksum) == 64
    assert artifacts.path_for(artifact).is_file()
    database.close()
