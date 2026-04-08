"""Tests for memory.user_profile.UserProfile — global and per-project profiles."""
import os
import pytest

from memory.vault import MemoryVault
from memory.user_profile import UserProfile, _GLOBAL_TEMPLATE, _PROJECT_TEMPLATE


@pytest.fixture
def vault(tmp_path):
    return MemoryVault(str(tmp_path / "vault"))


@pytest.fixture
def profile(vault):
    return UserProfile(vault)


# ---------------------------------------------------------------------------
# Global profile
# ---------------------------------------------------------------------------

class TestGlobalProfile:
    def test_read_returns_template_when_missing(self, profile):
        content = profile.read()
        assert "Developer Identity" in content
        assert "Prompting Style" in content

    def test_exists_false_initially(self, profile):
        assert not profile.exists()

    def test_write_creates_file(self, profile):
        profile.write(_GLOBAL_TEMPLATE)
        assert profile.exists()
        assert os.path.isfile(profile.path)

    def test_write_stamps_timestamp(self, profile):
        profile.write(_GLOBAL_TEMPLATE)
        content = profile.read()
        assert "**Last updated:**" in content
        assert "UTC" in content
        # Should NOT still have the placeholder dash
        assert "**Last updated:** —" not in content

    def test_write_read_roundtrip(self, profile):
        custom = _GLOBAL_TEMPLATE.replace(
            "_Not yet observed._", "Expert Python developer."
        )
        profile.write(custom)
        content = profile.read()
        assert "Expert Python developer." in content

    def test_write_overwrites(self, profile):
        profile.write(_GLOBAL_TEMPLATE + "\nFirst write.")
        profile.write(_GLOBAL_TEMPLATE + "\nSecond write.")
        content = profile.read()
        assert "Second write." in content

    def test_path_under_vault(self, profile, vault):
        assert profile.path.startswith(vault.root)

    def test_global_template_has_required_sections(self):
        for section in ("Developer Identity", "Personality Traits",
                        "Technical Interests", "Behavioral Patterns",
                        "Prompting Style", "Current Focus"):
            assert section in _GLOBAL_TEMPLATE


# ---------------------------------------------------------------------------
# Per-project profile
# ---------------------------------------------------------------------------

class TestProjectProfile:
    def test_read_returns_template_when_missing(self, profile):
        content = profile.read_project("myproject")
        assert "myproject" in content
        assert "Tech Stack" in content
        assert "Current Focus" in content

    def test_write_project_creates_file(self, profile, vault):
        profile.write_project("myproject", _PROJECT_TEMPLATE.format(project="myproject"))
        path = os.path.join(vault.root, "projects", "myproject", "profile.md")
        assert os.path.isfile(path)

    def test_write_project_stamps_timestamp(self, profile):
        profile.write_project("proj", _PROJECT_TEMPLATE.format(project="proj"))
        content = profile.read_project("proj")
        assert "UTC" in content

    def test_write_read_roundtrip(self, profile):
        custom = _PROJECT_TEMPLATE.format(project="proj").replace(
            "_Not yet observed._", "FastAPI backend service."
        )
        profile.write_project("proj", custom)
        content = profile.read_project("proj")
        assert "FastAPI backend service." in content

    def test_different_projects_isolated(self, profile):
        profile.write_project("proj_a", "# Project Profile: proj_a\n\nContent A.")
        profile.write_project("proj_b", "# Project Profile: proj_b\n\nContent B.")
        assert "Content A." in profile.read_project("proj_a")
        assert "Content B." in profile.read_project("proj_b")
        assert "Content A." not in profile.read_project("proj_b")

    def test_project_profile_path_under_projects(self, profile, vault):
        profile.write_project("myproj", "content")
        path = os.path.join(vault.root, "projects", "myproj", "profile.md")
        assert os.path.isfile(path)

    def test_project_template_has_required_sections(self):
        rendered = _PROJECT_TEMPLATE.format(project="test")
        for section in ("Summary", "Tech Stack", "Current Focus", "Recurring Tasks"):
            assert section in rendered

    def test_overwrite_project_profile(self, profile):
        profile.write_project("proj", "first content")
        profile.write_project("proj", "second content")
        content = profile.read_project("proj")
        assert "second content" in content
