"""VaultLinter — detect broken links, orphans, stale MOCs, empty notes."""
from __future__ import annotations

from dataclasses import dataclass, field

from memory.linker import Linker
from memory.vault import MemoryVault


@dataclass
class LintReport:
    broken_links: list[tuple[str, str]] = field(default_factory=list)
    orphan_notes: list[str] = field(default_factory=list)
    stale_mocs: list[str] = field(default_factory=list)
    empty_notes: list[str] = field(default_factory=list)

    @property
    def has_issues(self) -> bool:
        return bool(self.broken_links or self.orphan_notes
                    or self.stale_mocs or self.empty_notes)

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
        return ", ".join(parts) if parts else "Vault is clean"


class VaultLinter:
    def __init__(self, vault: MemoryVault, linker: Linker) -> None:
        self._vault  = vault
        self._linker = linker

    def run(self) -> LintReport:
        self._linker.build()
        report = LintReport()

        # Broken links
        report.broken_links = self._linker.broken_links()

        # Orphan notes (skip MOCs and user profile)
        report.orphan_notes = [
            t for t in self._linker.orphans()
            if not t.startswith("MOC") and "profile" not in t.lower()
        ]

        # Empty notes
        for note in self._vault.all_notes():
            body = note.body().strip()
            if len(body) < 20:
                report.empty_notes.append(note.title)

        # Stale MOCs: linked note titles that no longer exist
        for moc in self._vault.list_mocs():
            for link in moc.outgoing_links:
                if self._linker.resolve(link) is None:
                    report.stale_mocs.append(moc.title)
                    break

        return report
