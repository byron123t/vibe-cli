#!/usr/bin/env python3
"""
Claude Code Stop hook — auto-updates README.md and CLAUDE.md.

Reads the current git diff (excluding the docs themselves), reads both
docs, then asks Claude to produce updated versions that reflect any
new features, changed behaviour, or removed code.  Writes the results
in-place only when the content actually changed.

Skips silently if:
  - ANTHROPIC_API_KEY is not set
  - There are no tracked-file changes (nothing to document)
  - The diff touches only README.md / CLAUDE.md (avoid feedback loop)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _git(args: list[str]) -> str:
    r = subprocess.run(["git", "-C", str(ROOT)] + args,
                       capture_output=True, text=True)
    return r.stdout.strip()


def main() -> None:
    # Read hook payload from stdin (Claude Code passes JSON)
    try:
        payload = json.load(sys.stdin)
    except Exception:
        payload = {}

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return

    # Diff of everything changed vs HEAD (staged + unstaged)
    diff = _git(["diff", "HEAD", "--", ".",
                 ":!README.md", ":!CLAUDE.md"])
    if not diff.strip():
        # Also check staged-only
        diff = _git(["diff", "--cached", "--", ".",
                     ":!README.md", ":!CLAUDE.md"])
    if not diff.strip():
        return   # nothing changed outside the docs — skip

    readme_path = ROOT / "README.md"
    claude_path = ROOT / "CLAUDE.md"
    readme = readme_path.read_text() if readme_path.exists() else ""
    claude = claude_path.read_text() if claude_path.exists() else ""

    try:
        import anthropic
    except ImportError:
        return

    client = anthropic.Anthropic(api_key=api_key)

    system = (
        "You are a technical writer maintaining docs for vibe-cli, "
        "a keyboard-first multi-project AI coding terminal built with Textual.\n"
        "You will be given:\n"
        "  1. A git diff of recent code changes\n"
        "  2. The current README.md\n"
        "  3. The current CLAUDE.md (developer/AI reference)\n\n"
        "Update both documents to accurately reflect the changes. Rules:\n"
        "- Keep the same structure and tone\n"
        "- README.md is user-facing: features, keys, install, config\n"
        "- CLAUDE.md is developer/AI reference: architecture, widget table, "
        "key design decisions, data flows\n"
        "- Remove anything that no longer applies\n"
        "- Do NOT add speculative future features\n"
        "- Return ONLY a JSON object: "
        "{\"readme\": \"<full updated README.md>\", "
        "\"claude_md\": \"<full updated CLAUDE.md>\"}"
    )

    user = (
        f"<diff>\n{diff[:8000]}\n</diff>\n\n"
        f"<readme>\n{readme}\n</readme>\n\n"
        f"<claude_md>\n{claude}\n</claude_md>"
    )

    try:
        msg = client.messages.create(
            model="claude-opus-4-6",
            max_tokens=8096,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        raw = msg.content[0].text.strip()

        # Strip markdown code fence if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0].strip()

        result = json.loads(raw)
    except Exception as e:
        print(f"[update_docs] skipped: {e}", file=sys.stderr)
        return

    new_readme    = result.get("readme", "").strip()
    new_claude_md = result.get("claude_md", "").strip()

    if new_readme and new_readme != readme.strip():
        readme_path.write_text(new_readme + "\n")
        print("[update_docs] README.md updated", file=sys.stderr)

    if new_claude_md and new_claude_md != claude.strip():
        claude_path.write_text(new_claude_md + "\n")
        print("[update_docs] CLAUDE.md updated", file=sys.stderr)


if __name__ == "__main__":
    main()
