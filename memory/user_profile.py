"""UserProfile — persistent psychometric profile for the VibeSwipe user.

Stored at vault/user/profile.md.
Written and read by ProfileAnalyzer; excluded from vault lint orphan checks.
"""
from __future__ import annotations

import os
from datetime import datetime

from memory.vault import MemoryVault


_INITIAL_TEMPLATE = """\
# VibeSwipe User Profile

_Maintained automatically by VibeSwipe after each agent run. Do not edit by hand._

**Last updated:** —

## Prompting Style
_Not yet observed._

## Technical Stack
_Not yet observed._

## Task Patterns
_Not yet observed._

## Cognitive Tendencies
_Not yet observed._

## Current Focus
_Not yet observed._

## Behavioral Notes
_Not yet observed._
"""


class UserProfile:
    """Read/write the user's psychometric profile note."""

    REL_PATH = os.path.join("user", "profile.md")

    def __init__(self, vault: MemoryVault) -> None:
        self._vault = vault

    @property
    def path(self) -> str:
        return os.path.join(self._vault.root, self.REL_PATH)

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def read(self) -> str:
        """Return current profile text; returns initial template if not yet created."""
        if not self.exists():
            return _INITIAL_TEMPLATE
        with open(self.path, encoding="utf-8") as f:
            return f.read()

    def write(self, content: str) -> None:
        """Overwrite the profile with updated content, stamping the timestamp."""
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        # Stamp last-updated
        stamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        content = content.replace("**Last updated:** —",
                                  f"**Last updated:** {stamp}")
        # If the stamp line already has a date, update it
        import re
        content = re.sub(
            r"\*\*Last updated:\*\* \d{4}-\d{2}-\d{2}.*",
            f"**Last updated:** {stamp}",
            content,
        )
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(content)
