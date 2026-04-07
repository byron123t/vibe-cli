"""ClaudeSession — Anthropic Claude Code CLI wrapper.

Uses --print --output-format stream-json for structured streaming output:
  - session_id capture (for multi-turn --resume)
  - clean text extraction from assistant message blocks

Permissions in --print mode
----------------------------
Claude Code does NOT emit permission_request events in --print mode.
VibeSwipe handles permissions via a PreToolUse HTTP hook: before executing
any tool, Claude POSTs to ApprovalServer which shows a TUI prompt and blocks
until the user approves or denies.  See terminal/approval_server.py.
"""
from __future__ import annotations

import asyncio
import json as _json
import re
from typing import Callable

from terminal.agent_session import AgentSession


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mKHFABCDJG]", "", text)


# Maps VibeSwipe permission modes → claude CLI flags
# "safe" uses no flags — permissions are handled by the PreToolUse HTTP hook.
PERMISSION_FLAGS: dict[str, list[str]] = {
    "safe":         [],
    "accept_edits": ["--permission-mode", "acceptEdits"],
    "bypass":       ["--dangerously-skip-permissions"],
}


class ClaudeSession(AgentSession):
    """
    Wraps `claude --print --verbose --output-format stream-json [--resume id] <prompt>`.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def is_available() -> bool:
        import shutil
        return shutil.which("claude") is not None

    async def run(
        self,
        on_line: Callable[[str], None] | None = None,
        on_permission_request: Callable[[dict], None] | None = None,
    ) -> int:
        perm_flags = PERMISSION_FLAGS.get(self.permission_mode, [])
        cmd = ["claude", "--print", "--verbose", "--output-format", "stream-json"]
        if self.resume_session_id:
            cmd += ["--resume", self.resume_session_id]
        cmd += perm_flags + self.extra_flags + [self.prompt]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.project_path,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert self._proc.stdout is not None
            async for raw_line in self._proc.stdout:
                line = raw_line.decode(errors="replace").rstrip()
                if not line:
                    continue
                if line.startswith("{"):
                    try:
                        event = _json.loads(line)
                        self._handle_event(event, on_line)
                        continue
                    except _json.JSONDecodeError:
                        pass
                cleaned = _strip_ansi(line)
                if cleaned:
                    self._emit(cleaned, on_line)
            await self._proc.wait()
            self.exit_code = self._proc.returncode
        except FileNotFoundError:
            self._emit("[!] `claude` CLI not found — install Claude Code first.", on_line)
            self.exit_code = 127
        except Exception as e:
            self._emit(f"[!] Claude session error: {e}", on_line)
            self.exit_code = 1
        return self.exit_code or 0

    def _handle_event(
        self,
        event: dict,
        on_line: Callable[[str], None] | None,
    ) -> None:
        etype = event.get("type", "")

        if etype == "system" and event.get("subtype") == "init":
            sid = event.get("session_id")
            if sid:
                self.captured_session_id = sid
            return

        if etype == "assistant":
            parts: list[str] = []
            for block in event.get("message", {}).get("content", []):
                btype = block.get("type", "")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        parts.append(text)
                elif btype == "tool_use":
                    name   = block.get("name", "?")
                    inp    = block.get("input", {})
                    detail = inp.get("command", inp.get("file_path", inp.get("path", "")))
                    detail = str(detail)[:80]
                    parts.append(f"⟳ {name}({detail})" if detail else f"⟳ {name}")
            if parts:
                self._emit("\n".join(parts), on_line)
            return

        if etype == "result":
            sid = event.get("session_id")
            if sid:
                self.captured_session_id = sid
            result = event.get("result", "").strip()
            if result:
                self._emit(result, on_line)
            return

    async def approve_permission(self, request_id: str, allow: bool) -> None:
        pass  # Handled by ApprovalServer HTTP hook, not stdin

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
