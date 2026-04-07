"""KnowledgeGraph — networkx DiGraph built from vault wikilinks."""
from __future__ import annotations

import networkx as nx

from memory.vault import MemoryVault


class KnowledgeGraph:
    """
    Builds a directed knowledge graph from the vault's note links.
    Nodes = note titles; edges = [[wikilinks]].
    Node attributes include tags, type, centrality scores.
    """

    def __init__(self, vault: MemoryVault) -> None:
        self._vault = vault
        self._g: nx.DiGraph = nx.DiGraph()

    @property
    def graph(self) -> nx.DiGraph:
        return self._g

    def build(self) -> None:
        self._g = nx.DiGraph()
        notes = self._vault.all_notes()

        for note in notes:
            self._g.add_node(
                note.title,
                tags=note.tags,
                note_type=note.frontmatter.get("type", "note"),
                path=note.path,
                modified=note.modified_at,
            )

        for note in notes:
            for target in note.outgoing_links:
                if target != note.title:
                    if not self._g.has_node(target):
                        self._g.add_node(target, tags=[], note_type="unknown", path="", modified="")
                    self._g.add_edge(note.title, target)

        # Compute centrality for node sizing
        if len(self._g) > 0:
            try:
                pr = nx.pagerank(self._g, max_iter=50)
            except Exception:
                pr = {n: 1.0 for n in self._g.nodes}
            nx.set_node_attributes(self._g, pr, "pagerank")

    def rebuild_incremental(self, changed_titles: list[str]) -> None:
        """Re-add edges for changed notes without full rebuild."""
        for title in changed_titles:
            # Remove existing edges from this node
            if self._g.has_node(title):
                edges_to_remove = list(self._g.out_edges(title))
                self._g.remove_edges_from(edges_to_remove)
            # Find the note and re-add its edges
            note = self._vault.get_by_title(title)
            if note:
                for target in note.outgoing_links:
                    if not self._g.has_node(target):
                        self._g.add_node(target, tags=[], note_type="unknown")
                    self._g.add_edge(title, target)

    def get_neighbors(self, title: str, depth: int = 1) -> nx.DiGraph:
        """Return subgraph containing title and all nodes within `depth` hops."""
        if title not in self._g:
            return nx.DiGraph()
        ego = nx.ego_graph(self._g, title, radius=depth, undirected=True)
        return ego

    def get_central_nodes(self, top_n: int = 10) -> list[tuple[str, float]]:
        """Return top N nodes by PageRank."""
        pr = nx.get_node_attributes(self._g, "pagerank")
        if not pr:
            return []
        return sorted(pr.items(), key=lambda x: x[1], reverse=True)[:top_n]

    def shortest_path(self, source: str, target: str) -> list[str]:
        try:
            return nx.shortest_path(self._g, source, target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def node_count(self) -> int:
        return self._g.number_of_nodes()

    def edge_count(self) -> int:
        return self._g.number_of_edges()
