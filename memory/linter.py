"""VaultLinter — detect and auto-clean vault issues.

Detection (run()):
  - Broken wikilinks
  - Orphan notes (no incoming or outgoing links)
  - Stale MOCs (linking deleted notes)
  - Empty notes (body < 20 chars)

Auto-clean (auto_clean(compactor)):
  - Deletes empty notes from disk
  - Merges temporally-close, related run log notes via Compactor
  Returns (deleted_empty, compacted_notes) counts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from memory.linker import Linker
from memory.vault import MemoryVault

if TYPE_CHECKING:
    from memory.compactor import Compactor


@dataclass
class LintReport:
    broken_links:  list[tuple[str, str]] = field(default_factory=list)
    orphan_notes:  list[str]             = field(default_factory=list)
    stale_mocs:    list[str]             = field(default_factory=list)
    empty_notes:   list[str]             = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(
            self.broken_links or self.orphan_notes
            or self.stale_mocs or self.empty_notes
        )

    def summary(self) -> str:
        parts = []
        if self.broken_links:
            parts.append(f"{len(self.broken_links)} broken links")
        if self.orphan_notes:
            parts.append(f"{len(self.orphan_notes)} orphans")
        if self.stale_mocs:
            parts.append(f"{len(self.stale_mocs)} stale MOCs")
        if self.empty_notes:
            parts.append(f"{len(self.empty_notes)} empty notes")
        return ", ".join(parts) if parts else "Vault clean"


class VaultLinter:
    def __init__(self, vault: MemoryVault, linker: Linker) -> None:
        self._vault  = vault
        self._linker = linker

    # ------------------------------------------------------------------ detection

    def run(self) -> LintReport:
        self._linker.build()
        report = LintReport()

        report.broken_links = self._linker.broken_links()

        report.orphan_notes = [
            t for t in self._linker.orphans()
            if not t.startswith("MOC") and "profile" not in t.lower()
        ]

        for note in self._vault.all_notes():
            if len(note.body().strip()) < 20:
                report.empty_notes.append(note.title)

        for moc in self._vault.list_mocs():
            for link in moc.outgoing_links:
                if self._linker.resolve(link) is None:
                    report.stale_mocs.append(moc.title)
                    break

        return report

    # ------------------------------------------------------------------ auto-clean

    def auto_clean(self, compactor: "Compactor") -> tuple[int, int]:
        """Delete empty notes and compact redundant run logs.

        Returns (deleted_empty, compacted_notes).
        """
        deleted_empty = self._delete_empty_notes()
        compacted     = self._compact(compactor)
        return deleted_empty, compacted

    def _delete_empty_notes(self) -> int:
        """Remove notes whose body is effectively blank (<20 chars)."""
        count = 0
        for note in self._vault.all_notes():
            # Never delete MOCs, profiles, lint reports, or session files
            skip_keywords = ("moc", "profile", "lint_report", "session", "projects.json")
            if any(kw in note.path.lower() for kw in skip_keywords):
                continue
            if len(note.body().strip()) < 20:
                try:
                    self._vault.delete_note(note)
                    count += 1
                except Exception:
                    pass
        return count

    def _compact(self, compactor: "Compactor") -> int:
        """Run the compactor across all projects."""
        try:
            return compactor.compact_all()
        except Exception:
            return 0
