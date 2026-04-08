"""CursorSession — Cursor `agent` CLI wrapper.

Cursor CLI reference: https://cursor.com/docs/cli
Install: Install the Cursor desktop app (the `agent` binary is bundled)

Key flags used:
  --print / -p              Non-interactive / headless mode
  --force / -f              Allow all commands without per-command approval
  --output-format stream-json  NDJSON event stream for real-time parsing
  --resume [chatId]         Continue a previous conversation
"""
from __future__ import annotations

import asyncio
import json as _json
import re
import shutil
from typing import Callable

from terminal.agent_session import AgentSession


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mKHFABCDJG]", "", text)


class CursorSession(AgentSession):
    """
    Wraps: cursor agent --print [--force] --output-format stream-json [--resume id] "<prompt>"

    In 'safe' mode (no --force), Cursor only proposes changes without writing.
    In 'accept_edits' and 'bypass' modes, --force is added to allow writes.

    Stream-json events are parsed for display text.  Unknown event shapes
    fall back to plain-text display after ANSI stripping.

    The `captured_session_id` is set from the first session_id seen in the
    output stream, enabling multi-turn via --resume.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def is_available() -> bool:
        return shutil.which("agent") is not None

    @staticmethod
    def _cli() -> str:
        return "agent"

    async def run(
        self,
        on_line: Callable[[str], None] | None = None,
        on_permission_request: Callable[[dict], None] | None = None,
    ) -> int:
        force_flags = ["--force"] if self.permission_mode in ("accept_edits", "bypass") else []
        cmd = ["agent", "--print", "--output-format", "stream-json"]
        if self.resume_session_id:
            cmd += ["--resume", self.resume_session_id]
        cmd += force_flags + self.extra_flags + [self.prompt]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.project_path,
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
            self._emit(
                "[!] `agent` not found — install the Cursor desktop app",
                on_line,
            )
            self.exit_code = 127
        except Exception as e:
            self._emit(f"[!] Cursor session error: {e}", on_line)
            self.exit_code = 1
        return self.exit_code or 0

    def _handle_event(self, event: dict, on_line: Callable[[str], None] | None) -> None:
        """Parse a Cursor stream-json event and emit display text."""
        etype = event.get("type", "")

        # Capture session ID for resumption (from any event)
        if not self.captured_session_id:
            sid = event.get("session_id")
            if sid and isinstance(sid, str):
                self.captured_session_id = sid

        if etype in ("assistant", "message", "delta", "content"):
            # assistant events: message.content is a list of content blocks
            msg = event.get("message") or {}
            content = msg.get("content") or []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            self._emit(text, on_line)
            else:
                text = (event.get("text")
                        or event.get("content")
                        or (event.get("delta") or {}).get("text", ""))
                if text:
                    self._emit(str(text).strip(), on_line)

        elif etype == "tool_call":
            subtype = event.get("subtype", "")
            if subtype == "started":
                tc   = event.get("tool_call") or {}
                name = tc.get("name", "?")
                inp  = tc.get("input") or {}
                detail = str(inp.get("command", inp.get("file_path", "")))[:60]
                self._emit(f"⟳ {name}({detail})" if detail else f"⟳ {name}", on_line)

        elif etype == "result":
            sid = event.get("session_id")
            if sid:
                self.captured_session_id = sid
            text = event.get("result") or event.get("text", "")
            if text:
                self._emit(str(text).strip(), on_line)

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
