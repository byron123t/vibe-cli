"""Tests for core.project_manager.ProjectManager."""
import os
import pytest

from core.project_manager import Project, ProjectManager


@pytest.fixture
def pm(tmp_path, monkeypatch):
    """ProjectManager backed by a temp vault dir (no file I/O to real vault)."""
    projects_file = str(tmp_path / "projects.json")
    monkeypatch.setattr("core.project_manager.PROJECTS_FILE", projects_file)
    return ProjectManager()


def test_add_project(pm, tmp_path):
    d = tmp_path / "myproject"
    d.mkdir()
    proj = pm.add_project(str(d))
    assert proj.name == "myproject"
    assert proj.path == str(d)
    assert len(pm.projects) == 1


def test_add_duplicate(pm, tmp_path):
    d = tmp_path / "dup"
    d.mkdir()
    p1 = pm.add_project(str(d))
    p2 = pm.add_project(str(d))
    assert p1 is p2
    assert len(pm.projects) == 1


def test_next_prev_project(pm, tmp_path):
    dirs = []
    for name in ("a", "b", "c"):
        d = tmp_path / name
        d.mkdir()
        dirs.append(d)
        pm.add_project(str(d))

    pm.set_active(0)
    pm.next_project()
    assert pm.active_idx == 1
    pm.next_project()
    assert pm.active_idx == 2
    pm.next_project()  # wraps around
    assert pm.active_idx == 0

    pm.set_active(0)
    pm.prev_project()  # wraps to last
    assert pm.active_idx == 2
    pm.prev_project()
    assert pm.active_idx == 1


def test_set_active(pm, tmp_path):
    for name in ("x", "y", "z"):
        d = tmp_path / name
        d.mkdir()
        pm.add_project(str(d))

    pm.set_active(2)
    assert pm.active.name == "z"
    pm.set_active(0)
    assert pm.active.name == "x"


def test_remove_project(pm, tmp_path):
    for name in ("r1", "r2"):
        d = tmp_path / name
        d.mkdir()
        pm.add_project(str(d))
    assert len(pm.projects) == 2
    pm.remove_project(0)
    assert len(pm.projects) == 1
    assert pm.projects[0].name == "r2"


def test_resolve_active_file(pm, tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    main_py = d / "main.py"
    main_py.write_text("# hello")
    proj = pm.add_project(str(d))
    result = proj.resolve_active_file()
    assert result is not None
    assert result.endswith("main.py")
    assert os.path.isfile(result)


def test_is_git_repo(tmp_path):
    d = tmp_path / "notgit"
    d.mkdir()
    proj = Project(name="notgit", path=str(d))
    assert proj.is_git_repo() is False
