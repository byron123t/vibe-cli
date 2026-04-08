"""Tests for graph.personalization_graph.PersonalizationGraph."""
import os
import time
import pytest

from graph.personalization_graph import PersonalizationGraph


@pytest.fixture
def graph(tmp_path):
    return PersonalizationGraph(str(tmp_path / "graph.json"))


# ---------------------------------------------------------------------------
# record_use
# ---------------------------------------------------------------------------

class TestRecordUse:
    def test_records_without_error(self, graph):
        graph.record_use("prompt:fix bug", "myproject")

    def test_action_appears_in_stats(self, graph):
        graph.record_use("prompt:fix bug", "myproject")
        stats = graph.action_stats()
        assert "prompt:fix bug" in stats

    def test_use_count_increments(self, graph):
        graph.record_use("prompt:fix bug", "proj")
        graph.record_use("prompt:fix bug", "proj")
        stats = graph.action_stats()
        assert stats["prompt:fix bug"]["total_uses"] == 2

    def test_project_count_tracked(self, graph):
        graph.record_use("prompt:fix", "projA")
        graph.record_use("prompt:fix", "projB")
        stats = graph.action_stats()
        counts = stats["prompt:fix"]["project_counts"]
        assert counts.get("projA", 0) == 1
        assert counts.get("projB", 0) == 1

    def test_multiple_actions(self, graph):
        graph.record_use("prompt:a", "proj")
        graph.record_use("prompt:b", "proj")
        stats = graph.action_stats()
        assert "prompt:a" in stats
        assert "prompt:b" in stats


# ---------------------------------------------------------------------------
# record_transition
# ---------------------------------------------------------------------------

class TestRecordTransition:
    def test_records_without_error(self, graph):
        graph.record_transition("prompt:a", "prompt:b", "proj")

    def test_transition_influences_prediction(self, graph):
        # After recording a → b many times, b should rank high after a
        for _ in range(5):
            graph.record_use("prompt:a", "proj")
            graph.record_use("prompt:b", "proj")
            graph.record_transition("prompt:a", "prompt:b", "proj")
        predictions = graph.get_likely_next("prompt:a", "proj", top_n=5)
        action_ids = [p[0] for p in predictions]
        assert "prompt:b" in action_ids

    def test_transition_returns_list_of_tuples(self, graph):
        graph.record_use("prompt:a", "proj")
        graph.record_use("prompt:b", "proj")
        graph.record_transition("prompt:a", "prompt:b", "proj")
        result = graph.get_likely_next("prompt:a", "proj")
        assert isinstance(result, list)
        if result:
            assert isinstance(result[0], tuple)
            assert len(result[0]) == 2


# ---------------------------------------------------------------------------
# get_likely_next
# ---------------------------------------------------------------------------

class TestGetLikelyNext:
    def test_returns_list(self, graph):
        result = graph.get_likely_next("prompt:none", "proj")
        assert isinstance(result, list)

    def test_empty_graph_returns_global_top(self, graph):
        graph.record_use("prompt:popular", "proj")
        graph.record_use("prompt:popular", "proj")
        result = graph.get_likely_next("", "proj", top_n=5)
        assert any(a == "prompt:popular" for a, _ in result)

    def test_scores_are_positive(self, graph):
        graph.record_use("prompt:x", "proj")
        result = graph.get_likely_next("", "proj", top_n=5)
        assert all(score >= 0 for _, score in result)

    def test_top_n_limits_results(self, graph):
        for i in range(10):
            graph.record_use(f"prompt:action{i}", "proj")
        result = graph.get_likely_next("", "proj", top_n=3)
        assert len(result) <= 3

    def test_project_affinity_weights(self, graph):
        # proj-specific action used more on projA
        for _ in range(10):
            graph.record_use("prompt:proj-specific", "projA")
        graph.record_use("prompt:proj-specific", "projB")
        result_a = graph.get_likely_next("", "projA", top_n=5)
        result_b = graph.get_likely_next("", "projB", top_n=5)
        score_a = next((s for a, s in result_a if a == "prompt:proj-specific"), 0)
        score_b = next((s for a, s in result_b if a == "prompt:proj-specific"), 0)
        assert score_a > score_b


# ---------------------------------------------------------------------------
# top_actions
# ---------------------------------------------------------------------------

class TestTopActions:
    def test_returns_sorted_by_use_count(self, graph):
        graph.record_use("prompt:rare", "proj")
        for _ in range(5):
            graph.record_use("prompt:common", "proj")
        top = graph.top_actions(top_n=2)
        assert top[0][0] == "prompt:common"

    def test_returns_at_most_top_n(self, graph):
        for i in range(10):
            graph.record_use(f"prompt:a{i}", "proj")
        top = graph.top_actions(top_n=4)
        assert len(top) <= 4

    def test_empty_graph_returns_empty(self, graph):
        assert graph.top_actions() == []


# ---------------------------------------------------------------------------
# save / load
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_creates_file(self, tmp_path):
        path = str(tmp_path / "graph.json")
        g = PersonalizationGraph(path)
        g.record_use("prompt:x", "proj")
        g.save()
        assert os.path.isfile(path)

    def test_load_restores_state(self, tmp_path):
        path = str(tmp_path / "graph.json")
        g1 = PersonalizationGraph(path)
        g1.record_use("prompt:persistent", "proj")
        g1.save()

        g2 = PersonalizationGraph(path)
        stats = g2.action_stats()
        assert "prompt:persistent" in stats

    def test_missing_file_loads_empty(self, tmp_path):
        path = str(tmp_path / "nonexistent.json")
        g = PersonalizationGraph(path)
        assert g.action_stats() == {}

    def test_save_load_roundtrip_preserves_use_count(self, tmp_path):
        path = str(tmp_path / "graph.json")
        g1 = PersonalizationGraph(path)
        for _ in range(7):
            g1.record_use("prompt:count_me", "proj")
        g1.save()

        g2 = PersonalizationGraph(path)
        stats = g2.action_stats()
        assert stats["prompt:count_me"]["total_uses"] == 7
