"""MOCManager — create and maintain Maps of Content in the vault."""
from __future__ import annotations

import os
from datetime import datetime

from memory.note import Note
from memory.vault import MemoryVault


class MOCManager:
    """
    Manages Maps of Content (MOC) — index notes that collect links to
    related notes by topic/tag. MOCs live in vault/_MOCs/.
    Auto-maintained: rebuilt whenever notes are added/modified.
    """

    def __init__(self, vault: MemoryVault) -> None:
        self._vault = vault

    def get_or_create_moc(self, topic: str) -> Note:
        existing = self._vault.get_moc(topic)
        if existing:
            return existing
        path = self._vault.moc_path(topic)
        return Note.create_new(
            path=path,
            title=f"MOC - {topic}",
            body=f"# {topic} MOC\n\n_This Map of Content is auto-maintained by VibeCLI._\n",
            tags=["moc", topic.lower().replace(" ", "_")],
            note_type="moc",
        )

    def update_moc(self, topic: str) -> None:
        """Rebuild a MOC by scanning the vault for notes tagged with topic."""
        notes = self._vault.get_by_tag(topic)
        moc   = self.get_or_create_moc(topic)

        links_section = "\n".join(
            f"- [[{n.title}]]" for n in sorted(notes, key=lambda n: n.modified_at, reverse=True)
        )
        body = (
            f"# {topic} MOC\n\n"
            f"_Auto-maintained by VibeCLI. Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC_\n\n"
            f"## Notes\n\n{links_section or '_No notes yet._'}\n"
        )

        # Rewrite body preserving frontmatter
        fm_lines = [f"---"]
        for k, v in moc.frontmatter.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}: [{', '.join(str(i) for i in v)}]")
            else:
                fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        moc.content = "\n".join(fm_lines) + "\n\n" + body
        moc.save()

    def update_index_moc(self) -> None:
        """Rebuild the master index MOC linking to all other MOCs."""
        mocs  = self._vault.list_mocs()
        notes = self._vault.all_notes()

        moc_links = "\n".join(
            f"- [[{m.title}]]" for m in sorted(mocs, key=lambda m: m.title)
            if m.title != "MOC - Index"
        )
        stats = (
            f"- Total notes: {len(notes)}\n"
            f"- Total MOCs: {len(mocs)}\n"
            f"- Last updated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC\n"
        )

        body = (
            "# VibeCLI Memory Index\n\n"
            "_Master index of all Maps of Content. Auto-maintained by VibeCLI._\n\n"
            "## Vault Statistics\n\n" + stats + "\n"
            "## Maps of Content\n\n" + (moc_links or "_No MOCs yet._") + "\n"
        )

        index_path = self._vault.moc_path("Index")
        if os.path.isfile(index_path):
            index = Note.from_file(index_path)
            # Replace body
            fm_end = index.content.find("---", 3)
            if fm_end >= 0:
                index.content = index.content[:fm_end + 3] + "\n\n" + body
            else:
                index.content = body
            index.save()
        else:
            Note.create_new(
                path=index_path,
                title="MOC - Index",
                body=body,
                tags=["moc", "index"],
                note_type="moc",
            )

    def update_projects_moc(self, projects: list[str]) -> None:
        moc = self.get_or_create_moc("Projects")
        rows = "\n".join(f"| [[{p}]] | — | — |" for p in sorted(projects))
        body = (
            "# Projects MOC\n\n"
            "_All tracked projects._\n\n"
            "| Project | Last Active | Notes |\n"
            "|---------|-------------|-------|\n"
            + (rows or "| _none_ | — | — |") + "\n"
        )
        fm_lines = ["---"]
        for k, v in moc.frontmatter.items():
            if isinstance(v, list):
                fm_lines.append(f"{k}: [{', '.join(str(i) for i in v)}]")
            else:
                fm_lines.append(f"{k}: {v}")
        fm_lines.append("---")
        moc.content = "\n".join(fm_lines) + "\n\n" + body
        moc.save()

    def add_note_to_moc(self, note: Note, topic: str) -> None:
        """Append a note link to a MOC."""
        moc = self.get_or_create_moc(topic)
        if f"[[{note.title}]]" not in moc.content:
            moc.content += f"\n- [[{note.title}]]"
            moc.save()
