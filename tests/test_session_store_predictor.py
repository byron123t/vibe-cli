"""Tests for core.session_store.SessionStore and personalization.predictor.Predictor."""
import os
import json
import pytest

from core.session_store import SessionStore, MAX_OUTPUT_LINES
from graph.personalization_graph import PersonalizationGraph
from personalization.predictor import Predictor


# ---------------------------------------------------------------------------
# SessionStore
# ---------------------------------------------------------------------------

import core.session_store as _ss_module


@pytest.fixture
def store(tmp_path, monkeypatch):
    """SessionStore uses a fixed SESSION_FILE — patch it to a tmp path."""
    path = str(tmp_path / "session.json")
    monkeypatch.setattr(_ss_module, "SESSION_FILE", path)
    return SessionStore(), path


class TestSessionStore:
    def test_load_returns_empty_when_missing(self, store):
        s, _ = store
        state = s.load()
        assert isinstance(state, dict)

    def test_save_creates_file(self, store):
        s, path = store
        s.save({"version": 1})
        assert os.path.isfile(path)

    def test_save_load_roundtrip(self, store):
        s, _ = store
        state = {"version": 1, "global": {"permission_mode": "safe"}}
        s.save(state)
        loaded = s.load()
        assert loaded["global"]["permission_mode"] == "safe"

    def test_save_overwrites(self, store):
        s, _ = store
        s.save({"version": 1, "data": "first"})
        s.save({"version": 1, "data": "second"})
        loaded = s.load()
        assert loaded["data"] == "second"

    def test_load_invalid_json_returns_empty(self, store, tmp_path, monkeypatch):
        _, path = store
        with open(path, "w") as f:
            f.write("not valid json {{{")
        s = SessionStore()
        result = s.load()
        assert isinstance(result, dict)

    def test_load_wrong_version_returns_empty(self, store):
        s, _ = store
        s.save({"version": 99, "data": "old"})
        result = s.load()
        assert result == {}

    def test_save_complex_nested_state(self, store):
        s, _ = store
        state = {
            "version": 1,
            "projects": {
                "/path/to/proj": {"agents": [{"prompt": "fix bug", "exit_code": 0}]}
            }
        }
        s.save(state)
        loaded = s.load()
        assert loaded["projects"]["/path/to/proj"]["agents"][0]["prompt"] == "fix bug"


class TestCapOutput:
    def test_keeps_last_n_lines(self):
        lines = [f"line {i}" for i in range(MAX_OUTPUT_LINES + 50)]
        result = SessionStore.cap_output(lines)
        result_lines = result.split("\n")
        assert len(result_lines) <= MAX_OUTPUT_LINES

    def test_empty_input(self):
        result = SessionStore.cap_output([])
        assert result == ""

    def test_short_input_unchanged(self):
        lines = ["a", "b", "c"]
        result = SessionStore.cap_output(lines)
        assert "a" in result
        assert "b" in result
        assert "c" in result

    def test_returns_string(self):
        result = SessionStore.cap_output(["hello", "world"])
        assert isinstance(result, str)

    def test_preserves_last_lines(self):
        lines = [f"line {i}" for i in range(MAX_OUTPUT_LINES + 10)]
        result = SessionStore.cap_output(lines)
        # Last line should be preserved
        assert f"line {MAX_OUTPUT_LINES + 9}" in result


# ---------------------------------------------------------------------------
# Predictor
# ---------------------------------------------------------------------------

@pytest.fixture
def graph(tmp_path):
    g = PersonalizationGraph(str(tmp_path / "graph.json"))
    return g


@pytest.fixture
def predictor(graph):
    return Predictor(graph)


class TestPredictor:
    def test_score_action_returns_float(self, predictor, graph):
        graph.record_use("prompt:fix", "proj")
        score = predictor.score_action("prompt:fix", "proj")
        assert isinstance(score, float)

    def test_score_zero_for_unknown_action(self, predictor):
        score = predictor.score_action("prompt:unknown_xyz", "proj")
        assert score == 0.0

    def test_score_positive_for_known_action(self, predictor, graph):
        graph.record_use("prompt:known", "proj")
        score = predictor.score_action("prompt:known", "proj")
        assert score > 0

    def test_rank_actions_sorted_descending(self, predictor, graph):
        graph.record_use("prompt:common", "proj")
        graph.record_use("prompt:common", "proj")
        graph.record_use("prompt:rare", "proj")
        ranked = predictor.rank_actions(["prompt:common", "prompt:rare"], "proj")
        assert ranked[0][0] == "prompt:common"

    def test_rank_actions_returns_list_of_tuples(self, predictor, graph):
        graph.record_use("prompt:a", "proj")
        result = predictor.rank_actions(["prompt:a"], "proj")
        assert isinstance(result, list)
        assert isinstance(result[0], tuple)

    def test_rank_actions_empty_input(self, predictor):
        result = predictor.rank_actions([], "proj")
        assert result == []

    def test_get_top_actions_returns_list(self, predictor, graph):
        for i in range(5):
            graph.record_use(f"prompt:action{i}", "proj")
        result = predictor.get_top_actions("proj", n=3)
        assert isinstance(result, list)

    def test_get_top_actions_limits_n(self, predictor, graph):
        for i in range(10):
            graph.record_use(f"prompt:a{i}", "proj")
        result = predictor.get_top_actions("proj", n=4)
        assert len(result) <= 4

    def test_get_top_actions_empty_graph(self, predictor):
        result = predictor.get_top_actions("proj", n=4)
        assert result == []

    def test_last_action_context_gives_nonzero_score(self, predictor, graph):
        # After recording a→b transitions, b should score > 0 given context a
        for _ in range(5):
            graph.record_use("prompt:a", "proj")
            graph.record_use("prompt:b", "proj")
            graph.record_transition("prompt:a", "prompt:b", "proj")
        score = predictor.score_action("prompt:b", "proj", last_action="prompt:a")
        assert score > 0

    def test_unknown_action_scores_zero_with_any_context(self, predictor, graph):
        graph.record_use("prompt:a", "proj")
        score = predictor.score_action("prompt:never_seen", "proj",
                                       last_action="prompt:a")
        assert score == 0.0
