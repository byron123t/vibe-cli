"""Predictor — returns ranked next-action predictions for menu personalization."""
from __future__ import annotations

from graph.personalization_graph import PersonalizationGraph


class Predictor:
    """
    Wraps PersonalizationGraph to provide menu-friendly predictions.
    Used by MenuEngine to re-order children by predicted_weight.
    """

    def __init__(self, graph: PersonalizationGraph) -> None:
        self._graph = graph

    def score_action(self, action_id: str, project: str,
                     last_action: str = "") -> float:
        """Return a relevance score [0, ∞) for a single action."""
        predictions = self._graph.get_likely_next(last_action, project, top_n=20)
        pred_map = dict(predictions)
        return pred_map.get(action_id, 0.0)

    def rank_actions(self, action_ids: list[str], project: str,
                     last_action: str = "") -> list[tuple[str, float]]:
        """Return actions sorted by predicted score descending."""
        scored = [
            (aid, self.score_action(aid, project, last_action))
            for aid in action_ids
        ]
        return sorted(scored, key=lambda x: x[1], reverse=True)

    def update_menu_weights(self, nodes: list, project: str,
                             last_action: str = "") -> None:
        """In-place update of predicted_weight on MenuNode list."""
        for node in nodes:
            if node.action_id:
                node.predicted_weight = self.score_action(
                    node.action_id, project, last_action
                )

    def get_top_actions(self, project: str, last_action: str = "",
                        n: int = 4) -> list[str]:
        """Return top N predicted action_ids."""
        predictions = self._graph.get_likely_next(last_action, project, top_n=n)
        return [aid for aid, _ in predictions]
