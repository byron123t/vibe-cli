"""MemoryVault — read/write/search the Obsidian-style markdown vault."""
from __future__ import annotations

import os
import re
from typing import Iterator

from memory.note import Note


class MemoryVault:
    """
    Manages the on-disk markdown vault.
    All notes are .md files under vault_root.
    """

    def __init__(self, vault_root: str) -> None:
        self.root = os.path.abspath(vault_root)
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(os.path.join(self.root, "_MOCs"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "projects"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "user"), exist_ok=True)

    # ------------------------------------------------------------------ CRUD

    def get_note(self, rel_path: str) -> Note | None:
        path = self._abs(rel_path)
        if not os.path.isfile(path):
            return None
        return Note.from_file(path)

    def create_note(self, rel_path: str, title: str, body: str,
                    tags: list[str] | None = None,
                    extra_fm: dict | None = None,
                    note_type: str = "note") -> Note:
        path = self._abs(rel_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        return Note.create_new(path, title, body, tags, extra_fm, note_type)

    def save_note(self, note: Note) -> None:
        note.save()

    # ------------------------------------------------------------------ search

    def all_notes(self) -> list[Note]:
        notes = []
        for root, dirs, files in os.walk(self.root):
            # Skip hidden dirs
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fname.endswith(".md"):
                    try:
                        notes.append(Note.from_file(os.path.join(root, fname)))
                    except Exception:
                        pass
        return notes

    def search(self, query: str, case_sensitive: bool = False) -> list[Note]:
        """Full-text search across all notes."""
        results = []
        flags = 0 if case_sensitive else re.IGNORECASE
        pattern = re.compile(re.escape(query), flags)
        for note in self.all_notes():
            if pattern.search(note.content):
                results.append(note)
        return results

    def get_by_title(self, title: str) -> Note | None:
        for note in self.all_notes():
            if note.title.lower() == title.lower():
                return note
        return None

    def get_by_tag(self, tag: str) -> list[Note]:
        return [n for n in self.all_notes() if tag in n.tags]

    # ------------------------------------------------------------------ projects

    def list_projects(self) -> list[str]:
        proj_dir = os.path.join(self.root, "projects")
        if not os.path.isdir(proj_dir):
            return []
        return [d for d in os.listdir(proj_dir)
                if os.path.isdir(os.path.join(proj_dir, d))]

    def get_project_notes(self, project: str) -> list[Note]:
        proj_dir = os.path.join(self.root, "projects", project)
        notes = []
        if not os.path.isdir(proj_dir):
            return notes
        for root, _, files in os.walk(proj_dir):
            for fname in files:
                if fname.endswith(".md"):
                    try:
                        notes.append(Note.from_file(os.path.join(root, fname)))
                    except Exception:
                        pass
        return notes

    def ensure_project(self, project: str) -> None:
        proj_dir = os.path.join(self.root, "projects", project)
        os.makedirs(os.path.join(proj_dir, "run_logs"), exist_ok=True)

    # ------------------------------------------------------------------ MOCs

    def get_moc(self, topic: str) -> Note | None:
        rel = os.path.join("_MOCs", f"MOC - {topic}.md")
        return self.get_note(rel)

    def moc_path(self, topic: str) -> str:
        return os.path.join(self.root, "_MOCs", f"MOC - {topic}.md")

    def list_mocs(self) -> list[Note]:
        moc_dir = os.path.join(self.root, "_MOCs")
        notes = []
        for fname in os.listdir(moc_dir):
            if fname.endswith(".md"):
                try:
                    notes.append(Note.from_file(os.path.join(moc_dir, fname)))
                except Exception:
                    pass
        return notes

    # ------------------------------------------------------------------ helpers

    def _abs(self, rel: str) -> str:
        if not rel.endswith(".md"):
            rel += ".md"
        return os.path.join(self.root, rel)

    def rel_path(self, note: Note) -> str:
        return os.path.relpath(note.path, self.root)
