"""PersonalizationGraph — weighted action transition graph for usage learning."""
from __future__ import annotations

import json
import math
import os
import time
from datetime import datetime

import networkx as nx

# NetworkX changed the edge-list key from "links" (<=3.1) to "edges" (>=3.2).
# These wrappers handle both versions transparently.
def _node_link_data(g: nx.DiGraph) -> dict:
    try:
        return nx.node_link_data(g, edges="links")   # nx >= 3.2
    except TypeError:
        return nx.node_link_data(g, link="links")    # nx 3.0 / 3.1


def _node_link_graph(data: dict) -> nx.DiGraph:
    edges_key = "links" if "links" in data else "edges"
    try:
        return nx.node_link_graph(data, directed=True, edges=edges_key)  # nx >= 3.2
    except TypeError:
        return nx.node_link_graph(data, directed=True, link=edges_key)   # nx 3.0 / 3.1


class PersonalizationGraph:
    """
    A weighted directed graph where:
      nodes = action_id strings
      edges = (from_action → to_action) with weight = transition frequency
      node attrs = {total_uses, last_used, project_counts: {project: count}}

    Persisted as JSON to vault/user/personalization_graph.json.
    """

    HALF_LIFE_DAYS = 7.0

    def __init__(self, persist_path: str) -> None:
        self._path = persist_path
        self._g: nx.DiGraph = nx.DiGraph()
        self.load()

    # ------------------------------------------------------------------ recording

    def record_use(self, action_id: str, project: str) -> None:
        """Record that an action was used in a project."""
        if not self._g.has_node(action_id):
            self._g.add_node(action_id, total_uses=0, last_used=0.0, project_counts={})
        node = self._g.nodes[action_id]
        node["total_uses"] += 1
        node["last_used"] = time.time()
        counts = node.setdefault("project_counts", {})
        counts[project] = counts.get(project, 0) + 1

    def record_transition(self, from_action: str, to_action: str,
                           project: str) -> None:
        """Record that to_action was performed after from_action."""
        self.record_use(to_action, project)
        if from_action:
            if not self._g.has_edge(from_action, to_action):
                self._g.add_edge(from_action, to_action, weight=0.0,
                                 project_weights={})
            edge = self._g.edges[from_action, to_action]
            edge["weight"] = edge.get("weight", 0.0) + 1.0
            pw = edge.setdefault("project_weights", {})
            pw[project] = pw.get(project, 0.0) + 1.0

    # ------------------------------------------------------------------ prediction

    def get_likely_next(self, current_action: str, project: str,
                        top_n: int = 5) -> list[tuple[str, float]]:
        """
        Returns [(action_id, score)] sorted descending.
        Score = transition_weight × project_affinity × recency.
        """
        if not self._g.has_node(current_action):
            return self._top_global(project, top_n)

        candidates: list[tuple[str, float]] = []
        for _, to_node, data in self._g.out_edges(current_action, data=True):
            base_weight    = data.get("weight", 0.0)
            proj_weight    = data.get("project_weights", {}).get(project, 0.0)
            proj_fraction  = proj_weight / (base_weight + 1e-6)
            node_data      = self._g.nodes.get(to_node, {})
            recency_score  = self._recency_score(node_data.get("last_used", 0.0))
            score = base_weight * (0.5 + 0.5 * proj_fraction) * recency_score
            candidates.append((to_node, score))

        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[:top_n]

    def _top_global(self, project: str, top_n: int) -> list[tuple[str, float]]:
        """Fallback: return top globally used actions in this project."""
        scores = []
        for node, data in self._g.nodes(data=True):
            proj_uses = data.get("project_counts", {}).get(project, 0)
            recency   = self._recency_score(data.get("last_used", 0.0))
            scores.append((node, proj_uses * recency))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_n]

    def _recency_score(self, last_used_ts: float) -> float:
        if last_used_ts == 0:
            return 0.1
        days_ago = (time.time() - last_used_ts) / 86400.0
        return math.exp(-math.log(2) * days_ago / self.HALF_LIFE_DAYS)

    # ------------------------------------------------------------------ persistence

    def save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        data = _node_link_data(self._g)
        with open(self._path, "w") as f:
            json.dump(data, f, indent=2)

    def load(self) -> None:
        if os.path.isfile(self._path):
            with open(self._path) as f:
                data = json.load(f)
            self._g = _node_link_graph(data)
        else:
            self._g = nx.DiGraph()

    # ------------------------------------------------------------------ stats

    def action_stats(self) -> dict[str, dict]:
        return dict(self._g.nodes(data=True))

    def top_actions(self, top_n: int = 10) -> list[tuple[str, int]]:
        nodes = [(n, d.get("total_uses", 0)) for n, d in self._g.nodes(data=True)]
        return sorted(nodes, key=lambda x: x[1], reverse=True)[:top_n]
