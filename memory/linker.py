"""Linker — parse [[wikilinks]] and build a complete link index."""
from __future__ import annotations

from memory.note import Note
from memory.vault import MemoryVault


class Linker:
    """
    Builds a bidirectional link map from all notes in the vault.
    outgoing[note_title]  = [linked_title, ...]
    incoming[note_title]  = [source_title, ...]
    """

    def __init__(self, vault: MemoryVault) -> None:
        self._vault = vault
        self.outgoing: dict[str, list[str]] = {}
        self.incoming: dict[str, list[str]] = {}
        self._title_map: dict[str, Note] = {}   # lowercase title → Note

    def build(self) -> None:
        notes = self._vault.all_notes()
        self._title_map = {n.title.lower(): n for n in notes}
        self.outgoing = {n.title: n.outgoing_links for n in notes}
        self.incoming = {n.title: [] for n in notes}

        for source_title, targets in self.outgoing.items():
            for target in targets:
                if target in self.incoming:
                    self.incoming[target].append(source_title)

    def resolve(self, link_name: str) -> Note | None:
        return self._title_map.get(link_name.lower())

    def broken_links(self) -> list[tuple[str, str]]:
        """Returns list of (source_title, broken_target)."""
        broken = []
        for source, targets in self.outgoing.items():
            for t in targets:
                if t.lower() not in self._title_map:
                    broken.append((source, t))
        return broken

    def orphans(self) -> list[str]:
        """Notes with no incoming and no outgoing links."""
        return [
            title for title in self.outgoing
            if not self.outgoing[title] and not self.incoming.get(title)
        ]
