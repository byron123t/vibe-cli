# graph/

Two NetworkX graphs: a knowledge graph built from vault wikilinks, and a personalization graph that tracks prompt usage patterns.

## Files

| File | Purpose |
|---|---|
| `knowledge_graph.py` | DiGraph of note titles connected by `[[wikilinks]]` |
| `personalization_graph.py` | Weighted action-transition graph for prompt frequency/recency ranking |

## Knowledge graph (`knowledge_graph.py`)

A `networkx.DiGraph` where:
- **Nodes** = note titles; attributes include `tags`, `note_type`, `path`, `modified`
- **Edges** = directed `[[wikilink]]` references between notes

Built by calling `KnowledgeGraph.build()` which iterates all vault notes via `MemoryVault.all_notes()`. Rebuild is triggered after each post-run hook cycle.

### Usage

```python
kg = KnowledgeGraph(vault)
kg.build()

# Nodes ranked by in-degree (most-linked notes)
central = sorted(kg.graph.nodes, key=lambda n: kg.graph.in_degree(n), reverse=True)

# Outgoing links from a note
kg.graph.successors("myapp MOC")
```

The `GraphPane` widget in the TUI reads `kg.graph` to render the navigable tree view.

## Personalization graph (`personalization_graph.py`)

A weighted `DiGraph` that models transitions between prompts and projects. Edge weight encodes frequency and recency; it decays over time so stale prompts drop in rank.

### Recording and ranking

```python
pg = PersonalizationGraph(path="vault/meta/personalization.json")
pg.record_use("prompt:fix the auth bug", project="myapp")

ranked = pg.rank(project="myapp", top_n=4)
# → ["prompt:fix the auth bug", "prompt:write tests for auth", ...]
```

### NetworkX compatibility

Uses version-agnostic wrappers for `node_link_data` / `node_link_graph` to handle the API change between NetworkX 3.0 (`link=`) and 3.2+ (`edges=`). Serialization is plain JSON so the graph persists across sessions.

### Persistence

The graph is saved to and loaded from `vault/meta/personalization.json` automatically on `record_use()` and at startup.
