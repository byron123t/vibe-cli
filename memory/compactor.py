"""Compactor — merge temporally-close, topically-related run log notes.

Groups run_log notes for a project that were created within
COMPACT_WINDOW_HOURS of each other AND share a component or topic tag.
Matched groups of ≥ MIN_GROUP_SIZE are collapsed into one consolidated note;
originals are deleted from disk.  MOC links are updated accordingly.

Merged note format:

    # auth · session — 2026-04-10

    Merged 3 runs  (18:00 – 19:45)

    - **18:00** Fix JWT expiry check _(fix the auth bug in the login flow)_
    - **18:42** Add refresh token endpoint _(implement refresh tokens)_
    - **19:45** Write auth unit tests _(add tests for auth module)_

    **Files:** `src/auth.py` · `src/tests/test_auth.py`

    [[myproject]]
"""
from __future__ import annotations

import os
from collections import Counter
from datetime import datetime
from typing import TYPE_CHECKING

from memory.note import Note
from memory.vault import MemoryVault
from memory.moc import MOCManager

if TYPE_CHECKING:
    pass

# Notes created within this many hours of each other are candidates for merging
COMPACT_WINDOW_HOURS: float = 2.0

# Minimum group size to trigger a merge
MIN_GROUP_SIZE: int = 2

# Tags that carry no topical information (ignored when comparing similarity)
_BASE_TAGS = frozenset({"run_log", "run_outputs"})

_DT_FORMATS = (
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H-%M-%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)


def _parse_dt(s: str) -> datetime:
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            pass
    return datetime.min


def _note_summary(note: Note) -> str:
    """Extract one-line summary from new (blockquote) or old (## Summary) format."""
    body = note.body()
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("> "):
            return s[2:].strip()
    start = body.find("## Summary")
    if start >= 0:
        snippet = body[start + 10:start + 300].strip().splitlines()
        for ln in snippet:
            if ln.strip():
                return ln.strip()[:180]
    return note.title


def _note_prompt(note: Note) -> str:
    """Extract the prompt line from a run log note."""
    body = note.body()
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("**Prompt:**"):
            return s[len("**Prompt:**"):].strip()
    # Old format
    start = body.find("## Prompt")
    if start >= 0:
        for ln in body[start + 9:start + 300].strip().splitlines():
            if ln.strip():
                return ln.strip()[:120]
    return note.frontmatter.get("action", "")


def _note_component(note: Note) -> str:
    return note.frontmatter.get("component", "")


def _note_files(note: Note) -> set[str]:
    raw = note.frontmatter.get("files") or []
    if isinstance(raw, list):
        return set(raw)
    return set()


def _note_topic_tags(note: Note, project: str) -> set[str]:
    skip = _BASE_TAGS | {project, "run_log"}
    return {t for t in note.tags if t not in skip}


def _files_line(files: set[str]) -> str:
    return "  ·  ".join(f"`{f}`" for f in sorted(files)[:10])


# ---------------------------------------------------------------------------
# Compactor
# ---------------------------------------------------------------------------

class Compactor:
    """Merge redundant run log notes within a project's vault directory."""

    def __init__(self, vault: MemoryVault, moc: MOCManager) -> None:
        self._vault = vault
        self._moc   = moc

    # ------------------------------------------------------------------ public

    def compact_project(self, project: str) -> int:
        """Compact run logs for *project*.  Returns number of notes removed."""
        notes = self._vault.get_project_notes(project)
        run_logs = [n for n in notes if "run_log" in n.tags]
        if len(run_logs) < MIN_GROUP_SIZE:
            return 0

        groups = self._group_notes(run_logs, project)
        removed = 0
        for group in groups:
            self._merge_group(group, project)
            removed += len(group) - 1   # one new note replaces the group

        if removed:
            # Rebuild MOC after structural changes
            try:
                self._moc.update_moc(project)
                self._moc.update_moc("Run Outputs")
                self._moc.update_index_moc()
            except Exception:
                pass

        return removed

    def compact_all(self) -> int:
        """Compact every project in the vault.  Returns total notes removed."""
        total = 0
        for project in self._vault.list_projects():
            try:
                total += self.compact_project(project)
            except Exception:
                pass
        return total

    # ------------------------------------------------------------------ grouping

    def _group_notes(self, notes: list[Note], project: str) -> list[list[Note]]:
        """Return lists of ≥ MIN_GROUP_SIZE notes that should be merged."""
        sorted_notes = sorted(notes, key=lambda n: _parse_dt(n.created_at))
        groups: list[list[Note]] = []
        current: list[Note] = [sorted_notes[0]]

        for note in sorted_notes[1:]:
            prev = current[-1]
            dt_prev = _parse_dt(prev.created_at)
            dt_cur  = _parse_dt(note.created_at)
            hours   = (dt_cur - dt_prev).total_seconds() / 3600.0

            if hours <= COMPACT_WINDOW_HOURS and self._similar(prev, note, project):
                current.append(note)
            else:
                if len(current) >= MIN_GROUP_SIZE:
                    groups.append(current)
                current = [note]

        if len(current) >= MIN_GROUP_SIZE:
            groups.append(current)

        return groups

    def _similar(self, a: Note, b: Note, project: str) -> bool:
        """True if two notes share a component, topic tag, or overlapping file."""
        ca, cb = _note_component(a), _note_component(b)
        if ca and cb and ca == cb:
            return True
        ta = _note_topic_tags(a, project)
        tb = _note_topic_tags(b, project)
        if ta & tb:
            return True
        fa, fb = _note_files(a), _note_files(b)
        if fa and fb and fa & fb:
            return True
        return False

    # ------------------------------------------------------------------ merge

    def _merge_group(self, group: list[Note], project: str) -> Note:
        """Replace *group* with a single consolidated note; delete originals."""
        # Determine dominant component
        components = [_note_component(n) for n in group if _note_component(n)]
        component = Counter(components).most_common(1)[0][0] if components else project

        # Union of all tags (preserve order, skip base tags in title)
        all_tags: list[str] = []
        seen_tags: set[str] = set()
        for note in group:
            for t in note.tags:
                if t not in seen_tags:
                    seen_tags.add(t)
                    all_tags.append(t)

        # Union of all files
        all_files: set[str] = set()
        for note in group:
            all_files |= _note_files(note)

        # Time range
        dts = [_parse_dt(n.created_at) for n in group]
        dt_first, dt_last = min(dts), max(dts)
        date_str  = dt_first.strftime("%Y-%m-%d")
        range_str = f"{dt_first.strftime('%H:%M')} – {dt_last.strftime('%H:%M')}"

        # Build merged body
        body_lines = [
            f"# {component} · session — {date_str}\n",
            f"\nMerged {len(group)} runs  ({range_str})\n",
        ]
        for note in group:
            dt   = _parse_dt(note.created_at)
            summ = _note_summary(note)[:120]
            prt  = _note_prompt(note)[:80]
            line = f"- **{dt.strftime('%H:%M')}** {summ}"
            if prt and prt.lower() not in summ.lower():
                line += f" _({prt})_"
            body_lines.append(line + "\n")

        if all_files:
            body_lines.append(f"\n**Files:** {_files_line(all_files)}\n")
        body_lines.append(f"\n[[{project}]]\n")

        title     = f"{component} · session — {date_str}"
        timestamp = dt_first.strftime("%Y-%m-%dT%H-%M-%S")
        rel_path  = os.path.join(
            "projects", project, "run_logs",
            f"{timestamp}_session_{component}.md",
        )

        merged = self._vault.create_note(
            rel_path=rel_path,
            title=title,
            body="".join(body_lines),
            tags=all_tags,
            extra_fm={
                "project":    project,
                "component":  component,
                "files":      sorted(all_files),
                "merged_from": len(group),
                "moc_topics": [project, "run_outputs"],
            },
            note_type="run_log",
        )

        # Delete originals
        for note in group:
            self._vault.delete_note(note)

        return merged
