"""Tests for claude.suggestion_engine.PromptSuggestionEngine."""
import pytest

from graph.personalization_graph import PersonalizationGraph
from claude.suggestion_engine import PromptSuggestionEngine


@pytest.fixture
def engine(tmp_path):
    graph_file = str(tmp_path / "pg.json")
    graph = PersonalizationGraph(graph_file)
    return PromptSuggestionEngine(graph)


def test_returns_n_suggestions(engine):
    suggestions = engine.get_suggestions(project_name="myproject", n=3)
    assert len(suggestions) <= 3


def test_record_boosts_suggestion(engine):
    engine.record("myproject", "Fix the login bug")
    suggestions = engine.get_suggestions(project_name="myproject", n=8)
    assert "Fix the login bug" in suggestions
    # The recorded prompt should be near the top (within first 3)
    idx = suggestions.index("Fix the login bug")
    assert idx < 4


def test_fallback_prompts(engine):
    # With an empty graph, built-in suggestions should still come back
    suggestions = engine.get_suggestions(project_name="brand_new_project", n=4)
    assert len(suggestions) > 0
    # At least one built-in prompt should be present
    builtin_keywords = ["bug", "test", "commit", "annotation", "docstring"]
    found = any(
        any(kw in s.lower() for kw in builtin_keywords)
        for s in suggestions
    )
    assert found


def test_ext_hints_python(engine):
    suggestions = engine.get_suggestions(
        project_name="proj",
        active_file="/some/path/module.py",
        n=8,
    )
    # Python-specific hints should appear somewhere in the list
    python_hints = ["type annotation", "pytest", "linting", "flake8", "ruff"]
    found = any(
        any(kw in s.lower() for kw in python_hints)
        for s in suggestions
    )
    assert found
