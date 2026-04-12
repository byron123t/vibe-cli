"""ui/app_slash.py — Slash command handlers for VibeCLIApp (_SlashCommandMixin)."""
from __future__ import annotations

import os
import re
import threading
from typing import TYPE_CHECKING

from ui.constants import (
    PERM_LABELS as _PERM_LABELS,
    AGENT_LABELS as _AGENT_LABELS,
    EFFORT_LABELS as _EFFORT_LABELS,
    SLASH_HINTS as _SLASH_HINTS,
    PERM_CYCLE as _PERM_CYCLE,
    AGENT_CYCLE as _AGENT_CYCLE,
    EFFORT_CYCLE as _EFFORT_CYCLE,
)


class _SlashCommandMixin:
    """Mixin that adds slash-command handling to VibeCLIApp."""

    # ------------------------------------------------------------------ slash commands

    def _handle_slash_command(self, raw: str) -> bool:
        """
        Parse and dispatch a /command typed in the prompt bar.

        Returns True  → command was consumed (do NOT forward to agent).
        Returns False → unknown or pass-through (forward to Claude as-is;
                        other agents will get an "unknown command" warning).
        """
        parts = raw.strip().split(None, 1)
        cmd   = parts[0].lower()          # e.g. "/effort"
        arg   = parts[1].strip() if len(parts) > 1 else ""

        _DISPATCH = {
            "/effort":       self._scmd_effort,
            "/agent":        self._scmd_agent,
            "/switch":       self._scmd_agent,
            "/perm":         self._scmd_perm,
            "/permissions":  self._scmd_perm,
            "/model":        self._scmd_model,
            "/budget":       self._scmd_budget,
            "/turns":        self._scmd_turns,
            "/max-turns":    self._scmd_turns,
            "/system":       self._scmd_system,
            "/tools":        self._scmd_tools,
            "/clear":        self._scmd_clear,
            "/compact":      self._scmd_compact,
            "/fork":         self._scmd_fork,
            "/help":         self._scmd_help,
            "/obsidian":     self._scmd_obsidian,
        }

        if cmd in _DISPATCH:
            _DISPATCH[cmd](arg)
            return True

        # Unknown command: pass through to Claude (it may handle /init, /memory, etc.)
        # For other agents, warn and consume.
        if self._agent_type == "claude":
            return False

        self.notify(
            f"Unknown command: {cmd}  ·  type /help for available commands",
            severity="warning", timeout=4,
        )
        return True

    # ── individual command handlers ───────────────────────────────────────────

    def _scmd_effort(self, arg: str) -> None:
        level = arg.lower()
        if level in _EFFORT_CYCLE:
            self._effort_mode = level
            from ui.widgets import StatusBar
            self.query_one("#status-bar", StatusBar).update_effort(level)
            label, _ = _EFFORT_LABELS[level]
            self.notify(f"Effort → {label}", timeout=3)
        elif not arg:
            label, _ = _EFFORT_LABELS[self._effort_mode]
            self.notify(
                f"Current effort: {label}  ·  usage: /effort low|medium|high",
                timeout=4,
            )
        else:
            self.notify(
                f"Unknown effort level: '{arg}'  ·  use: low | medium | high",
                severity="warning", timeout=4,
            )

    def _scmd_agent(self, arg: str) -> None:
        ag = arg.lower()
        if ag in _AGENT_CYCLE:
            old = self._agent_type
            self._agent_type = ag
            from ui.widgets import StatusBar
            sb = self.query_one("#status-bar", StatusBar)
            sb.update_agent(ag)
            sb.clear_openclaw_status()
            label, _ = _AGENT_LABELS[ag]
            self.notify(f"Agent → {label}", timeout=3)
            if ag == "openclaw":
                self._check_openclaw_gateway()
                if self._show_inbox:
                    self._start_gateway_client()
            elif old == "openclaw":
                self._stop_gateway_client()
        elif not arg:
            label, _ = _AGENT_LABELS[self._agent_type]
            opts = " | ".join(_AGENT_CYCLE)
            self.notify(f"Current agent: {label}  ·  /agent {opts}", timeout=4)
        else:
            self.notify(
                f"Unknown agent: '{arg}'  ·  use: {' | '.join(_AGENT_CYCLE)}",
                severity="warning", timeout=4,
            )

    def _scmd_perm(self, arg: str) -> None:
        mode = arg.lower()
        if mode in _PERM_CYCLE:
            self._perm_mode = mode
            from ui.widgets import StatusBar, AgentWidget, PromptBar
            self.query_one("#status-bar", StatusBar).update_perm(mode)
            label, _ = _PERM_LABELS[mode]
            self.notify(f"Permission → {label}", timeout=3)
            # Refresh inline indicators
            active = self._pm.active
            proj = os.path.basename(active.path.rstrip("/")) if active else ""
            try:
                self.query_one("#prompt-bar", PromptBar).update_perm_indicator(mode, proj)
                for widget in self.query(AgentWidget):
                    widget.update_perm_indicator(mode)
            except Exception:
                pass
        elif not arg:
            label, _ = _PERM_LABELS[self._perm_mode]
            opts = " | ".join(_PERM_CYCLE)
            self.notify(f"Current: {label}  ·  /perm {opts}", timeout=4)
        else:
            self.notify(
                f"Unknown permission mode: '{arg}'  ·  use: {' | '.join(_PERM_CYCLE)}",
                severity="warning", timeout=4,
            )

    def _scmd_model(self, arg: str) -> None:
        if arg:
            self._model_override = arg
            self.notify(
                f"Model override → {arg}  ·  applies to next agent run  "
                f"(Claude & OpenClaw)  ·  /model to clear",
                timeout=5,
            )
        else:
            if self._model_override:
                self._model_override = ""
                self.notify("Model override cleared — using agent default.", timeout=3)
            else:
                self.notify(
                    "No model override set.  Usage: /model <provider/id>  "
                    "(e.g. /model anthropic/claude-opus-4-5)",
                    timeout=5,
                )

    def _scmd_budget(self, arg: str) -> None:
        if arg:
            # Accept "$5", "5", "5.00"
            cleaned = arg.lstrip("$").strip()
            try:
                amount = float(cleaned)
                self._max_budget_usd = amount
                self._refresh_limits_bar()
                self.notify(
                    f"Budget cap → ${amount:.2f} per session  (Claude only)  "
                    "·  /budget to clear",
                    timeout=4,
                )
            except ValueError:
                self.notify(
                    f"Invalid amount: '{arg}'  ·  usage: /budget 5.00",
                    severity="warning", timeout=4,
                )
        else:
            if self._max_budget_usd is not None:
                self._max_budget_usd = None
                self._refresh_limits_bar()
                self.notify("Budget cap cleared.", timeout=3)
            else:
                self.notify(
                    "No budget cap set.  Usage: /budget <amount>  e.g. /budget 2.50",
                    timeout=4,
                )

    def _scmd_turns(self, arg: str) -> None:
        if arg:
            try:
                n = int(arg)
                if n < 1:
                    raise ValueError
                self._max_turns = n
                self._refresh_limits_bar()
                agent_note = "(Claude: max turns · Codex: attempts 1-4)"
                self.notify(f"Max turns → {n}  {agent_note}  ·  /turns to clear", timeout=4)
            except ValueError:
                self.notify(
                    f"Invalid number: '{arg}'  ·  usage: /turns 10",
                    severity="warning", timeout=4,
                )
        else:
            if self._max_turns is not None:
                self._max_turns = None
                self._refresh_limits_bar()
                self.notify("Max turns limit cleared.", timeout=3)
            else:
                self.notify(
                    "No turn limit set.  Usage: /turns <n>  e.g. /turns 20",
                    timeout=4,
                )

    def _scmd_system(self, arg: str) -> None:
        if arg:
            self._system_prompt = arg
            self._refresh_limits_bar()
            preview = arg[:60] + ("…" if len(arg) > 60 else "")
            self.notify(
                f"System prompt → \"{preview}\"  "
                "(appended to each session, Claude only)  ·  /system to clear",
                timeout=5,
            )
        else:
            if self._system_prompt:
                self._system_prompt = ""
                self._refresh_limits_bar()
                self.notify("System prompt cleared.", timeout=3)
            else:
                self.notify(
                    "No system prompt set.  Usage: /system <text>",
                    timeout=4,
                )

    def _scmd_tools(self, arg: str) -> None:
        """
        /tools allow <pattern> [pattern …]   — add to allowed tools list
        /tools deny  <pattern> [pattern …]   — add to disallowed tools list
        /tools clear                          — clear both lists
        /tools                                — show current lists
        """
        parts = arg.split(None, 1)
        sub   = parts[0].lower() if parts else ""
        rest  = parts[1].strip() if len(parts) > 1 else ""

        if sub == "allow":
            if not rest:
                self.notify("Usage: /tools allow <pattern>  e.g. /tools allow Bash(git*)", timeout=4)
                return
            new = [p.strip() for p in rest.split(",") if p.strip()]
            self._allowed_tools = list(dict.fromkeys(self._allowed_tools + new))
            self._refresh_limits_bar()
            self.notify(f"Allowed tools: {', '.join(self._allowed_tools)}", timeout=4)

        elif sub == "deny":
            if not rest:
                self.notify("Usage: /tools deny <pattern>  e.g. /tools deny Bash(*)", timeout=4)
                return
            new = [p.strip() for p in rest.split(",") if p.strip()]
            self._disallowed_tools = list(dict.fromkeys(self._disallowed_tools + new))
            self._refresh_limits_bar()
            self.notify(f"Disallowed tools: {', '.join(self._disallowed_tools)}", timeout=4)

        elif sub == "clear":
            self._allowed_tools    = []
            self._disallowed_tools = []
            self._refresh_limits_bar()
            self.notify("Tool lists cleared.", timeout=3)

        elif sub == "remove":
            pattern = rest.strip()
            if pattern in self._allowed_tools:
                self._allowed_tools.remove(pattern)
            if pattern in self._disallowed_tools:
                self._disallowed_tools.remove(pattern)
            self._refresh_limits_bar()
            self.notify(f"Removed '{pattern}' from tool lists.", timeout=3)

        else:
            allow_str = ", ".join(self._allowed_tools)  or "none"
            deny_str  = ", ".join(self._disallowed_tools) or "none"
            self.notify(
                f"Allowed: {allow_str}\nDenied: {deny_str}\n"
                "Usage: /tools allow|deny|remove|clear <pattern>",
                timeout=6,
            )

    def _scmd_clear(self, _arg: str) -> None:
        """Clear agent panel for non-Claude agents; pass through for Claude."""
        from ui.widgets import AgentPanel
        if self._agent_type == "claude":
            # Fall through to Claude CLI — it handles /clear natively
            # Re-dispatch as a real agent prompt (bypass slash-command detection)
            active = self._pm.active
            if active:
                session = self._make_session("/clear", active.path)
                self.query_one("#agent-panel", AgentPanel).add_agent(
                    session, vault=self._vault, agent_type="claude"
                )
                self._apply_layout()
        else:
            self.query_one("#agent-panel", AgentPanel).clear_active()
            self.notify("Panel cleared.", timeout=2)

    def _scmd_compact(self, _arg: str) -> None:
        """Compact for Claude (pass through); clear panel for other agents."""
        from ui.widgets import AgentPanel
        if self._agent_type == "claude":
            active = self._pm.active
            if active:
                session = self._make_session("/compact", active.path)
                self.query_one("#agent-panel", AgentPanel).add_agent(
                    session, vault=self._vault, agent_type="claude"
                )
                self._apply_layout()
        else:
            self.query_one("#agent-panel", AgentPanel).clear_active()
            self.notify("History compacted.", timeout=2)

    def _scmd_fork(self, arg: str) -> None:
        """
        Fork the current conversation.

        Claude:  passes /fork [arg] natively to the CLI.
        Others:  launches a new agent prefixed with the last agent's output
                 as context, plus the user's arg as the new instruction.
        """
        from ui.widgets import AgentPanel
        active = self._pm.active
        if active is None:
            self.notify("No active project.", severity="warning", timeout=3)
            return

        if self._agent_type == "claude":
            prompt = f"/fork {arg}".strip()
            session = self._make_session(prompt, active.path)
            self.query_one("#agent-panel", AgentPanel).add_agent(
                session, vault=self._vault, agent_type="claude"
            )
            self._apply_layout()
            return

        # Non-Claude: inject previous output as context
        panel   = self.query_one("#agent-panel", AgentPanel)
        context = panel.last_agent_context(n=20)
        if context:
            ctx_text  = "\n".join(context)
            fork_prompt = (
                "[Forked from previous agent output]\n"
                f"{ctx_text}\n\n"
                f"{arg}" if arg else
                "[Forked from previous agent output]\n"
                f"{ctx_text}"
            )
        else:
            fork_prompt = arg or "Continue from where we left off."

        session = self._make_session(fork_prompt, active.path)
        panel.add_agent(session, vault=self._vault, agent_type=self._agent_type)
        self._apply_layout()
        self.notify("Forked → new agent with previous context.", timeout=3)

    def _scmd_help(self, _arg: str) -> None:
        lines = [
            "/effort  [low|medium|high]              set reasoning depth",
            "/agent   [claude|codex|cursor|openclaw] switch agent",
            "/perm    [plan|safe|accept_edits|bypass] set permissions",
            "/model   [provider/id]                  override model",
            "/budget  [amount]                       USD spending cap (Claude)",
            "/turns   [n]                            max turns/attempts",
            "/system  [text]                         append to system prompt",
            "/tools   allow|deny|remove|clear <pat>  tool access lists",
            "/fork    [instruction]                  fork with context",
            "/clear                                  clear panel",
            "/compact                                compact history",
            "/obsidian [path]                        connect Obsidian vault",
            "/help                                   show this message",
            "Claude also accepts: /init /memory /config /review …",
        ]
        self.notify("\n".join(lines), title="Slash commands", timeout=14)

    def _scmd_obsidian(self, arg: str) -> None:
        """
        /obsidian <path>   — attach an Obsidian vault at <path> and persist it
        /obsidian          — show current vault path (or hint if not set)
        /obsidian clear    — detach the current vault
        """
        import json as _json
        from memory.obsidian import ObsidianVault, ObsidianLinker

        arg = arg.strip()

        if not arg:
            if self._obsidian_vault_path:
                self.notify(
                    f"Obsidian vault: {self._obsidian_vault_path}  ·  "
                    "[bold]O[/bold] to toggle panel  ·  /obsidian clear to detach",
                    timeout=6,
                )
            else:
                self.notify(
                    "No Obsidian vault connected.  Usage: /obsidian <path>",
                    timeout=5,
                )
            return

        if arg == "clear":
            self._obsidian_vault_path = ""
            self._obsidian_vault      = None
            self._obsidian_linker     = None
            self._show_obsidian       = False
            self._apply_layout()
            self._persist_obsidian_config("")
            self.notify("Obsidian vault detached.", timeout=3)
            return

        # Set / update vault path
        path = os.path.expanduser(arg)
        if not os.path.isdir(path):
            self.notify(f"Not a directory: {path}", severity="error", timeout=5)
            return

        self._obsidian_vault_path = path
        self._obsidian_vault      = ObsidianVault(path)
        if self._obsidian_linker is None:
            self._obsidian_linker = ObsidianLinker(self._vault)

        self._persist_obsidian_config(path)
        self.notify(
            f"Obsidian vault connected: {path}  ·  press [bold]O[/bold] to open",
            timeout=5,
        )
        # Auto-open the panel
        self._show_obsidian = True
        self._show_graph    = False
        self._apply_layout()
