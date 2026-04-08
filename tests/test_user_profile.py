"""Tests for memory.user_profile.UserProfile — forensic JSON + per-project profiles."""
import os
import pytest

from memory.vault import MemoryVault
from memory.user_profile import UserProfile, _PROJECT_TEMPLATE, _profile_to_markdown


_SAMPLE_FORENSIC = {
    "demographics": {
        "estimated_age_range": "25-35",
        "likely_occupation": "senior software engineer",
        "likely_location": "US",
        "experience_level": "senior",
        "role_type": "indie hacker",
        "education_signal": "CS degree",
    },
    "personality": {
        "work_style": "pragmatic",
        "approach": "exploratory",
        "focus_granularity": "detail-oriented",
        "confidence": "confident",
        "traits": ["fast iteration"],
    },
    "technical_interests": {
        "primary_languages": ["python"],
        "frameworks": ["textual"],
        "domains": ["cli"],
        "tools": ["git"],
        "enjoys": ["building tools"],
        "avoids_or_delegates": ["docs"],
    },
    "behavioral_patterns": {
        "completion_tendency": "finishes tasks",
        "testing_behavior": "asks AI to test",
        "commit_pattern": "frequent small commits",
        "iteration_style": "many small prompts",
        "context_switching": "frequent",
        "prompting_cadence": "short bursts",
    },
    "prompting_style": {
        "phrasing": "imperative",
        "verbosity": "terse",
        "recurring_vocabulary": ["fix", "add"],
        "context_inclusion": "minimal",
    },
    "inferences": {
        "likely_motivations": ["shipping fast"],
        "current_focus": "building vibe-cli",
        "project_maturity": "building",
        "career_signal": "indie hacker",
    },
}


@pytest.fixture
def vault(tmp_path):
    return MemoryVault(str(tmp_path / "vault"))


@pytest.fixture
def profile(vault):
    return UserProfile(vault)


# ---------------------------------------------------------------------------
# Forensic JSON profile
# ---------------------------------------------------------------------------

class TestForensicJSONProfile:
    def test_read_json_returns_empty_dict_when_missing(self, profile):
        result = profile.read_json()
        assert result == {}

    def test_exists_false_initially(self, profile):
        assert not profile.exists()

    def test_write_json_creates_file(self, profile):
        profile.write_json(_SAMPLE_FORENSIC)
        assert profile.exists()
        assert os.path.isfile(profile.json_path)

    def test_write_json_roundtrip(self, profile):
        profile.write_json(_SAMPLE_FORENSIC)
        loaded = profile.read_json()
        assert loaded["demographics"]["experience_level"] == "senior"
        assert loaded["personality"]["work_style"] == "pragmatic"
        assert loaded["technical_interests"]["primary_languages"] == ["python"]

    def test_write_json_creates_markdown_view(self, profile):
        profile.write_json(_SAMPLE_FORENSIC)
        assert os.path.isfile(profile.md_path)

    def test_markdown_view_contains_key_data(self, profile):
        profile.write_json(_SAMPLE_FORENSIC)
        md = profile.read()
        assert "senior software engineer" in md
        assert "python" in md
        assert "pragmatic" in md

    def test_write_json_overwrites(self, profile):
        profile.write_json(_SAMPLE_FORENSIC)
        updated = dict(_SAMPLE_FORENSIC)
        updated["demographics"] = dict(_SAMPLE_FORENSIC["demographics"])
        updated["demographics"]["experience_level"] = "expert"
        profile.write_json(updated)
        loaded = profile.read_json()
        assert loaded["demographics"]["experience_level"] == "expert"

    def test_json_path_under_vault(self, profile, vault):
        assert profile.json_path.startswith(vault.root)

    def test_md_path_under_vault(self, profile, vault):
        assert profile.md_path.startswith(vault.root)


# ---------------------------------------------------------------------------
# _profile_to_markdown
# ---------------------------------------------------------------------------

class TestProfileToMarkdown:
    def test_returns_template_for_empty_dict(self):
        md = _profile_to_markdown({})
        assert "VibeCLI User Profile" in md

    def test_contains_all_sections(self):
        md = _profile_to_markdown(_SAMPLE_FORENSIC)
        for section in ("Demographics", "Personality", "Technical Interests",
                        "Behavioral Patterns", "Prompting Style", "Inferences"):
            assert section in md

    def test_lists_rendered_as_comma_separated(self):
        md = _profile_to_markdown(_SAMPLE_FORENSIC)
        assert "python" in md

    def test_scalar_values_rendered(self):
        md = _profile_to_markdown(_SAMPLE_FORENSIC)
        assert "senior software engineer" in md

    def test_traits_list_shown(self):
        md = _profile_to_markdown(_SAMPLE_FORENSIC)
        assert "fast iteration" in md


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
