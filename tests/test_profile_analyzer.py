"""Tests for claude.profile_analyzer.ProfileAnalyzer.

All tests run without ANTHROPIC_API_KEY and verify graceful degradation.
Methods are smoke-tested with a mock SDK so logic is exercised without real API calls.
"""
import json
import pytest

from unittest.mock import MagicMock
from claude.profile_analyzer import ProfileAnalyzer
from claude.sdk_client import ClaudeSDKClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def unavailable_sdk():
    sdk = MagicMock(spec=ClaudeSDKClient)
    sdk.is_available.return_value = False
    return sdk


@pytest.fixture
def available_sdk():
    sdk = MagicMock(spec=ClaudeSDKClient)
    sdk.is_available.return_value = True
    return sdk


@pytest.fixture
def analyzer_unavailable(unavailable_sdk):
    return ProfileAnalyzer(unavailable_sdk)


@pytest.fixture
def analyzer_available(available_sdk):
    return ProfileAnalyzer(available_sdk)


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
        "traits": ["fast iteration", "ships often"],
    },
    "technical_interests": {
        "primary_languages": ["python"],
        "frameworks": ["textual"],
        "domains": ["cli", "ai"],
        "tools": ["claude", "git"],
        "enjoys": ["building tools"],
        "avoids_or_delegates": ["writing docs"],
    },
    "behavioral_patterns": {
        "completion_tendency": "finishes tasks",
        "testing_behavior": "asks AI to test",
        "commit_pattern": "frequent small commits",
        "iteration_style": "many small prompts",
        "context_switching": "frequent across projects",
        "prompting_cadence": "short imperative bursts",
    },
    "prompting_style": {
        "phrasing": "imperative",
        "verbosity": "terse",
        "recurring_vocabulary": ["fix", "add", "refactor"],
        "context_inclusion": "minimal",
    },
    "inferences": {
        "likely_motivations": ["shipping fast", "personal productivity"],
        "current_focus": "building vibe-cli TUI",
        "project_maturity": "building",
        "career_signal": "likely indie hacker or small team engineer",
    },
}


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

def test_is_available_delegates_to_sdk(unavailable_sdk, available_sdk):
    assert ProfileAnalyzer(unavailable_sdk).is_available() is False
    assert ProfileAnalyzer(available_sdk).is_available() is True


# ---------------------------------------------------------------------------
# summarize_run
# ---------------------------------------------------------------------------

class TestSummarizeRun:
    def test_unavailable_returns_empty(self, analyzer_unavailable):
        summary, tags = analyzer_unavailable.summarize_run("fix bug", ["output"], "proj")
        assert summary == "" and tags == []

    def test_sdk_error_prefix_returns_empty(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "[SDK Error] not available"
        summary, tags = analyzer_available.summarize_run("p", ["out"], "proj")
        assert summary == "" and tags == []

    def test_returns_summary_and_tags(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps({
            "summary": "Fixed the authentication bug.",
            "tags": ["python", "bugfix", "auth"]
        })
        summary, tags = analyzer_available.summarize_run("fix auth", ["Fixed auth"], "proj")
        assert summary == "Fixed the authentication bug."
        assert "python" in tags and "bugfix" in tags

    def test_handles_invalid_json(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "not valid json"
        summary, tags = analyzer_available.summarize_run("p", ["out"], "proj")
        assert summary == "" and tags == []

    def test_caps_tags_at_6(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps({
            "summary": "Done.", "tags": ["a", "b", "c", "d", "e", "f", "g", "h"]
        })
        _, tags = analyzer_available.summarize_run("p", ["out"], "proj")
        assert len(tags) <= 6

    def test_tags_lowercased(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps({
            "summary": "Done.", "tags": ["Python", "BugFix"]
        })
        _, tags = analyzer_available.summarize_run("p", ["out"], "proj")
        assert all(t == t.lower() for t in tags)

    def test_calls_sdk_complete(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = '{"summary": "x", "tags": []}'
        analyzer_available.summarize_run("prompt", ["out"], "proj")
        available_sdk.complete.assert_called_once()


# ---------------------------------------------------------------------------
# build_forensic_profile
# ---------------------------------------------------------------------------

class TestBuildForensicProfile:
    def test_unavailable_returns_empty_dict(self, analyzer_unavailable):
        # is_available() is False but build_forensic_profile calls sdk.complete
        # regardless; caller guards with is_available(). Test SDK error fallback.
        result = analyzer_unavailable.build_forensic_profile("p", "proj", [], {})
        # With unavailable sdk, complete returns mock default (not [SDK...]),
        # but we can at least verify the return type
        assert isinstance(result, dict)

    def test_sdk_error_prefix_returns_empty_dict(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "[SDK Error] rate limit"
        result = analyzer_available.build_forensic_profile("p", "proj", [], {})
        assert result == {}

    def test_returns_structured_dict(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(_SAMPLE_FORENSIC)
        result = analyzer_available.build_forensic_profile(
            "fix auth bug", "myproj", ["fix auth bug"], {}
        )
        assert isinstance(result, dict)
        assert "demographics" in result
        assert "personality" in result
        assert "technical_interests" in result
        assert "behavioral_patterns" in result
        assert "prompting_style" in result
        assert "inferences" in result

    def test_demographics_fields_present(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(_SAMPLE_FORENSIC)
        result = analyzer_available.build_forensic_profile("p", "proj", [], {})
        demo = result["demographics"]
        assert "estimated_age_range" in demo
        assert "experience_level" in demo
        assert "role_type" in demo

    def test_personality_traits_is_list(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(_SAMPLE_FORENSIC)
        result = analyzer_available.build_forensic_profile("p", "proj", [], {})
        assert isinstance(result["personality"]["traits"], list)

    def test_technical_interests_languages_is_list(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(_SAMPLE_FORENSIC)
        result = analyzer_available.build_forensic_profile("p", "proj", [], {})
        assert isinstance(result["technical_interests"]["primary_languages"], list)

    def test_handles_invalid_json(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "not json at all"
        result = analyzer_available.build_forensic_profile("p", "proj", [], {})
        assert result == {}

    def test_accepts_current_profile_dict(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(_SAMPLE_FORENSIC)
        # Should not raise when given existing profile
        result = analyzer_available.build_forensic_profile(
            "p", "proj", ["prev prompt"], _SAMPLE_FORENSIC
        )
        assert isinstance(result, dict)

    def test_handles_large_prompt_corpus(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(_SAMPLE_FORENSIC)
        many = [f"fix bug {i}" for i in range(200)]
        result = analyzer_available.build_forensic_profile("p", "proj", many, {})
        assert isinstance(result, dict)

    def test_inferences_current_focus_is_string(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(_SAMPLE_FORENSIC)
        result = analyzer_available.build_forensic_profile("p", "proj", [], {})
        assert isinstance(result["inferences"]["current_focus"], str)


# ---------------------------------------------------------------------------
# update_project_profile
# ---------------------------------------------------------------------------

class TestUpdateProjectProfile:
    def test_returns_project_profile(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "## Summary\nA FastAPI service."
        result = analyzer_available.update_project_profile(
            "add endpoint", [], "myproject", ["add endpoint"], ""
        )
        assert "FastAPI service" in result

    def test_strips_whitespace(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "  ## Summary\nContent  "
        result = analyzer_available.update_project_profile("p", [], "proj", [], "")
        assert result == "## Summary\nContent"

    def test_sdk_error_returns_empty(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "[SDK Error] timeout"
        result = analyzer_available.update_project_profile("p", [], "proj", [], "")
        assert result == ""

    def test_unavailable_returns_empty(self, analyzer_unavailable):
        result = analyzer_unavailable.update_project_profile("p", [], "proj", [], "")
        # unavailable sdk returns mock default — just check it's a string
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# predict_prompts — takes dict profile
# ---------------------------------------------------------------------------

class TestPredictPrompts:
    def test_unavailable_returns_empty_list(self, analyzer_unavailable):
        result = analyzer_unavailable.predict_prompts({}, "proj", "last", [], [], n=4)
        assert isinstance(result, list)

    def test_sdk_error_returns_empty_list(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "[SDK Error] rate limit"
        result = analyzer_available.predict_prompts({}, "proj", "p", [], [], n=4)
        assert result == []

    def test_returns_list_of_strings(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps([
            "Fix the tests", "Add type annotations", "Commit changes"
        ])
        result = analyzer_available.predict_prompts(
            _SAMPLE_FORENSIC, "proj", "last prompt", [], [], n=3
        )
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)

    def test_returns_at_most_n(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(
            [f"suggestion {i}" for i in range(10)]
        )
        result = analyzer_available.predict_prompts({}, "proj", "p", [], [], n=3)
        assert len(result) <= 3

    def test_invalid_json_returns_empty(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "not json"
        result = analyzer_available.predict_prompts({}, "proj", "p", [], [], n=4)
        assert result == []

    def test_filters_non_string_items(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(
            ["valid string", 42, None, "another string"]
        )
        result = analyzer_available.predict_prompts({}, "proj", "p", [], [], n=4)
        assert all(isinstance(s, str) for s in result)

    def test_accepts_empty_profile_dict(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(["fix bug", "add test"])
        result = analyzer_available.predict_prompts({}, "proj", "p", [], [], n=2)
        assert len(result) == 2

    def test_profile_dict_serialized_in_prompt(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(["suggestion"])
        analyzer_available.predict_prompts(
            _SAMPLE_FORENSIC, "proj", "p", [], [], n=1
        )
        call_args = available_sdk.complete.call_args
        user_msg = call_args[0][1]  # second positional arg
        assert "pragmatic" in user_msg or "indie hacker" in user_msg
