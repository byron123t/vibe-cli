"""Tests for memory.note.Note — the core vault document model."""
import os
import pytest

from memory.note import Note, WIKILINK_RE, FRONTMATTER_RE


@pytest.fixture
def tmp_note_path(tmp_path):
    return str(tmp_path / "test_note.md")


# ---------------------------------------------------------------------------
# Note.create_new
# ---------------------------------------------------------------------------

class TestCreateNew:
    def test_creates_file_on_disk(self, tmp_note_path):
        Note.create_new(tmp_note_path, "My Note", "Hello world")
        assert os.path.isfile(tmp_note_path)

    def test_title_stored_in_frontmatter(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "Test Title", "body")
        assert note.title == "Test Title"
        assert "Test Title" in note.content

    def test_tags_stored(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "b", tags=["alpha", "beta"])
        assert "alpha" in note.tags
        assert "beta" in note.tags

    def test_extra_frontmatter_stored(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "b", extra_fm={"project": "myproj"})
        assert note.frontmatter.get("project") == "myproj"

    def test_note_type_stored(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "b", note_type="run_log")
        assert note.frontmatter.get("type") == "run_log"

    def test_created_and_modified_set(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "b")
        assert note.created_at
        assert note.modified_at

    def test_outgoing_links_extracted(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "See [[Other Note]] and [[Third]]")
        assert "Other Note" in note.outgoing_links
        assert "Third" in note.outgoing_links

    def test_empty_tags_default(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "b")
        assert isinstance(note.tags, list)

    def test_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "a" / "b" / "c" / "note.md")
        note = Note.create_new(path, "Deep Note", "body")
        assert os.path.isfile(path)


# ---------------------------------------------------------------------------
# Note.from_file
# ---------------------------------------------------------------------------

class TestFromFile:
    def test_roundtrip(self, tmp_note_path):
        Note.create_new(tmp_note_path, "Round Trip", "body text", tags=["t1"])
        loaded = Note.from_file(tmp_note_path)
        assert loaded.title == "Round Trip"
        assert "t1" in loaded.tags

    def test_body_content_preserved(self, tmp_note_path):
        Note.create_new(tmp_note_path, "T", "unique body content xyz")
        loaded = Note.from_file(tmp_note_path)
        assert "unique body content xyz" in loaded.body()

    def test_wikilinks_extracted(self, tmp_note_path):
        Note.create_new(tmp_note_path, "T", "See [[Linked Note]] here")
        loaded = Note.from_file(tmp_note_path)
        assert "Linked Note" in loaded.outgoing_links

    def test_no_duplicate_links(self, tmp_note_path):
        Note.create_new(tmp_note_path, "T", "[[A]] and [[A]] again")
        loaded = Note.from_file(tmp_note_path)
        assert loaded.outgoing_links.count("A") == 1

    def test_tags_as_list_from_frontmatter(self, tmp_note_path):
        Note.create_new(tmp_note_path, "T", "b", tags=["x", "y", "z"])
        loaded = Note.from_file(tmp_note_path)
        assert isinstance(loaded.tags, list)
        assert set(loaded.tags) == {"x", "y", "z"}

    def test_path_attribute_set(self, tmp_note_path):
        Note.create_new(tmp_note_path, "T", "b")
        loaded = Note.from_file(tmp_note_path)
        assert loaded.path == tmp_note_path

    def test_file_without_frontmatter(self, tmp_path):
        path = str(tmp_path / "bare.md")
        with open(path, "w") as f:
            f.write("# Just a heading\n\nSome content.")
        note = Note.from_file(path)
        assert "Some content" in note.body()


# ---------------------------------------------------------------------------
# Note.body()
# ---------------------------------------------------------------------------

class TestBody:
    def test_excludes_frontmatter(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "actual body here")
        body = note.body()
        assert "---" not in body
        assert "actual body here" in body

    def test_empty_body(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "")
        assert isinstance(note.body(), str)


# ---------------------------------------------------------------------------
# Note.save()
# ---------------------------------------------------------------------------

class TestSave:
    def test_save_updates_modified(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "original")
        original_modified = note.modified_at
        import time; time.sleep(0.01)
        note.content = note.content.replace("original", "updated")
        note.save()
        assert note.modified_at >= original_modified

    def test_save_persists_content(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "original")
        note.content = note.content.replace("original", "changed content")
        note.save()
        loaded = Note.from_file(tmp_note_path)
        assert "changed content" in loaded.content

    def test_save_creates_parent_dirs(self, tmp_path):
        path = str(tmp_path / "deep" / "nested" / "note.md")
        note = Note.create_new(path, "T", "b")
        # Already created by create_new, but save again should not fail
        note.save()
        assert os.path.isfile(path)


# ---------------------------------------------------------------------------
# Note.add_link()
# ---------------------------------------------------------------------------

class TestAddLink:
    def test_adds_wikilink(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "body")
        note.add_link("Other Note")
        assert "Other Note" in note.outgoing_links
        assert "[[Other Note]]" in note.content

    def test_no_duplicate_link(self, tmp_note_path):
        note = Note.create_new(tmp_note_path, "T", "body [[Existing]]")
        note.add_link("Existing")
        assert note.outgoing_links.count("Existing") == 1


# ---------------------------------------------------------------------------
# WIKILINK_RE
# ---------------------------------------------------------------------------

class TestWikilinkRE:
    def test_basic_link(self):
        assert WIKILINK_RE.findall("[[My Note]]") == ["My Note"]

    def test_multiple_links(self):
        found = WIKILINK_RE.findall("[[A]] and [[B]]")
        assert set(found) == {"A", "B"}

    def test_link_with_alias(self):
        found = WIKILINK_RE.findall("[[Note Title|display text]]")
        assert "Note Title" in found

    def test_no_links(self):
        assert WIKILINK_RE.findall("plain text") == []
