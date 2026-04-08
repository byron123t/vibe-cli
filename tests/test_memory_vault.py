"""Tests for memory.vault.MemoryVault and memory.moc.MOCManager."""
import os
import pytest

from memory.vault import MemoryVault
from memory.moc import MOCManager
from memory.note import Note


@pytest.fixture
def vault(tmp_path):
    return MemoryVault(str(tmp_path / "vault"))


@pytest.fixture
def moc(vault):
    return MOCManager(vault)


# ---------------------------------------------------------------------------
# MemoryVault — structure
# ---------------------------------------------------------------------------

class TestVaultInit:
    def test_creates_root(self, tmp_path):
        v = MemoryVault(str(tmp_path / "myvault"))
        assert os.path.isdir(v.root)

    def test_creates_mocs_dir(self, vault):
        assert os.path.isdir(os.path.join(vault.root, "_MOCs"))

    def test_creates_projects_dir(self, vault):
        assert os.path.isdir(os.path.join(vault.root, "projects"))

    def test_creates_user_dir(self, vault):
        assert os.path.isdir(os.path.join(vault.root, "user"))


# ---------------------------------------------------------------------------
# MemoryVault — CRUD
# ---------------------------------------------------------------------------

class TestVaultCRUD:
    def test_create_and_get_note(self, vault):
        note = vault.create_note("notes/test", "Test Note", "hello world")
        fetched = vault.get_note("notes/test")
        assert fetched is not None
        assert fetched.title == "Test Note"

    def test_get_note_missing_returns_none(self, vault):
        assert vault.get_note("does/not/exist") is None

    def test_create_note_saves_to_disk(self, vault):
        note = vault.create_note("notes/disk_test", "Disk Note", "body")
        assert os.path.isfile(note.path)

    def test_create_note_appends_md_extension(self, vault):
        note = vault.create_note("notes/noext", "No Ext", "body")
        assert note.path.endswith(".md")

    def test_save_note_persists(self, vault):
        note = vault.create_note("notes/mutable", "Mutable", "original body")
        note.content = note.content.replace("original body", "updated body")
        vault.save_note(note)
        fetched = vault.get_note("notes/mutable")
        assert "updated body" in fetched.content

    def test_all_notes_includes_created(self, vault):
        vault.create_note("notes/a", "Note A", "body a")
        vault.create_note("notes/b", "Note B", "body b")
        titles = {n.title for n in vault.all_notes()}
        assert "Note A" in titles
        assert "Note B" in titles

    def test_all_notes_empty_vault(self, vault):
        notes = vault.all_notes()
        assert notes == []

    def test_search_finds_content(self, vault):
        vault.create_note("notes/searchme", "Searchable", "unique_keyword_xyz")
        results = vault.search("unique_keyword_xyz")
        assert len(results) == 1
        assert results[0].title == "Searchable"

    def test_search_case_insensitive(self, vault):
        vault.create_note("notes/case", "Case Note", "MixedCaseContent")
        results = vault.search("mixedcasecontent")
        assert len(results) == 1

    def test_search_returns_empty_on_no_match(self, vault):
        vault.create_note("notes/nothing", "Nothing", "some content")
        results = vault.search("ZZZNOMATCH")
        assert results == []

    def test_get_by_tag(self, vault):
        vault.create_note("notes/tagged", "Tagged", "body", tags=["mytag"])
        vault.create_note("notes/untagged", "Untagged", "body")
        results = vault.get_by_tag("mytag")
        titles = {n.title for n in results}
        assert "Tagged" in titles
        assert "Untagged" not in titles

    def test_get_by_title(self, vault):
        vault.create_note("notes/titled", "Unique Title XYZ", "body")
        result = vault.get_by_title("Unique Title XYZ")
        assert result is not None
        assert result.title == "Unique Title XYZ"

    def test_get_by_title_missing(self, vault):
        assert vault.get_by_title("Nonexistent Title") is None


# ---------------------------------------------------------------------------
# MemoryVault — projects
# ---------------------------------------------------------------------------

class TestVaultProjects:
    def test_ensure_project_creates_dirs(self, vault):
        vault.ensure_project("myproject")
        assert os.path.isdir(os.path.join(vault.root, "projects", "myproject", "run_logs"))

    def test_list_projects_empty(self, vault):
        assert vault.list_projects() == []

    def test_list_projects_after_ensure(self, vault):
        vault.ensure_project("alpha")
        vault.ensure_project("beta")
        projects = vault.list_projects()
        assert "alpha" in projects
        assert "beta" in projects

    def test_get_project_notes_empty(self, vault):
        vault.ensure_project("empty_proj")
        notes = vault.get_project_notes("empty_proj")
        assert notes == []

    def test_get_project_notes_nonexistent(self, vault):
        notes = vault.get_project_notes("ghost_project")
        assert notes == []

    def test_get_project_notes_returns_only_project(self, vault):
        vault.create_note("projects/alpha/run_logs/r1", "Alpha Run", "body",
                          tags=["run_log", "alpha"])
        vault.create_note("projects/beta/run_logs/r1", "Beta Run", "body",
                          tags=["run_log", "beta"])
        alpha_notes = vault.get_project_notes("alpha")
        titles = {n.title for n in alpha_notes}
        assert "Alpha Run" in titles
        assert "Beta Run" not in titles

    def test_rel_path(self, vault):
        note = vault.create_note("projects/myproj/run_logs/test", "T", "b")
        rel = vault.rel_path(note)
        assert not rel.startswith("/")
        assert "myproj" in rel


# ---------------------------------------------------------------------------
# MOCManager
# ---------------------------------------------------------------------------

class TestMOCManager:
    def test_get_or_create_moc_new(self, vault, moc):
        m = moc.get_or_create_moc("TestTopic")
        assert "TestTopic" in m.title
        assert os.path.isfile(m.path)

    def test_get_or_create_moc_idempotent(self, vault, moc):
        m1 = moc.get_or_create_moc("Topic")
        m2 = moc.get_or_create_moc("Topic")
        assert m1.path == m2.path

    def test_add_note_to_moc_creates_link(self, vault, moc):
        note = vault.create_note("notes/n1", "My Note", "body")
        moc.add_note_to_moc(note, "TestMOC")
        moc_note = moc.get_or_create_moc("TestMOC")
        assert "[[My Note]]" in moc_note.content

    def test_add_note_to_moc_no_duplicate(self, vault, moc):
        note = vault.create_note("notes/n2", "Unique Note", "body")
        moc.add_note_to_moc(note, "TestMOC")
        moc.add_note_to_moc(note, "TestMOC")  # second time
        moc_note = moc.get_or_create_moc("TestMOC")
        assert moc_note.content.count("[[Unique Note]]") == 1

    def test_update_index_moc_creates_file(self, vault, moc):
        moc.update_index_moc()
        index_path = vault.moc_path("Index")
        assert os.path.isfile(index_path)

    def test_update_index_moc_lists_all_mocs(self, vault, moc):
        moc.get_or_create_moc("TopicA")
        moc.get_or_create_moc("TopicB")
        moc.update_index_moc()
        index = vault.get_moc("Index")
        assert "TopicA" in index.content
        assert "TopicB" in index.content

    def test_update_projects_moc(self, vault, moc):
        moc.update_projects_moc(["alpha", "beta", "gamma"])
        proj_moc = moc.get_or_create_moc("Projects")
        assert "alpha" in proj_moc.content
        assert "beta" in proj_moc.content
        assert "gamma" in proj_moc.content

    def test_list_mocs(self, vault):
        moc = MOCManager(vault)
        moc.get_or_create_moc("X")
        moc.get_or_create_moc("Y")
        mocs = vault.list_mocs()
        titles = {m.title for m in mocs}
        assert "MOC - X" in titles
        assert "MOC - Y" in titles
