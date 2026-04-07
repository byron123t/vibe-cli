"""CursorSession — Cursor cursor-agent CLI wrapper.

Cursor CLI reference: https://cursor.com/docs/cli
Install: curl https://cursor.com/install -fsS | bash
         (or install the Cursor desktop app and enable CLI tools)

Key flags used:
  -p / --print              Non-interactive / headless mode
  --force                   Allow actual file writes (otherwise proposals only)
  --output-format stream-json  NDJSON event stream for real-time parsing
  --resume [thread-id]      Continue a previous conversation
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
    Wraps: cursor-agent -p [--force] --output-format stream-json [--resume id] "<prompt>"

    In 'safe' mode (no --force), Cursor only proposes changes without writing.
    In 'accept_edits' and 'bypass' modes, --force is added to allow writes.

    Stream-json events are parsed for display text.  Unknown event shapes
    fall back to plain-text display after ANSI stripping.

    The `captured_session_id` is set from the first session_id / thread_id
    seen in the output stream, enabling multi-turn via --resume.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def is_available() -> bool:
        return bool(shutil.which("cursor-agent") or shutil.which("cursor"))

    @staticmethod
    def _cli() -> str:
        return "cursor-agent" if shutil.which("cursor-agent") else "cursor"

    async def run(
        self,
        on_line: Callable[[str], None] | None = None,
        on_permission_request: Callable[[dict], None] | None = None,
    ) -> int:
        cli         = self._cli()
        force_flags = ["--force"] if self.permission_mode in ("accept_edits", "bypass") else []
        cmd = [cli, "-p", "--output-format", "stream-json"]
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
                "[!] `cursor-agent` not found — install the Cursor desktop app "
                "and run: cursor --install-cli",
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

        # Capture session/thread ID for resumption
        for key in ("session_id", "thread_id", "id"):
            sid = event.get(key)
            if sid and isinstance(sid, str) and not self.captured_session_id:
                self.captured_session_id = sid
                break

        if etype in ("message", "delta", "content"):
            text = (event.get("text")
                    or event.get("content")
                    or (event.get("delta") or {}).get("text", ""))
            if text:
                self._emit(str(text).strip(), on_line)

        elif etype == "tool_use":
            name   = event.get("name", "?")
            inp    = event.get("input") or {}
            detail = str(inp.get("command", inp.get("file_path", "")))[:60]
            self._emit(f"⟳ {name}({detail})" if detail else f"⟳ {name}", on_line)

        elif etype == "result":
            for key in ("session_id", "thread_id"):
                sid = event.get(key)
                if sid:
                    self.captured_session_id = sid
                    break
            text = event.get("result") or event.get("text", "")
            if text:
                self._emit(str(text).strip(), on_line)

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
