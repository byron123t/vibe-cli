"""UserProfile — persistent forensic behavioral profile for the VibeCLI user.

Global profile:
  vault/user/profile.json   — structured forensic JSON (demographics, personality, etc.)
  vault/user/profile.md     — human-readable markdown view (rendered in TUI graph pane)

Per-project profiles:
  vault/projects/{project}/profile.md — concise project summary, tech stack, focus
"""
from __future__ import annotations

import json as _json
import os
import re
from datetime import datetime

from memory.vault import MemoryVault


# ---------------------------------------------------------------------------
# Global profile — JSON structure and markdown template
# ---------------------------------------------------------------------------

# Default empty forensic profile
_EMPTY_FORENSIC: dict = {
    "demographics": {
        "estimated_age_range": "unknown",
        "likely_occupation": "unknown",
        "likely_location": "unknown",
        "experience_level": "unknown",
        "role_type": "unknown",
        "education_signal": "unknown",
    },
    "personality": {
        "work_style": "unknown",
        "approach": "unknown",
        "focus_granularity": "unknown",
        "confidence": "unknown",
        "traits": [],
    },
    "technical_interests": {
        "primary_languages": [],
        "frameworks": [],
        "domains": [],
        "tools": [],
        "enjoys": [],
        "avoids_or_delegates": [],
    },
    "behavioral_patterns": {
        "completion_tendency": "unknown",
        "testing_behavior": "unknown",
        "commit_pattern": "unknown",
        "iteration_style": "unknown",
        "context_switching": "unknown",
        "prompting_cadence": "unknown",
    },
    "prompting_style": {
        "phrasing": "unknown",
        "verbosity": "unknown",
        "recurring_vocabulary": [],
        "context_inclusion": "unknown",
    },
    "inferences": {
        "likely_motivations": [],
        "current_focus": "unknown",
        "project_maturity": "unknown",
        "career_signal": "unknown",
    },
}

_GLOBAL_MD_TEMPLATE = """\
# VibeCLI User Profile

_Maintained automatically by VibeCLI after each agent run._

**Last updated:** —

## Demographics
_Not yet observed._

## Personality
_Not yet observed._

## Technical Interests
_Not yet observed._

## Behavioral Patterns
_Not yet observed._

## Prompting Style
_Not yet observed._

## Inferences
_Not yet observed._
"""

_PROJECT_TEMPLATE = """\
# Project Profile: {project}

_Maintained automatically by VibeCLI after each agent run._

**Last updated:** —

## Summary
_Not yet observed._

## Tech Stack
_Not yet observed._

## Current Focus
_Not yet observed._

## Recurring Tasks
_Not yet observed._
"""

_STAMP_RE = re.compile(r"\*\*Last updated:\*\*.*")


def _stamp(content: str) -> str:
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    replacement = f"**Last updated:** {ts}"
    content = content.replace("**Last updated:** —", replacement)
    return _STAMP_RE.sub(replacement, content)


def _profile_to_markdown(profile: dict) -> str:
    """Convert a forensic JSON profile dict to a readable markdown document."""
    if not profile:
        return _GLOBAL_MD_TEMPLATE

    lines = [
        "# VibeCLI User Profile",
        "",
        "_Maintained automatically by VibeCLI after each agent run._",
        "",
        f"**Last updated:** —",
        "",
    ]

    def _kv(d: dict) -> list[str]:
        out = []
        for k, v in d.items():
            label = k.replace("_", " ").title()
            if isinstance(v, list):
                if v:
                    out.append(f"- **{label}:** {', '.join(str(x) for x in v)}")
                else:
                    out.append(f"- **{label}:** _none yet_")
            else:
                out.append(f"- **{label}:** {v}")
        return out

    section_map = {
        "demographics":         "## Demographics",
        "personality":          "## Personality",
        "technical_interests":  "## Technical Interests",
        "behavioral_patterns":  "## Behavioral Patterns",
        "prompting_style":      "## Prompting Style",
        "inferences":           "## Inferences",
    }

    for key, heading in section_map.items():
        section = profile.get(key, {})
        lines.append(heading)
        if section:
            lines.extend(_kv(section))
        else:
            lines.append("_Not yet observed._")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# UserProfile
# ---------------------------------------------------------------------------

class UserProfile:
    """Read/write the forensic JSON profile and per-project markdown profiles."""

    GLOBAL_JSON = os.path.join("user", "profile.json")
    GLOBAL_MD   = os.path.join("user", "profile.md")

    def __init__(self, vault: MemoryVault) -> None:
        self._vault = vault

    # ------------------------------------------------------------------ global JSON profile

    @property
    def json_path(self) -> str:
        return os.path.join(self._vault.root, self.GLOBAL_JSON)

    @property
    def md_path(self) -> str:
        return os.path.join(self._vault.root, self.GLOBAL_MD)

    def exists(self) -> bool:
        return os.path.isfile(self.json_path)

    def read_json(self) -> dict:
        """Return the current forensic profile as a dict."""
        if not os.path.isfile(self.json_path):
            return {}
        try:
            with open(self.json_path, encoding="utf-8") as f:
                return _json.load(f)
        except Exception:
            return {}

    def write_json(self, profile: dict) -> None:
        """Persist the forensic profile dict and regenerate the markdown view."""
        os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
        with open(self.json_path, "w", encoding="utf-8") as f:
            _json.dump(profile, f, indent=2, ensure_ascii=False)
        # Keep markdown view in sync
        md = _stamp(_profile_to_markdown(profile))
        with open(self.md_path, "w", encoding="utf-8") as f:
            f.write(md)

    # Legacy read/write (markdown only — used by TUI display)
    def read(self) -> str:
        if os.path.isfile(self.md_path):
            with open(self.md_path, encoding="utf-8") as f:
                return f.read()
        return _profile_to_markdown(self.read_json()) or _GLOBAL_MD_TEMPLATE

    def write(self, content: str) -> None:
        """Write a raw markdown profile (used for display overrides)."""
        os.makedirs(os.path.dirname(self.md_path), exist_ok=True)
        with open(self.md_path, "w", encoding="utf-8") as f:
            f.write(_stamp(content))

    # ------------------------------------------------------------------ per-project profile

    def _project_path(self, project: str) -> str:
        return os.path.join(self._vault.root, "projects", project, "profile.md")

    def read_project(self, project: str) -> str:
        p = self._project_path(project)
        if not os.path.isfile(p):
            return _PROJECT_TEMPLATE.format(project=project)
        with open(p, encoding="utf-8") as f:
            return f.read()

    def write_project(self, project: str, content: str) -> None:
        p = self._project_path(project)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(_stamp(content))
