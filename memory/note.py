"""Note — dataclass wrapping a single markdown file in the vault."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import yaml


WIKILINK_RE = re.compile(r'\[\[([^\[\]|#]+?)(?:[|#][^\]]*?)?\]\]')
FRONTMATTER_RE = re.compile(r'^---\s*\n(.*?)\n---\s*\n', re.DOTALL)


def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")


@dataclass
class Note:
    path: str                              # absolute path on disk
    title: str
    content: str                           # raw markdown (including frontmatter)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    outgoing_links: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    modified_at: str = field(default_factory=_now_iso)

    # ------------------------------------------------------------------ class methods

    @classmethod
    def from_file(cls, path: str) -> "Note":
        with open(path, encoding="utf-8") as f:
            raw = f.read()

        fm_match = FRONTMATTER_RE.match(raw)
        if fm_match:
            try:
                fm = yaml.safe_load(fm_match.group(1)) or {}
            except yaml.YAMLError:
                fm = {}
            body = raw[fm_match.end():]
        else:
            fm = {}
            body = raw

        title = fm.get("title") or os.path.splitext(os.path.basename(path))[0]
        tags  = fm.get("tags") or []
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]

        links = WIKILINK_RE.findall(body) + WIKILINK_RE.findall(raw)
        links = list(dict.fromkeys(links))  # dedup while preserving order

        return cls(
            path=path,
            title=title,
            content=raw,
            frontmatter=fm,
            tags=tags,
            outgoing_links=links,
            created_at=str(fm.get("created", _now_iso())),
            modified_at=str(fm.get("modified", _now_iso())),
        )

    @classmethod
    def create_new(cls, path: str, title: str, body: str,
                   tags: list[str] | None = None,
                   extra_fm: dict | None = None,
                   note_type: str = "note") -> "Note":
        tags = tags or []
        now  = _now_iso()
        fm: dict[str, Any] = {
            "title": title,
            "type": note_type,
            "tags": tags,
            "created": now,
            "modified": now,
        }
        if extra_fm:
            fm.update(extra_fm)
        fm_str = yaml.dump(fm, default_flow_style=False, allow_unicode=True).strip()
        content = f"---\n{fm_str}\n---\n\n{body}"
        links = WIKILINK_RE.findall(body)
        note = cls(
            path=path,
            title=title,
            content=content,
            frontmatter=fm,
            tags=tags,
            outgoing_links=links,
            created_at=now,
            modified_at=now,
        )
        note.save()
        return note

    # ------------------------------------------------------------------ methods

    def body(self) -> str:
        """Return markdown without frontmatter."""
        fm_match = FRONTMATTER_RE.match(self.content)
        return self.content[fm_match.end():] if fm_match else self.content

    def add_link(self, target_title: str) -> None:
        if target_title not in self.outgoing_links:
            self.outgoing_links.append(target_title)
            self.content += f"\n[[{target_title}]]"

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # Update modified timestamp in frontmatter
        now = _now_iso()
        self.modified_at = now
        if self.frontmatter:
            self.frontmatter["modified"] = now
            fm_str = yaml.dump(
                self.frontmatter, default_flow_style=False, allow_unicode=True
            ).strip()
            body = self.body()
            self.content = f"---\n{fm_str}\n---\n\n{body}"
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(self.content)

    def __repr__(self) -> str:
        return f"Note(title={self.title!r}, tags={self.tags})"
