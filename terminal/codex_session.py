"""CodexSession — OpenAI Codex CLI wrapper.

Codex CLI reference: https://github.com/openai/codex
Install: npm install -g @openai/codex

Key flags used:
  -q / --quiet          Non-interactive mode (no spinner/REPL)
  --approval-mode       suggest | auto-edit | full-auto
    suggest    — read files freely, asks before writes/shell commands
    auto-edit  — reads + applies file edits, asks before shell commands
    full-auto  — reads, writes, and runs shell commands without asking
"""
from __future__ import annotations

import asyncio
import re
import shutil
from typing import Callable

from terminal.agent_session import AgentSession


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mKHFABCDJG]", "", text)


# Maps VibeSwipe permission modes → codex --approval-mode values
_APPROVAL: dict[str, list[str]] = {
    "safe":         ["--approval-mode", "suggest"],
    "accept_edits": ["--approval-mode", "auto-edit"],
    "bypass":       ["--approval-mode", "full-auto"],
}


class CodexSession(AgentSession):
    """
    Wraps: codex -q --approval-mode <mode> "<prompt>"

    Codex streams plain-text output to stdout.  No structured JSON output
    is available, so each non-empty line is displayed as-is after stripping
    ANSI escape codes.

    Session resumption is not supported by the Codex CLI; each run is
    independent.  The `resume_session_id` parameter is accepted but ignored.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def is_available() -> bool:
        return shutil.which("codex") is not None

    async def run(
        self,
        on_line: Callable[[str], None] | None = None,
        on_permission_request: Callable[[dict], None] | None = None,
    ) -> int:
        approval_flags = _APPROVAL.get(self.permission_mode, _APPROVAL["accept_edits"])
        cmd = ["codex", "-q"] + approval_flags + self.extra_flags + [self.prompt]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert self._proc.stdout is not None
            async for raw_line in self._proc.stdout:
                line = _strip_ansi(raw_line.decode(errors="replace").rstrip())
                if line:
                    self._emit(line, on_line)
            await self._proc.wait()
            self.exit_code = self._proc.returncode
        except FileNotFoundError:
            self._emit(
                "[!] `codex` CLI not found — install with: npm install -g @openai/codex",
                on_line,
            )
            self.exit_code = 127
        except Exception as e:
            self._emit(f"[!] Codex session error: {e}", on_line)
            self.exit_code = 1
        return self.exit_code or 0

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
