"""Tests for graph.knowledge_graph.KnowledgeGraph."""
import pytest

from memory.vault import MemoryVault
from graph.knowledge_graph import KnowledgeGraph


@pytest.fixture
def vault(tmp_path):
    return MemoryVault(str(tmp_path / "vault"))


@pytest.fixture
def kg(vault):
    return KnowledgeGraph(vault)


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

class TestBuild:
    def test_empty_vault(self, kg):
        kg.build()
        assert kg.node_count() == 0
        assert kg.edge_count() == 0

    def test_single_note_no_links(self, vault, kg):
        vault.create_note("notes/a", "NoteA", "plain text")
        kg.build()
        assert kg.node_count() == 1
        assert kg.edge_count() == 0

    def test_two_linked_notes(self, vault, kg):
        vault.create_note("notes/a", "NoteA", "See [[NoteB]]")
        vault.create_note("notes/b", "NoteB", "body")
        kg.build()
        assert kg.node_count() == 2
        assert kg.edge_count() >= 1

    def test_bidirectional_links(self, vault, kg):
        vault.create_note("notes/a", "NoteA", "[[NoteB]]")
        vault.create_note("notes/b", "NoteB", "[[NoteA]]")
        kg.build()
        assert kg.edge_count() == 2

    def test_graph_property(self, vault, kg):
        vault.create_note("notes/a", "A", "body")
        kg.build()
        g = kg.graph
        assert g is not None
        assert "A" in g.nodes

    def test_rebuild_replaces_graph(self, vault, kg):
        vault.create_note("notes/a", "A", "body")
        kg.build()
        assert kg.node_count() == 1
        vault.create_note("notes/b", "B", "body")
        kg.build()
        assert kg.node_count() == 2


# ---------------------------------------------------------------------------
# get_neighbors
# ---------------------------------------------------------------------------

class TestGetNeighbors:
    def test_returns_subgraph(self, vault, kg):
        vault.create_note("notes/a", "Hub", "[[Spoke1]] [[Spoke2]]")
        vault.create_note("notes/b", "Spoke1", "body")
        vault.create_note("notes/c", "Spoke2", "body")
        kg.build()
        sub = kg.get_neighbors("Hub", depth=1)
        assert "Hub" in sub.nodes

    def test_unknown_node_returns_empty(self, vault, kg):
        kg.build()
        sub = kg.get_neighbors("DoesNotExist")
        assert len(sub.nodes) == 0

    def test_depth_1_includes_direct_neighbors(self, vault, kg):
        vault.create_note("notes/a", "A", "[[B]]")
        vault.create_note("notes/b", "B", "[[C]]")
        vault.create_note("notes/c", "C", "body")
        kg.build()
        sub = kg.get_neighbors("A", depth=1)
        assert "B" in sub.nodes
        assert "C" not in sub.nodes  # depth=1 stops at B


# ---------------------------------------------------------------------------
# get_central_nodes
# ---------------------------------------------------------------------------

class TestGetCentralNodes:
    def test_returns_list_of_tuples(self, vault, kg):
        vault.create_note("notes/a", "A", "[[B]]")
        vault.create_note("notes/b", "B", "[[A]]")
        kg.build()
        result = kg.get_central_nodes(top_n=5)
        assert isinstance(result, list)
        if result:
            assert isinstance(result[0], tuple)
            assert len(result[0]) == 2

    def test_empty_graph_returns_empty(self, kg):
        kg.build()
        assert kg.get_central_nodes() == []

    def test_limits_to_top_n(self, vault, kg):
        for i in range(6):
            vault.create_note(f"notes/{i}", f"Note{i}", f"[[Note{(i+1)%6}]]")
        kg.build()
        result = kg.get_central_nodes(top_n=3)
        assert len(result) <= 3

    def test_hub_node_ranks_higher(self, vault, kg):
        # Hub is linked to by many nodes → should rank high
        vault.create_note("notes/hub", "Hub", "body")
        for i in range(5):
            vault.create_note(f"notes/spoke{i}", f"Spoke{i}", "[[Hub]]")
        kg.build()
        result = kg.get_central_nodes(top_n=6)
        top_title = result[0][0] if result else None
        assert top_title == "Hub"


# ---------------------------------------------------------------------------
# shortest_path
# ---------------------------------------------------------------------------

class TestShortestPath:
    def test_direct_link(self, vault, kg):
        vault.create_note("notes/a", "A", "[[B]]")
        vault.create_note("notes/b", "B", "body")
        kg.build()
        path = kg.shortest_path("A", "B")
        assert path == ["A", "B"]

    def test_no_path_returns_empty(self, vault, kg):
        vault.create_note("notes/a", "A", "body")
        vault.create_note("notes/b", "B", "body")
        kg.build()
        path = kg.shortest_path("A", "B")
        assert path == []

    def test_same_node(self, vault, kg):
        vault.create_note("notes/a", "A", "body")
        kg.build()
        path = kg.shortest_path("A", "A")
        assert path == ["A"]

    def test_two_hop_path(self, vault, kg):
        vault.create_note("notes/a", "A", "[[B]]")
        vault.create_note("notes/b", "B", "[[C]]")
        vault.create_note("notes/c", "C", "body")
        kg.build()
        path = kg.shortest_path("A", "C")
        assert path == ["A", "B", "C"]


# ---------------------------------------------------------------------------
# node_count / edge_count
# ---------------------------------------------------------------------------

class TestCounts:
    def test_node_count_zero_empty(self, kg):
        kg.build()
        assert kg.node_count() == 0

    def test_edge_count_zero_empty(self, kg):
        kg.build()
        assert kg.edge_count() == 0

    def test_node_count_after_notes(self, vault, kg):
        for i in range(4):
            vault.create_note(f"notes/{i}", f"N{i}", "body")
        kg.build()
        assert kg.node_count() == 4

    def test_edge_count_after_links(self, vault, kg):
        vault.create_note("notes/a", "A", "[[B]] [[C]]")
        vault.create_note("notes/b", "B", "body")
        vault.create_note("notes/c", "C", "body")
        kg.build()
        assert kg.edge_count() == 2
