"""OpenClawSession — OpenClaw CLI wrapper.

OpenClaw reference: https://github.com/openclaw/openclaw
Install: npm install -g openclaw@latest  (then: openclaw onboard --install-daemon)

Architecture
────────────
OpenClaw is a Gateway-based personal AI assistant.  The Gateway daemon
(ws://127.0.0.1:18789) is the control plane; the `openclaw agent` CLI
sends a message through the Gateway and streams back the response.

Key flags used:
  agent --message "<prompt>"     Non-interactive agent invocation
  --thinking <level>             Reasoning depth:
                                   off | minimal | low | medium | high | xhigh
  --model <provider>/<id>        Model override (reads ~/.openclaw/openclaw.json
                                   when absent)
  --deliver-to <channel>         Optionally echo the response back to a channel
                                   (e.g. "telegram", "discord")

Permission mode → thinking level mapping:
  plan         → minimal   (+ planning prefix injected into prompt)
  safe         → low
  accept_edits → medium
  bypass       → high

The Gateway daemon must be running.  If it is not reachable on
127.0.0.1:<port>, we emit a clear error with start instructions.

Session resumption is not supported; each run is independent.
"""
from __future__ import annotations

import asyncio
import re
import shutil
from typing import Callable

from terminal.agent_session import AgentSession
from core.openclaw_config import (
    OpenClawConfig,
    is_gateway_reachable,
    gateway_port,
)


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[mKHFABCDJG]", "", text)


# Permission mode → --thinking level
_THINKING: dict[str, str] = {
    "plan":         "minimal",
    "safe":         "low",
    "accept_edits": "medium",
    "bypass":       "high",
}

_PLAN_PREFIX = (
    "PLANNING MODE — Do NOT write any files or execute any shell commands. "
    "Only analyse, reason, and describe your plan. "
    "If you would normally make edits, describe exactly what changes "
    "you would make instead. "
)


class OpenClawSession(AgentSession):
    """
    Wraps: openclaw agent --message "<prompt>" --thinking <level> [--model ...] [--deliver-to ...]

    The Gateway daemon must be running before agent calls succeed.
    Start it with: openclaw gateway --port 18789
    Or keep it running via: openclaw onboard --install-daemon
    """

    def __init__(self, deliver_to: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._proc:      asyncio.subprocess.Process | None = None
        self._deliver_to = deliver_to   # optional channel name to echo response to

    @staticmethod
    def is_available() -> bool:
        return shutil.which("openclaw") is not None

    @staticmethod
    def _cli() -> str:
        return "openclaw"

    async def run(
        self,
        on_line: Callable[[str], None] | None = None,
        on_permission_request: Callable[[dict], None] | None = None,
    ) -> int:
        # ── Load config ──────────────────────────────────────────────────
        try:
            cfg = OpenClawConfig.load()
        except Exception:
            cfg = OpenClawConfig()

        port = gateway_port(cfg)

        # ── Gateway health check ─────────────────────────────────────────
        if not is_gateway_reachable(port):
            self._emit(
                f"[!] OpenClaw Gateway is not running on 127.0.0.1:{port}",
                on_line,
            )
            self._emit(
                "    Start it with:  openclaw gateway --port 18789",
                on_line,
            )
            self._emit(
                "    Or run once:    openclaw onboard --install-daemon",
                on_line,
            )
            self.exit_code = 1
            return 1

        # ── Build command ────────────────────────────────────────────────
        thinking = _THINKING.get(self.permission_mode, _THINKING["accept_edits"])
        prompt   = (_PLAN_PREFIX + self.prompt) if self.permission_mode == "plan" else self.prompt

        cmd = [
            "openclaw", "agent",
            "--message", prompt,
            "--thinking", thinking,
        ]

        # Optional model override from config or extra_flags
        if cfg.model:
            cmd += ["--model", cfg.model]

        # Optional channel delivery
        if self._deliver_to:
            cmd += ["--deliver-to", self._deliver_to]

        cmd += self.extra_flags

        # ── Execute ──────────────────────────────────────────────────────
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
                "[!] `openclaw` not found — install with: npm install -g openclaw@latest",
                on_line,
            )
            self._emit(
                "    Then run: openclaw onboard --install-daemon",
                on_line,
            )
            self.exit_code = 127
        except Exception as e:
            self._emit(f"[!] OpenClaw session error: {e}", on_line)
            self.exit_code = 1

        return self.exit_code or 0

    def cancel(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
