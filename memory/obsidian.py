"""ObsidianVault — connect to an external Obsidian-style vault.

ObsidianNote    — lightweight note loaded from a .md file (title, body, tags, todos)
ObsidianVault   — reads all .md files, extracts todos, scores project relevance
ObsidianLinker  — persists project↔note associations to vault/user/obsidian_links.json
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from memory.vault import MemoryVault


@dataclass
class ObsidianNote:
    path:  str
    title: str
    body:  str          = ""
    tags:  list[str]    = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str) -> "ObsidianNote":
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()

        tags: list[str] = []
        body = raw

        # Extract YAML frontmatter
        fm_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            body    = raw[fm_match.end():]

            tags_match = re.search(r"^tags\s*:\s*(.+)$", fm_text, re.MULTILINE)
            if tags_match:
                raw_tags = tags_match.group(1).strip()
                if raw_tags.startswith("["):
                    tags = [t.strip().strip("\"'") for t in raw_tags.strip("[]").split(",") if t.strip()]
                else:
                    block = re.findall(r"^\s*-\s+(\S+)", fm_text, re.MULTILINE)
                    tags  = block if block else [raw_tags.strip()]

        # Inline #tags from body
        inline = re.findall(r"#([a-zA-Z][a-zA-Z0-9_/-]+)", body)
        seen   = set(tags)
        for t in inline:
            if t not in seen:
                tags.append(t)
                seen.add(t)

        # Title: first H1 heading, else filename stem
        h1 = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        title = h1.group(1).strip() if h1 else os.path.splitext(os.path.basename(path))[0]

        return cls(path=path, title=title, body=body, tags=tags)

    def todos(self) -> list[str]:
        """Return unchecked todo items (lines starting with - [ ] or * [ ])."""
        results = []
        for line in self.body.splitlines():
            s = line.strip()
            if (s.startswith("- [ ] ") or s.startswith("* [ ] ")):
                todo_text = s[6:].strip()
                if todo_text:
                    results.append(todo_text)
        return results


class ObsidianVault:
    """Read notes from an Obsidian vault directory (read-only)."""

    def __init__(self, vault_path: str) -> None:
        self.root = os.path.abspath(os.path.expanduser(vault_path))

    def exists(self) -> bool:
        return os.path.isdir(self.root)

    def all_notes(self) -> list[ObsidianNote]:
        notes: list[ObsidianNote] = []
        for root, dirs, files in os.walk(self.root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in sorted(files):
                if fname.endswith(".md"):
                    try:
                        notes.append(ObsidianNote.from_file(os.path.join(root, fname)))
                    except Exception:
                        pass
        return notes

    def score_relevance(
        self,
        note: ObsidianNote,
        project_name: str,
        project_path: str,
    ) -> float:
        """
        Heuristic 0.0–1.0 relevance score.

        0.5  — project name appears in note title
        0.3  — project name appears in note body
        0.2  — a path-derived keyword appears in title or body
        0.1  — project name is one of the note's tags
        """
        proj_lower  = project_name.lower()
        title_lower = note.title.lower()
        body_lower  = note.body.lower()

        score = 0.0

        if proj_lower in title_lower:
            score += 0.5
        elif proj_lower in body_lower:
            score += 0.3

        if project_path:
            base     = os.path.basename(project_path.rstrip("/"))
            keywords = [p.lower() for p in re.split(r"[/_\-\s]+", base)
                        if len(p) > 2 and p.lower() != proj_lower]
            for kw in keywords:
                if kw in title_lower or kw in body_lower:
                    score += 0.2
                    break

        if proj_lower in {t.lower() for t in note.tags}:
            score += 0.1

        return min(score, 1.0)


class ObsidianLinker:
    """Persist project↔Obsidian note associations to vault/user/obsidian_links.json."""

    def __init__(self, vault: "MemoryVault") -> None:
        self._path = os.path.join(vault.root, "user", "obsidian_links.json")
        self._data: dict[str, list[str]] = self._load()

    def _load(self) -> dict[str, list[str]]:
        try:
            with open(self._path, encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._path), exist_ok=True)
        with open(self._path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2)

    def get_project_notes(self, project_name: str) -> list[str]:
        return list(self._data.get(project_name, []))

    def mark(self, project_name: str, note_path: str) -> None:
        notes = self._data.setdefault(project_name, [])
        if note_path not in notes:
            notes.append(note_path)
            self._save()

    def unmark(self, project_name: str, note_path: str) -> None:
        notes = self._data.get(project_name, [])
        if note_path in notes:
            notes.remove(note_path)
            self._save()

    def is_marked(self, project_name: str, note_path: str) -> bool:
        return note_path in self._data.get(project_name, [])
