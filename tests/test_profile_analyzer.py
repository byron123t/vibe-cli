"""Tests for claude.profile_analyzer.ProfileAnalyzer.

All tests run without ANTHROPIC_API_KEY and verify graceful degradation.
The summarize_run / update_profile / predict_prompts paths are smoke-tested
with a mock SDK so the logic is exercised without real API calls.
"""
import json
import pytest

from unittest.mock import MagicMock, patch
from claude.profile_analyzer import ProfileAnalyzer
from claude.sdk_client import ClaudeSDKClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def unavailable_sdk():
    """SDK that reports itself as unavailable (no API key)."""
    sdk = MagicMock(spec=ClaudeSDKClient)
    sdk.is_available.return_value = False
    return sdk


@pytest.fixture
def available_sdk():
    """SDK that reports available and returns controlled responses."""
    sdk = MagicMock(spec=ClaudeSDKClient)
    sdk.is_available.return_value = True
    return sdk


@pytest.fixture
def analyzer_unavailable(unavailable_sdk):
    return ProfileAnalyzer(unavailable_sdk)


@pytest.fixture
def analyzer_available(available_sdk):
    return ProfileAnalyzer(available_sdk)


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------

def test_is_available_delegates_to_sdk(unavailable_sdk, available_sdk):
    assert ProfileAnalyzer(unavailable_sdk).is_available() is False
    assert ProfileAnalyzer(available_sdk).is_available() is True


# ---------------------------------------------------------------------------
# summarize_run — SDK unavailable
# ---------------------------------------------------------------------------

class TestSummarizeRunUnavailable:
    def test_returns_empty_tuple(self, analyzer_unavailable):
        summary, tags = analyzer_unavailable.summarize_run(
            "fix the bug", ["Fixed it"], "myproject"
        )
        assert summary == ""
        assert tags == []

    def test_returns_empty_regardless_of_sdk_response(self, unavailable_sdk):
        # summarize_run gracefully returns empty when SDK errors out.
        # The caller (_post_run_hook) guards with is_available(); the method
        # itself handles [SDK...] prefixed responses as a fallback.
        unavailable_sdk.complete.return_value = "[SDK] not available"
        pa = ProfileAnalyzer(unavailable_sdk)
        summary, tags = pa.summarize_run("prompt", ["output"], "proj")
        assert summary == ""
        assert tags == []


# ---------------------------------------------------------------------------
# summarize_run — SDK available
# ---------------------------------------------------------------------------

class TestSummarizeRunAvailable:
    def test_returns_summary_and_tags(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps({
            "summary": "Fixed the authentication bug.",
            "tags": ["python", "bugfix", "auth"]
        })
        summary, tags = analyzer_available.summarize_run(
            "fix auth bug", ["Fixed auth"], "proj"
        )
        assert summary == "Fixed the authentication bug."
        assert "python" in tags
        assert "bugfix" in tags

    def test_handles_invalid_json(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "not valid json at all"
        summary, tags = analyzer_available.summarize_run("prompt", ["out"], "proj")
        assert summary == ""
        assert tags == []

    def test_handles_sdk_error_prefix(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "[SDK Error] something went wrong"
        summary, tags = analyzer_available.summarize_run("prompt", ["out"], "proj")
        assert summary == ""
        assert tags == []

    def test_caps_tags_at_6(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps({
            "summary": "Did stuff.",
            "tags": ["a", "b", "c", "d", "e", "f", "g", "h"]
        })
        _, tags = analyzer_available.summarize_run("prompt", ["out"], "proj")
        assert len(tags) <= 6

    def test_tags_lowercased(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps({
            "summary": "Done.",
            "tags": ["Python", "BugFix"]
        })
        _, tags = analyzer_available.summarize_run("prompt", ["out"], "proj")
        assert all(t == t.lower() for t in tags)

    def test_calls_sdk_complete(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = '{"summary": "x", "tags": []}'
        analyzer_available.summarize_run("prompt", ["out"], "proj")
        available_sdk.complete.assert_called_once()


# ---------------------------------------------------------------------------
# update_profile — SDK unavailable
# ---------------------------------------------------------------------------

class TestUpdateProfileUnavailable:
    def test_returns_empty_string(self, analyzer_unavailable):
        result = analyzer_unavailable.update_profile(
            "fix bug", ["output"], "proj", ["proj: fix bug"], "current profile"
        )
        assert result == ""


# ---------------------------------------------------------------------------
# update_profile — SDK available
# ---------------------------------------------------------------------------

class TestUpdateProfileAvailable:
    def test_returns_updated_profile(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "## Developer Identity\nSenior Python engineer."
        result = analyzer_available.update_profile(
            "fix bug", [], "proj", ["proj: fix bug"], ""
        )
        assert "Senior Python engineer." in result

    def test_strips_whitespace(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "  profile content  "
        result = analyzer_available.update_profile("p", [], "proj", [], "")
        assert result == "profile content"

    def test_sdk_error_returns_empty(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "[SDK Error] network error"
        result = analyzer_available.update_profile("p", [], "proj", [], "")
        assert result == ""

    def test_truncates_long_corpus(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "updated profile"
        many_prompts = [f"proj: prompt {i}" for i in range(100)]
        # Should not raise even with 100+ prompts
        result = analyzer_available.update_profile("p", [], "proj", many_prompts, "")
        assert result == "updated profile"


# ---------------------------------------------------------------------------
# update_project_profile — SDK available
# ---------------------------------------------------------------------------

class TestUpdateProjectProfile:
    def test_returns_project_profile(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "## Summary\nA FastAPI service."
        result = analyzer_available.update_project_profile(
            "add endpoint", [], "myproject", ["add endpoint"], ""
        )
        assert "FastAPI service" in result

    def test_sdk_error_returns_empty(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "[SDK Error] timeout"
        result = analyzer_available.update_project_profile("p", [], "proj", [], "")
        assert result == ""

    def test_unavailable_returns_empty(self, analyzer_unavailable):
        result = analyzer_unavailable.update_project_profile("p", [], "proj", [], "")
        assert result == ""


# ---------------------------------------------------------------------------
# predict_prompts — SDK unavailable
# ---------------------------------------------------------------------------

class TestPredictPromptsUnavailable:
    def test_returns_empty_list(self, analyzer_unavailable):
        result = analyzer_unavailable.predict_prompts(
            "profile", "proj", "last prompt", [], [], n=4
        )
        assert result == []


# ---------------------------------------------------------------------------
# predict_prompts — SDK available
# ---------------------------------------------------------------------------

class TestPredictPromptsAvailable:
    def test_returns_list_of_strings(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps([
            "Fix the tests", "Add type annotations", "Commit changes"
        ])
        result = analyzer_available.predict_prompts(
            "profile", "proj", "last prompt", [], [], n=3
        )
        assert isinstance(result, list)
        assert all(isinstance(s, str) for s in result)

    def test_returns_at_most_n(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(
            [f"suggestion {i}" for i in range(10)]
        )
        result = analyzer_available.predict_prompts(
            "profile", "proj", "p", [], [], n=3
        )
        assert len(result) <= 3

    def test_invalid_json_returns_empty(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "not json"
        result = analyzer_available.predict_prompts(
            "profile", "proj", "p", [], [], n=4
        )
        assert result == []

    def test_sdk_error_returns_empty(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = "[SDK Error] rate limit"
        result = analyzer_available.predict_prompts(
            "profile", "proj", "p", [], [], n=4
        )
        assert result == []

    def test_filters_non_string_items(self, analyzer_available, available_sdk):
        available_sdk.complete.return_value = json.dumps(
            ["valid string", 42, None, "another string"]
        )
        result = analyzer_available.predict_prompts(
            "profile", "proj", "p", [], [], n=4
        )
        assert all(isinstance(s, str) for s in result)
