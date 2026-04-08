"""UserProfile — persistent behavioral profile for the VibeSwipe user.

Global profile: vault/user/profile.md
  Tracks demographics, personality traits, technical interests, behavioral
  patterns, and prompting style inferred from the full cross-project corpus.

Per-project profiles: vault/projects/{project}/profile.md
  Concise project summary, tech stack, current focus, and recurring task types.
"""
from __future__ import annotations

import os
import re
from datetime import datetime

from memory.vault import MemoryVault


_GLOBAL_TEMPLATE = """\
# VibeSwipe User Profile

_Maintained automatically by VibeSwipe after each agent run. Do not edit by hand._

**Last updated:** —

## Developer Identity
_Not yet observed._

## Personality Traits
_Not yet observed._

## Technical Interests
_Not yet observed._

## Behavioral Patterns
_Not yet observed._

## Prompting Style
_Not yet observed._

## Current Focus
_Not yet observed._
"""

_PROJECT_TEMPLATE = """\
# Project Profile: {project}

_Maintained automatically by VibeSwipe after each agent run._

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


class UserProfile:
    """Read/write the global user profile and per-project profiles."""

    GLOBAL_REL = os.path.join("user", "profile.md")

    def __init__(self, vault: MemoryVault) -> None:
        self._vault = vault

    # ------------------------------------------------------------------ global profile

    @property
    def path(self) -> str:
        return os.path.join(self._vault.root, self.GLOBAL_REL)

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def read(self) -> str:
        if not self.exists():
            return _GLOBAL_TEMPLATE
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def write(self, content: str) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(_stamp(content))

    # ------------------------------------------------------------------ per-project profile

    def _project_path(self, project: str) -> str:
        return os.path.join(
            self._vault.root, "projects", project, "profile.md"
        )

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
