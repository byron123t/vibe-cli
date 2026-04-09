"""AgentSession — abstract base class for all AI coding agent sessions."""
from __future__ import annotations

import shutil
import time
import uuid
from abc import ABC, abstractmethod
from typing import Callable


class AgentSession(ABC):
    """
    Common interface for Claude, Codex, and Cursor agent sessions.

    Each subclass wraps a specific CLI tool and maps the three VibeCLI
    permission modes (safe / accept_edits / bypass) to that tool's flags.
    """

    def __init__(
        self,
        prompt: str,
        project_path: str,
        session_id: str | None = None,
        permission_mode: str = "accept_edits",
        extra_flags: list[str] | None = None,
        resume_session_id: str | None = None,
        effort_mode: str = "medium",
    ) -> None:
        self.session_id         = session_id or str(uuid.uuid4())[:8]
        self.prompt             = prompt
        self.project_path       = project_path
        self.permission_mode    = permission_mode
        self.effort_mode        = effort_mode   # "low" | "medium" | "high"
        self.extra_flags        = extra_flags or []
        self.resume_session_id  = resume_session_id   # for --resume / continuation
        self.start_time         = time.time()
        self.exit_code: int | None  = None
        self.captured_session_id: str | None = None   # set by subclass from tool output
        self._output_tail: list[str] = []             # last 30 lines for suggestions

    # ── subclass interface ──────────────────────────────────────────────────

    @staticmethod
    @abstractmethod
    def is_available() -> bool:
        """Return True if the underlying CLI tool is on PATH."""

    @abstractmethod
    async def run(
        self,
        on_line: Callable[[str], None] | None = None,
        on_permission_request: Callable[[dict], None] | None = None,
    ) -> int:
        """Run the agent, stream display lines via on_line, return exit code."""

    def cancel(self) -> None:
        """Terminate the running subprocess (no-op if not running)."""

    async def approve_permission(self, request_id: str, allow: bool) -> None:
        """
        Send a permission decision to the subprocess.
        Default no-op; overridden by ClaudeSession which uses stream-json stdin.
        """

    # ── shared helpers ──────────────────────────────────────────────────────

    def _emit(self, text: str, on_line: Callable[[str], None] | None) -> None:
        """Append text lines to the output tail and call on_line for each."""
        for line in text.split("\n"):
            self._output_tail.append(line)
            if len(self._output_tail) > 30:
                self._output_tail.pop(0)
            if on_line:
                on_line(line)

    # ── properties ─────────────────────────────────────────────────────────

    @property
    def output_tail(self) -> list[str]:
        return list(self._output_tail)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    @property
    def is_done(self) -> bool:
        return self.exit_code is not None


class RestoredSession(AgentSession):
    """
    A pre-completed session rebuilt from saved state.  Never spawns a process.
    Used by AgentWidget to show saved output and allow --resume replies.
    """

    @staticmethod
    def is_available() -> bool:
        return True

    async def run(self, on_line=None, on_permission_request=None) -> int:
        return self.exit_code or 0

    @classmethod
    def from_saved(cls, data: dict) -> "RestoredSession":
        inst = cls(
            prompt=data["prompt"],
            project_path=data["project_path"],
            session_id=data.get("session_id"),
            permission_mode=data.get("permission_mode", "accept_edits"),
        )
        inst.captured_session_id = data.get("captured_session_id")
        code = data.get("exit_code")
        inst.exit_code = code if code is not None else -1  # -1 = was interrupted
        return inst
