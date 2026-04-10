"""ClaudeSession — Anthropic Claude Code CLI wrapper.

Uses --print --output-format stream-json for structured streaming output:
  - session_id capture (for multi-turn --resume)
  - clean text extraction from assistant message blocks

Permissions in --print mode
----------------------------
Claude Code does NOT emit permission_request events in --print mode, and
--permission-mode flag behaviour in non-interactive mode is unpredictable
(may auto-approve Bash/network tools in ways the user didn't intend).

Instead, vibe-cli installs a PreToolUse HTTP hook for both "safe" and
"accept_edits" modes.  Every tool call is POST-ed to ApprovalServer which
decides (based on the current permission mode) whether to auto-approve, show
a TUI prompt, or auto-deny.

  safe         — every tool surfaces a TUI PermissionPrompt
  accept_edits — file tools (Read/Write/Edit/…) are silently auto-approved;
                 Bash, WebFetch, WebSearch still show the TUI prompt
  bypass       — --dangerously-skip-permissions, no hook installed

See terminal/approval_server.py and ACCEPT_EDITS_AUTO_APPROVE below.
"""
from __future__ import annotations

import asyncio
import json as _json
import re
from typing import Callable

from terminal.agent_session import AgentSession


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mKHFABCDJG]", "", text)


# Maps vibe-cli permission modes → claude CLI flags.
#
# "safe" and "accept_edits" both use no bypass flags.  All tool-call gating is
# handled by the PreToolUse HTTP hook (ApprovalServer) so we always have an
# explicit decision path rather than relying on --permission-mode behaviour in
# non-interactive (--print) mode, which can be unpredictable.
#
# "accept_edits" auto-approves file-manipulation tools silently inside the
# app's _on_tool_approval_request callback; Bash and network tools still show
# the TUI PermissionPrompt.
#
# "bypass" is the only mode that skips permissions entirely.
PERMISSION_FLAGS: dict[str, list[str]] = {
    "plan":         ["--permission-mode", "plan"],  # read-only / planning; no writes or exec
    "safe":         [],
    "accept_edits": [],                              # hook handles approval, not the CLI flag
    "bypass":       ["--dangerously-skip-permissions"],
}

# Tools auto-approved in "accept_edits" mode.
# These are read/write file operations that are trivially reversible via git.
# Any tool NOT in this set still shows the user a TUI confirmation prompt.
_EFFORT_PREFIX: dict[str, str] = {
    "low":  "Be concise. Give a brief, direct answer without extra explanation. ",
    "high": "Think step-by-step and reason thoroughly before answering. "
            "Consider edge cases and alternatives carefully. ",
}

ACCEPT_EDITS_AUTO_APPROVE: frozenset[str] = frozenset({
    "Read", "Write", "Edit", "MultiEdit",
    "Glob", "Grep", "LS",
    "TodoWrite", "TodoRead",
})


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
        prompt = _EFFORT_PREFIX.get(self.effort_mode, "") + self.prompt
        cmd = ["claude", "--print", "--verbose", "--output-format", "stream-json"]
        if self.resume_session_id:
            cmd += ["--resume", self.resume_session_id]
        if self.model_override:
            cmd += ["--model", self.model_override]
        if self.max_turns is not None:
            cmd += ["--max-turns", str(self.max_turns)]
        if self.max_budget_usd is not None:
            cmd += ["--max-budget-usd", f"{self.max_budget_usd:.2f}"]
        if self.system_prompt:
            cmd += ["--append-system-prompt", self.system_prompt]
        for tool in self.allowed_tools:
            cmd += ["--allowedTools", tool]
        for tool in self.disallowed_tools:
            cmd += ["--disallowedTools", tool]
        cmd += perm_flags + self.extra_flags + [prompt]

        try:
            self._proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=self.project_path,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=8 * 1024 * 1024,  # 8 MB — default 64 KB overflows on large JSON lines
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
            msg = event.get("message", {})
            # Capture model name from the first assistant message that reports it
            model = msg.get("model", "")
            if model and not self.active_model:
                self.active_model = model
            # Accumulate per-turn token usage from each assistant message
            usage = msg.get("usage", {})
            self.output_tokens += usage.get("output_tokens", 0)
            self.input_tokens  += usage.get("input_tokens",  0)
            parts: list[str] = []
            for block in msg.get("content", []):
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
            # The result event carries authoritative session-total usage
            usage = event.get("usage", {})
            if usage:
                self.output_tokens = usage.get("output_tokens", self.output_tokens)
                self.input_tokens  = usage.get("input_tokens",  self.input_tokens)
            result = event.get("result", "").strip()
            if result:
                self._emit(result, on_line)
            return

    async def approve_permission(self, request_id: str, allow: bool) -> None:
        pass  # Handled by ApprovalServer HTTP hook, not stdin

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
