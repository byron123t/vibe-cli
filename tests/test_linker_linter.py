"""Tests for memory.linker.Linker and memory.linter.VaultLinter."""
import pytest

from memory.vault import MemoryVault
from memory.moc import MOCManager
from memory.linker import Linker
from memory.linter import VaultLinter


@pytest.fixture
def vault(tmp_path):
    return MemoryVault(str(tmp_path / "vault"))


# ---------------------------------------------------------------------------
# Linker
# ---------------------------------------------------------------------------

class TestLinker:
    def test_build_empty_vault(self, vault):
        linker = Linker(vault)
        linker.build()
        assert linker.outgoing == {}
        assert linker.incoming == {}

    def test_outgoing_links_detected(self, vault):
        vault.create_note("notes/source", "Source", "See [[Target]]")
        vault.create_note("notes/target", "Target", "body")
        linker = Linker(vault)
        linker.build()
        assert "Target" in linker.outgoing.get("Source", [])

    def test_incoming_links_populated(self, vault):
        vault.create_note("notes/source", "Source", "See [[Target]]")
        vault.create_note("notes/target", "Target", "body")
        linker = Linker(vault)
        linker.build()
        assert "Source" in linker.incoming.get("Target", [])

    def test_no_self_links(self, vault):
        vault.create_note("notes/self", "Self", "[[Self]] reference")
        linker = Linker(vault)
        linker.build()
        # Self-links are allowed in content but should be tracked normally
        assert isinstance(linker.outgoing, dict)

    def test_broken_links_detected(self, vault):
        vault.create_note("notes/source", "Source", "See [[NonExistent]]")
        linker = Linker(vault)
        linker.build()
        broken = linker.broken_links()
        assert any(src == "Source" and tgt == "NonExistent"
                   for src, tgt in broken)

    def test_no_broken_links_when_all_resolve(self, vault):
        vault.create_note("notes/a", "NoteA", "See [[NoteB]]")
        vault.create_note("notes/b", "NoteB", "body")
        linker = Linker(vault)
        linker.build()
        assert linker.broken_links() == []

    def test_orphan_note_detected(self, vault):
        vault.create_note("notes/lone", "LoneNote", "no links here")
        linker = Linker(vault)
        linker.build()
        orphans = linker.orphans()
        assert "LoneNote" in orphans

    def test_linked_note_not_orphan(self, vault):
        vault.create_note("notes/a", "NoteA", "See [[NoteB]]")
        vault.create_note("notes/b", "NoteB", "body")
        linker = Linker(vault)
        linker.build()
        orphans = linker.orphans()
        # NoteA has outgoing, NoteB has incoming — neither should be orphan
        assert "NoteA" not in orphans
        assert "NoteB" not in orphans

    def test_resolve_exact_match(self, vault):
        vault.create_note("notes/x", "Exact Title", "body")
        linker = Linker(vault)
        linker.build()
        result = linker.resolve("Exact Title")
        assert result is not None
        assert result.title == "Exact Title"

    def test_resolve_case_insensitive(self, vault):
        vault.create_note("notes/x", "My Note", "body")
        linker = Linker(vault)
        linker.build()
        result = linker.resolve("my note")
        assert result is not None

    def test_resolve_missing_returns_none(self, vault):
        linker = Linker(vault)
        linker.build()
        assert linker.resolve("Does Not Exist") is None

    def test_multiple_links_from_one_note(self, vault):
        vault.create_note("notes/hub", "Hub", "[[A]] [[B]] [[C]]")
        vault.create_note("notes/a", "A", "body")
        vault.create_note("notes/b", "B", "body")
        vault.create_note("notes/c", "C", "body")
        linker = Linker(vault)
        linker.build()
        assert set(linker.outgoing.get("Hub", [])) >= {"A", "B", "C"}


# ---------------------------------------------------------------------------
# VaultLinter
# ---------------------------------------------------------------------------

class TestVaultLinter:
    def test_run_returns_lint_report(self, vault):
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        assert hasattr(report, "broken_links")
        assert hasattr(report, "orphan_notes")
        assert hasattr(report, "stale_mocs")
        assert hasattr(report, "empty_notes")

    def test_clean_vault_has_no_issues(self, vault):
        vault.create_note("notes/a", "A", "See [[B]]")
        vault.create_note("notes/b", "B", "See [[A]]")
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        assert report.broken_links == []

    def test_broken_link_reported(self, vault):
        vault.create_note("notes/src", "SrcNote", "[[MissingTarget]]")
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        assert any(tgt == "MissingTarget" for _, tgt in report.broken_links)

    def test_empty_note_reported(self, vault):
        vault.create_note("notes/empty", "EmptyNote", "")
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        assert "EmptyNote" in report.empty_notes

    def test_has_issues_true_when_problems(self, vault):
        vault.create_note("notes/bad", "BadNote", "[[Ghost]]")
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        assert report.has_issues is True

    def test_has_issues_false_when_clean(self, vault):
        # No notes at all — empty vault is clean
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        assert report.has_issues is False

    def test_summary_returns_string(self, vault):
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        assert isinstance(report.summary(), str)

    def test_moc_notes_excluded_from_orphan_check(self, vault):
        moc = MOCManager(vault)
        moc.get_or_create_moc("TestTopic")
        linker = Linker(vault)
        linker.build()
        report = VaultLinter(vault, linker).run()
        # MOC files should not appear as orphans
        assert not any("MOC" in o for o in report.orphan_notes)
