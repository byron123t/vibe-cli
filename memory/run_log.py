"""RunLogger — save agent run summaries as compact, coding-focused markdown notes.

Format (single-screen, no verbose output dump):

    ---
    type: run_log
    project: myproject
    component: auth
    files: [src/auth.py, src/login.py]
    tags: [run_log, myproject, bugfix, auth, python]
    duration_seconds: 45.2
    created: 2026-04-10T18:34:58
    modified: 2026-04-10T18:34:58
    ---
    # auth · bugfix — 2026-04-10 18:34

    > Fix JWT validation in src/auth.py; token expiry check was inverted.

    **Prompt:** Fix the auth bug in the login flow

    **Files:** `src/auth.py` · `src/login.py`

    [[myproject]]
"""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime

from memory.note import Note
from memory.vault import MemoryVault
from memory.moc import MOCManager


# ---------------------------------------------------------------------------
# Tag inference tables
# ---------------------------------------------------------------------------

_LANG_KEYWORDS: list[tuple[str, str]] = [
    ("python",     r"\bpython\b|\.py\b|import |def |pytest"),
    ("typescript", r"\btypescript\b|\.ts\b|interface |type "),
    ("javascript", r"\bjavascript\b|\.js\b|const |let |npm "),
    ("go",         r"\bgolang\b|\bgo\b|\.go\b|func |goroutine"),
    ("rust",       r"\brust\b|\.rs\b|cargo |fn |impl "),
    ("bash",       r"\bbash\b|#!/|shell |\.sh\b"),
    ("sql",        r"\bsql\b|SELECT |INSERT |CREATE TABLE"),
    ("html",       r"\bhtml\b|<div|<body|<head"),
    ("css",        r"\bcss\b|\.css\b|margin:|padding:"),
]

_TASK_KEYWORDS: list[tuple[str, list[str]]] = [
    ("commit",   ["git commit", "git add", "commit all", "commit the"]),
    ("bugfix",   ["fix", "bug", "error", "broken", "crash", "issue", "debug"]),
    ("test",     ["test", "pytest", "unittest", "spec", "assert", "coverage"]),
    ("docs",     ["readme", "docstring", "documentation", "comment", "document"]),
    ("refactor", ["refactor", "clean", "restructure", "rename", "reorganize", "simplify"]),
    ("config",   ["config", "settings", ".env", "environment", "setup", "install"]),
    ("feature",  ["add", "implement", "create", "build", "new", "write"]),
    ("analysis", ["explain", "why", "how", "what is", "analyze", "review", "check"]),
]

_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "auth":        ["auth", "login", "password", "token", "jwt", "oauth", "session", "cookie"],
    "database":    ["database", " db ", "sql", "mongo", "postgres", "sqlite", "redis", "orm"],
    "api":         ["api", "endpoint", "rest", "graphql", "request", "response", "http", "fetch"],
    "ui":          ["widget", "component", "button", "layout", "css", "style", "render", "ui "],
    "cli":         ["cli", "command", "argument", "flag", "terminal", "shell", "argparse"],
    "performance": ["performance", "speed", "optimize", "cache", "latency", "benchmark"],
    "security":    ["security", "vulnerabilit", "injection", "xss", "sanitize", "secure"],
    "ci":          ["github action", "ci/cd", "pipeline", "workflow", "deploy", "docker"],
    "types":       ["type annotation", "typing", "mypy", "typecheck", "interface", "generics"],
    "deps":        ["requirements", "package.json", "dependency", "install", "upgrade", "pip "],
}

# Top-level dirs that don't indicate a component (look one level deeper)
_SKIP_PREFIXES = frozenset({
    "src", "lib", "app", "apps", "pkg", "internal",
    "test", "tests", "spec", "specs", "core",
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _infer_tags(prompt: str, output: str) -> list[str]:
    text = (prompt + " " + output).lower()
    tags: list[str] = []
    for lang, pattern in _LANG_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            tags.append(lang)
            break
    for task_type, keywords in _TASK_KEYWORDS:
        if any(kw in text for kw in keywords):
            tags.append(task_type)
            break
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(kw in text for kw in keywords) and topic not in tags:
            tags.append(topic)
        if len(tags) >= 5:
            break
    return tags


def _extract_component(files: list[str], tags: list[str], project: str) -> str:
    """Derive a short component name from file paths or topic tags."""
    if files:
        segs: list[str] = []
        for f in files:
            parts = f.replace("\\", "/").split("/")
            stem_only = [os.path.splitext(p)[0].lower() for p in parts]
            # Walk path segments, skip uninformative top-level dirs
            found = ""
            for seg in stem_only[:-1]:          # exclude filename
                if seg not in _SKIP_PREFIXES:
                    found = seg
                    break
            if not found:
                # All dirs were skippable — use filename stem, strip test_ prefix
                raw = stem_only[-1]
                for pfx in ("test_", "spec_", "tests_"):
                    if raw.startswith(pfx):
                        raw = raw[len(pfx):]
                        break
                found = raw
            segs.append(found)
        if segs:
            return Counter(segs).most_common(1)[0][0]

    # Fall back to topic tags (auth, api, ui, …)
    topic_set = set(_TOPIC_KEYWORDS)
    for t in tags:
        if t in topic_set:
            return t

    # Fall back to first task-type tag
    task_set = {"bugfix", "feature", "refactor", "test", "docs", "config", "analysis"}
    for t in tags:
        if t in task_set:
            return t

    return project or "general"


def _simple_summary(prompt: str, output: str) -> str:
    """Extract the last substantive line from agent output."""
    candidates: list[str] = []
    for line in output.split("\n"):
        s = line.strip()
        if not s or s.startswith("⟳") or s.startswith("```") or s.startswith("#"):
            continue
        if len(s) > 25:
            candidates.append(s)
    if candidates:
        return candidates[-1][:220]
    return f"Completed: {prompt[:120]}"


def _fmt_duration(secs: float) -> str:
    if secs < 60:
        return f"{int(secs)}s"
    m, s = divmod(int(secs), 60)
    return f"{m}m {s:02d}s"


def _files_line(files: list[str]) -> str:
    return "  ·  ".join(f"`{f}`" for f in files[:8])


# ---------------------------------------------------------------------------
# RunLogger
# ---------------------------------------------------------------------------

class RunLogger:
    """
    After each agent run create a compact note in:
        vault/projects/<project>/run_logs/<timestamp>_<action>.md
    """

    def __init__(self, vault: MemoryVault, moc: MOCManager) -> None:
        self._vault = vault
        self._moc   = moc

    def log(
        self,
        action_id: str,
        action_label: str,
        project: str,
        prompt: str,
        output: str,
        files_modified: list[str] | None = None,
        duration_seconds: float = 0.0,
        summary: str = "",
        extra_tags: list[str] | None = None,
    ) -> Note:
        now = datetime.utcnow()
        timestamp_str = now.strftime("%Y-%m-%dT%H-%M-%S")
        safe_action   = action_id.replace(".", "_").replace("/", "_")
        rel_path = os.path.join(
            "projects", project, "run_logs",
            f"{timestamp_str}_{safe_action}.md"
        )

        self._vault.ensure_project(project)

        if not summary:
            summary = _simple_summary(prompt, output)

        # Merge base + LLM + inferred tags (deduplicated, order-preserved)
        inferred   = _infer_tags(prompt, output)
        base_tags  = ["run_log", project]
        seen: set[str] = set(base_tags)
        merged_tags = list(base_tags)
        for t in list(extra_tags or []) + inferred:
            if t and t not in seen:
                seen.add(t)
                merged_tags.append(t)

        files = files_modified or []
        component = _extract_component(files, merged_tags, project)

        # Title: "{component} · {task_type} — {date}"
        task_tag = next(
            (t for t in merged_tags if t in {
                "bugfix", "feature", "refactor", "test", "docs",
                "config", "analysis", "commit",
            }),
            None,
        )
        title_parts = [component]
        if task_tag:
            title_parts.append(task_tag)
        note_title = " · ".join(title_parts) + f" — {now.strftime('%Y-%m-%d %H:%M')}"

        # Compact body — no raw output dump
        prompt_display = prompt[:100] + ("…" if len(prompt) > 100 else "")
        body_parts = [
            f"# {note_title}\n",
            f"> {summary}\n",
            f"\n**Prompt:** {prompt_display}\n",
        ]
        if files:
            body_parts.append(f"\n**Files:** {_files_line(files)}\n")
        body_parts.append(f"\n[[{project}]]\n")
        body = "".join(body_parts)

        note = self._vault.create_note(
            rel_path=rel_path,
            title=note_title,
            body=body,
            tags=merged_tags,
            extra_fm={
                "project":          project,
                "component":        component,
                "files":            files,
                "action":           action_id,
                "duration_seconds": round(duration_seconds, 2),
                "moc_topics":       [project, "run_outputs"],
            },
            note_type="run_log",
        )

        self._moc.add_note_to_moc(note, "Run Outputs")
        if project:
            self._moc.add_note_to_moc(note, project)
        self._moc.update_index_moc()

        return note

    def get_recent_outputs(self, project: str, n: int = 5) -> list[str]:
        """Return the last N run summaries for a project (handles old + new format)."""
        notes = self._vault.get_project_notes(project)
        run_logs = [no for no in notes if "run_log" in no.tags]
        run_logs.sort(key=lambda no: no.modified_at, reverse=True)
        results = []
        for note in run_logs[:n]:
            body = note.body()
            # New format: blockquote line starting with ">"
            for line in body.splitlines():
                s = line.strip()
                if s.startswith("> "):
                    results.append(s[2:].strip())
                    break
            else:
                # Old format: ## Summary section
                start = body.find("## Summary")
                if start >= 0:
                    snippet = body[start + 10:start + 300].strip()
                    results.append(snippet[:200])
        return results
