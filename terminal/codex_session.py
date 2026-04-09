"""CodexSession — OpenAI Codex CLI wrapper.

Codex CLI reference: https://github.com/openai/codex
Install: npm install -g @openai/codex

Key flags used:
  exec                  Non-interactive subcommand
  --json                Emit JSONL events to stdout
  --sandbox             read-only | workspace-write | danger-full-access
  --full-auto           Convenience alias: workspace-write sandbox + auto approvals
  --dangerously-bypass-approvals-and-sandbox
                        No confirmations, no sandboxing (bypass mode)

Permission mode mapping:
  safe         → --sandbox read-only
  accept_edits → --full-auto  (workspace-write + auto approve)
  bypass       → --dangerously-bypass-approvals-and-sandbox
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


# Maps VibeCLI permission modes → codex exec flags
# Codex has no native plan mode, so "plan" uses read-only sandbox and a
# planning instruction is prepended to the prompt in run().
_PERMISSION_FLAGS: dict[str, list[str]] = {
    "plan":         ["--sandbox", "read-only"],
    "safe":         ["--sandbox", "read-only"],
    "accept_edits": ["--full-auto"],
    "bypass":       ["--dangerously-bypass-approvals-and-sandbox"],
}

_PLAN_PREFIX = (
    "PLANNING MODE — Do NOT write any files or execute any shell commands. "
    "Only analyse, reason, and describe your plan. "
    "If you would normally make edits, describe exactly what changes you would make instead. "
)

_EFFORT_PREFIX: dict[str, str] = {
    "low":  "Be concise. Give a brief, direct answer without extra explanation. ",
    "high": "Think step-by-step and reason thoroughly before answering. "
            "Consider edge cases and alternatives carefully. ",
}



class CodexSession(AgentSession):
    """
    Wraps: codex exec --json [permission flags] "<prompt>"

    With --json, codex emits JSONL events.  We extract message text from
    assistant events and display tool activity lines.  Plain non-JSON lines
    are shown after ANSI stripping.

    Session resumption is not supported by the Codex CLI; each run is
    independent.  The `resume_session_id` parameter is accepted but ignored.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._proc: asyncio.subprocess.Process | None = None

    @staticmethod
    def is_available() -> bool:
        return shutil.which("codex") is not None

    @staticmethod
    def _cli() -> str:
        return "codex"

    async def run(
        self,
        on_line: Callable[[str], None] | None = None,
        on_permission_request: Callable[[dict], None] | None = None,
    ) -> int:
        perm_flags = _PERMISSION_FLAGS.get(self.permission_mode, _PERMISSION_FLAGS["accept_edits"])
        prompt = self.prompt
        if self.permission_mode == "plan":
            prompt = _PLAN_PREFIX + prompt
        prompt = _EFFORT_PREFIX.get(self.effort_mode, "") + prompt
        cmd = ["codex", "exec", "--json"] + perm_flags + self.extra_flags + [prompt]

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
                "[!] `codex` CLI not found — install with: npm install -g @openai/codex",
                on_line,
            )
            self.exit_code = 127
        except Exception as e:
            self._emit(f"[!] Codex session error: {e}", on_line)
            self.exit_code = 1
        return self.exit_code or 0

    def _handle_event(self, event: dict, on_line: Callable[[str], None] | None) -> None:
        """Parse a codex --json event and emit display text.

        Observed event shapes:
          {"type":"thread.started","thread_id":"..."}
          {"type":"turn.started"}
          {"type":"item.completed","item":{"type":"agent_message","text":"..."}}
          {"type":"item.started","item":{"type":"command_execution","command":"...",...}}
          {"type":"item.completed","item":{"type":"command_execution","command":"...","aggregated_output":"...","exit_code":0}}
          {"type":"turn.completed","usage":{...}}
        """
        etype = event.get("type", "")

        # Capture thread/session id
        if not self.captured_session_id:
            sid = event.get("thread_id") or event.get("session_id")
            if sid and isinstance(sid, str):
                self.captured_session_id = sid

        # item.completed — agent messages and finished tool runs
        if etype == "item.completed":
            item = event.get("item") or {}
            itype = item.get("type", "")
            if itype == "agent_message":
                text = item.get("text", "").strip()
                if text:
                    self._emit(text, on_line)
            elif itype == "command_execution":
                output = item.get("aggregated_output", "").strip()
                if output:
                    for line in output.splitlines():
                        stripped = line.strip()
                        if stripped:
                            self._emit(stripped, on_line)
            return

        # item.started — show tool activity indicator
        if etype == "item.started":
            item = event.get("item") or {}
            itype = item.get("type", "")
            if itype == "command_execution":
                cmd = item.get("command", "")[:60]
                self._emit(f"⟳ {cmd}" if cmd else "⟳ running command…", on_line)
            return

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
