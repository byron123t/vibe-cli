"""RunLogger — save Claude CLI run outputs as timestamped markdown notes."""
from __future__ import annotations

import os
import re
from datetime import datetime

from memory.note import Note
from memory.vault import MemoryVault
from memory.moc import MOCManager


# ---------------------------------------------------------------------------
# Non-LLM helpers (always work, no API key required)
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
    ("bugfix",   ["fix", "bug", "error", "broken", "crash", "issue", "debug", "broken"]),
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


def _infer_tags(prompt: str, output: str) -> list[str]:
    """Infer semantic tags from prompt + output without requiring an LLM."""
    text = (prompt + " " + output).lower()
    tags: list[str] = []

    # 1. Language (first match wins)
    for lang, pattern in _LANG_KEYWORDS:
        if re.search(pattern, text, re.IGNORECASE):
            tags.append(lang)
            break

    # 2. Task type (first match wins)
    for task_type, keywords in _TASK_KEYWORDS:
        if any(kw in text for kw in keywords):
            tags.append(task_type)
            break

    # 3. Topics (up to 3)
    for topic, keywords in _TOPIC_KEYWORDS.items():
        if any(kw in text for kw in keywords) and topic not in tags:
            tags.append(topic)
        if len(tags) >= 5:
            break

    return tags


def _simple_summary(prompt: str, output: str) -> str:
    """Extract the first meaningful line from agent output as a summary."""
    # Strip tool-use indicators (⟳ lines) and find first real text
    lines = output.split("\n")
    candidates: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("⟳") or stripped.startswith("```") or stripped.startswith("#"):
            continue
        if len(stripped) > 25:
            candidates.append(stripped)

    if candidates:
        # Take the last substantive line (agents usually summarize at the end)
        summary = candidates[-1][:220]
        return summary

    # Fallback: truncate the prompt
    return f"Completed: {prompt[:120]}"


# ---------------------------------------------------------------------------
# RunLogger
# ---------------------------------------------------------------------------

class RunLogger:
    """
    After each Claude CLI run, create a markdown note in:
        vault/projects/<project>/run_logs/<timestamp>_<action>.md
    and link it from the project MOC and the Run Outputs MOC.
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
        safe_action = action_id.replace(".", "_").replace("/", "_")
        rel_path = os.path.join(
            "projects", project, "run_logs",
            f"{timestamp_str}_{safe_action}.md"
        )

        self._vault.ensure_project(project)

        # Summary: use LLM-provided one, or fall back to keyword extraction
        if not summary:
            summary = _simple_summary(prompt, output)

        # Tags: merge base + LLM-generated + inferred (deduplicated)
        inferred = _infer_tags(prompt, output)
        base_tags = ["run_log", project]
        seen: set[str] = set(base_tags)
        merged_tags = list(base_tags)
        for t in list(extra_tags or []) + inferred:
            if t and t not in seen:
                seen.add(t)
                merged_tags.append(t)

        files_section = ""
        if files_modified:
            files_section = "\n## Files Modified\n\n" + "\n".join(
                f"- `{f}`" for f in files_modified
            ) + "\n"

        body = (
            f"# Run: {action_label} — {now.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"## Summary\n\n{summary}\n\n"
            f"## Prompt\n\n{prompt}\n\n"
            f"## Output\n\n```\n{output}\n```\n"
            f"{files_section}"
            f"\n## Links\n\n[[{project}]]\n"
        )

        note = self._vault.create_note(
            rel_path=rel_path,
            title=f"{now.strftime('%Y-%m-%d %H:%M')} {action_label}",
            body=body,
            tags=merged_tags,
            extra_fm={
                "project":          project,
                "action":           action_id,
                "duration_seconds": round(duration_seconds, 2),
                "moc_topics":       [project, "run_outputs"],
            },
            note_type="run_log",
        )

        # Update MOCs
        self._moc.add_note_to_moc(note, "Run Outputs")
        if project:
            self._moc.add_note_to_moc(note, project)
        self._moc.update_index_moc()

        return note

    def get_recent_outputs(self, project: str, n: int = 5) -> list[str]:
        """Return the last N run summaries for a project."""
        notes = self._vault.get_project_notes(project)
        run_logs = [no for no in notes if "run_log" in no.tags]
        run_logs.sort(key=lambda no: no.modified_at, reverse=True)
        results = []
        for note in run_logs[:n]:
            body = note.body()
            for marker in ("## Summary", "## Output"):
                start = body.find(marker)
                if start >= 0:
                    snippet = body[start + len(marker):start + 400].strip()
                    results.append(snippet)
                    break
        return results
