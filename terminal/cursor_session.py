"""CursorSession — Cursor `agent` CLI wrapper.

Cursor CLI reference: https://cursor.com/docs/cli
Install: Install the Cursor desktop app (the `agent` binary is bundled)

Key flags used:
  --print / -p              Non-interactive / headless mode
  --trust                   Trust the workspace directory (required in safe mode)
  --force / -f              Trust + allow all commands without per-command approval
  --output-format stream-json  NDJSON event stream for real-time parsing
  --resume [chatId]         Continue a previous conversation

Stream-json event shapes (from live observation):
  system/init:   {"type":"system","subtype":"init","session_id":"..."}
  assistant:     {"type":"assistant","message":{"content":[{"type":"text","text":"..."}]}}
  tool_call:     {"type":"tool_call","subtype":"started","tool_call":{"shellToolCall":{"args":{...},"description":"..."}}}
  tool_call:     {"type":"tool_call","subtype":"completed","tool_call":{"shellToolCall":{...result...}}}
  result:        {"type":"result","result":"...","session_id":"..."}
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


_EFFORT_PREFIX: dict[str, str] = {
    "low":  "Be concise. Give a brief, direct answer without extra explanation. ",
    "high": "Think step-by-step and reason thoroughly before answering. "
            "Consider edge cases and alternatives carefully. ",
}


def _tool_name_from_key(key: str) -> str:
    """Convert 'shellToolCall' → 'Shell', 'editFileToolCall' → 'Edit File'."""
    name = re.sub(r"ToolCall$", "", key)
    name = re.sub(r"([A-Z])", r" \1", name).strip()
    return name.title() if name else "Tool"


# 8 MB readline buffer — agent's tool_call/completed events embed full file
# contents in JSON, easily exceeding asyncio's default 64 KB limit.
_STREAM_LIMIT = 8 * 1024 * 1024


class CursorSession(AgentSession):
    """
    Wraps: agent --print [--trust|--force] --output-format stream-json [--resume id] "<prompt>"

    Permission modes:
      safe         — adds --trust (workspace trusted, no auto-approval of writes)
      accept_edits — adds --force (allow all file edits silently)
      bypass       — adds --force
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
        # plan         → --plan (read-only / planning mode, no writes or exec)
        # safe         → --trust (workspace trust, writes blocked by cli.json deny rules)
        # accept_edits → --force (auto-approve file edits; shell blocked by cli.json)
        # bypass       → --force (allow everything)
        if self.permission_mode == "plan":
            mode_flags = ["--plan", "--trust"]
        elif self.permission_mode in ("accept_edits", "bypass"):
            mode_flags = ["--force"]
        else:
            mode_flags = ["--trust"]

        prompt = _EFFORT_PREFIX.get(self.effort_mode, "") + self.prompt
        cmd = ["agent", "--print", "--output-format", "stream-json"]
        if self.resume_session_id:
            cmd += ["--resume", self.resume_session_id]
        if self.verbose_output:
            cmd += ["--stream-partial-output"]  # stream text deltas in verbose mode
        cmd += mode_flags + self.extra_flags + [prompt]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.project_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=_STREAM_LIMIT,
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

        # Capture session ID from any event (first one wins)
        if not self.captured_session_id:
            sid = event.get("session_id")
            if sid and isinstance(sid, str):
                self.captured_session_id = sid

        # ── assistant: stream text blocks ──────────────────────────────────
        if etype in ("assistant", "message"):
            msg = event.get("message") or {}
            model = msg.get("model", "")
            if model and not self.active_model:
                self.active_model = model
            content = msg.get("content") or []
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "").strip()
                        if text:
                            self._emit(text, on_line)
            return

        # ── tool_call started: show what the agent is doing ────────────────
        if etype == "tool_call" and event.get("subtype") == "started":
            tc        = event.get("tool_call") or {}
            tool_key  = next(iter(tc), "")
            tool_data = tc.get(tool_key) or {}
            args      = tool_data.get("args") or {}
            desc      = tool_data.get("description", "").strip()
            name      = _tool_name_from_key(tool_key)

            if self.verbose_output:
                lines = [f"⟳ {name}"]
                if desc:
                    lines.append(f"  {desc}")
                for k, v in args.items():
                    v_str = str(v)
                    if len(v_str) > 200:
                        v_str = v_str[:200] + "…"
                    lines.append(f"  {k}: {v_str}")
                self._emit("\n".join(lines), on_line)
            elif desc:
                self._emit(f"⟳ {desc[:80]}", on_line)
            else:
                detail = str(
                    args.get("command",
                    args.get("file_path",
                    args.get("path", "")))
                )[:60]
                self._emit(f"⟳ {name}({detail})" if detail else f"⟳ {name}", on_line)
            return

        # ── tool_call completed: show result in verbose mode ──────────────
        if etype == "tool_call" and event.get("subtype") == "completed":
            if self.verbose_output:
                tc        = event.get("tool_call") or {}
                tool_key  = next(iter(tc), "")
                tool_data = tc.get(tool_key) or {}
                name      = _tool_name_from_key(tool_key)
                # Result fields vary by tool — grab output/result/content/error
                result = (
                    tool_data.get("output")
                    or tool_data.get("result")
                    or tool_data.get("content")
                    or tool_data.get("error")
                    or ""
                )
                result_str = str(result).strip()
                if result_str:
                    lines = [f"◀ {name}"]
                    for ln in result_str.splitlines():
                        lines.append(f"  {ln}")
                    self._emit("\n".join(lines), on_line)
            return

        # ── result: update session ID; text duplicates last assistant msg ──
        if etype == "result":
            sid = event.get("session_id")
            if sid:
                self.captured_session_id = sid
            return

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
