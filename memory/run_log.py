"""RunLogger — save Claude CLI run outputs as timestamped markdown notes."""
from __future__ import annotations

import os
from datetime import datetime

from memory.note import Note
from memory.vault import MemoryVault
from memory.moc import MOCManager


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

        # Build tags — base set + any LLM-generated semantic tags
        base_tags = ["run_log", project, safe_action]
        all_tags  = base_tags + [t for t in (extra_tags or []) if t not in base_tags]

        # Summary section (shown at top if available)
        summary_section = ""
        if summary:
            summary_section = f"## Summary\n\n{summary}\n\n"

        files_section = ""
        if files_modified:
            files_section = "\n## Files Modified\n\n" + "\n".join(
                f"- `{f}`" for f in files_modified
            ) + "\n"

        body = (
            f"# Run: {action_label} — {now.strftime('%Y-%m-%d %H:%M')}\n\n"
            f"{summary_section}"
            f"## Prompt\n\n{prompt}\n\n"
            f"## Output\n\n```\n{output}\n```\n"
            f"{files_section}"
            f"\n## Links\n\n[[{project}]]\n"
        )

        note = self._vault.create_note(
            rel_path=rel_path,
            title=f"{now.strftime('%Y-%m-%d %H:%M')} {action_label}",
            body=body,
            tags=all_tags,
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
        """Return the last N run output texts for a project."""
        notes = self._vault.get_project_notes(project)
        run_logs = [no for no in notes if "run_log" in no.tags]
        run_logs.sort(key=lambda no: no.modified_at, reverse=True)
        results = []
        for note in run_logs[:n]:
            body = note.body()
            # Prefer the Summary section if present
            for marker in ("## Summary", "## Output"):
                start = body.find(marker)
                if start >= 0:
                    snippet = body[start + len(marker):start + 500].strip()
                    results.append(snippet)
                    break
        return results
