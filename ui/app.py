"""VibeCLI TUI — modal, keyboard-first multi-project Claude Code interface.

Modes
─────
  Command mode  (default)  — single key shortcuts, no typing
  Prompt mode              — typing a prompt for a new agent  (n or Enter)

Command mode keys
─────────────────
  ]  /  [         next / prev project
  1 … 9           autofill prompt with a suggestion or preset shortcut
  n  or  Enter    open prompt to start a new agent
  x               cancel last running agent
  d               detach last agent widget
  j / k           scroll agent list down / up
  e               toggle editor panel
  f               toggle file browser
  m               toggle memory / knowledge graph
  t               toggle terminal panel
  r               open reattach menu
  A               cycle agent type (Claude → Codex → Cursor → OpenClaw)
  E               cycle effort level
  P               cycle permission mode
  ctrl+p          open command palette
  q               quit
  Escape / Backspace / ,   back to command mode

Prompt mode  (Input focused)
─────────────────────────────
  type freely   builds the prompt
  Tab           cycle through suggestions
  Enter         submit → launch agent
  Escape / ,    back to command mode  (comma only when Input is empty)

Slash commands (type in any prompt or reply input)
──────────────────────────────────────────────────
  /agent <type>     switch agent type
  /model <id>       override model
  /effort <level>   set effort (low/medium/high)
  /perm <mode>      set permission mode
  /budget <$>       set max spend per run
  /turns <n>        set max turns per run
  /system <text>    set system prompt
  /tools            list or toggle allowed tools
  /compact          compact session memory
  /fork             fork current agent output as new prompt
  /clear            clear agents for this project
  /obsidian <path>  connect external Obsidian vault
  /help             show slash command reference
"""
from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Callable, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Container, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import (
    Button, DirectoryTree, Footer, Input, Label, RichLog, Static, TextArea, Tree,
)
from textual import work, on

from core.project_manager import Project, ProjectManager
from core.session_store import SessionStore, session_path_for_vault
from terminal.agent_session import AgentSession
from terminal.claude_session import ClaudeSession, PERMISSION_FLAGS
from terminal.codex_session import CodexSession
from terminal.cursor_session import CursorSession
from terminal.openclaw_session import OpenClawSession
from terminal.pty_widget import PTYWidget
from terminal.approval_server import ApprovalServer
from claude.suggestion_engine import PromptSuggestionEngine
from claude.sdk_client import ClaudeSDKClient
from claude.profile_analyzer import ProfileAnalyzer
from graph.personalization_graph import PersonalizationGraph
from memory.vault import MemoryVault
from memory.moc import MOCManager
from memory.run_log import RunLogger
from memory.user_profile import UserProfile
from memory.linter import VaultLinter
from memory.linker import Linker
from memory.compactor import Compactor
from memory.brain_importer import BrainImporter
from memory.obsidian import ObsidianVault, ObsidianLinker
from core.openclaw_gateway import GatewayClient, ChannelMessage, DeviceEvent, make_client

from ui.themes import CUSTOM_THEMES as _CUSTOM_THEMES, APP_TO_PYGMENTS_THEME as _APP_TO_PYGMENTS_THEME
from ui.linting import (
    LintIssue,
    lint_file as _lint_file,
    LINTABLE_EXTS as _LINTABLE_EXTS,
    language_for as _language_for,
    set_ta_language as _set_ta_language,
)
from ui.constants import (
    AGENT_DISPLAY as _AGENT_DISPLAY,
    SLASH_HINTS as _SLASH_HINTS,
    slash_hint_text as _slash_hint_text,
    AUDIO_EXTS as _AUDIO_EXTS,
    PERM_LABELS as _PERM_LABELS,
    PERM_CYCLE as _PERM_CYCLE,
    PERM_INDICATOR_NAMES as _PERM_INDICATOR_NAMES,
    perm_indicator_text as _perm_indicator_text,
    AGENT_LABELS as _AGENT_LABELS,
    AGENT_CYCLE as _AGENT_CYCLE,
    EFFORT_LABELS as _EFFORT_LABELS,
    EFFORT_CYCLE as _EFFORT_CYCLE,
)
from ui.screens import (
    DirectoryPickerScreen,
    BrainImportScreen,
    DetachMenuScreen,
    _ObsidianPathScreen,
    CommandPaletteScreen,
)
from ui.widgets import (
    PromptSubmitted,
    CommandDetected,
    AgentLog,
    SelectableLog,
    AgentMemoryWidget,
    PermissionPrompt,
    AgentWidget,
    AgentPanel,
    TerminalPanel,
    FileBrowserPanel,
    EditorPanel,
    GraphPane,
    ProjectTabBar,
    PromptBar,
    OpenClawInboxPanel,
    _ObsidianTree,
    ObsidianPanel,
    StatusBar,
    ShortcutsBar,
    _audio_annotation_path,
)
from ui.app_slash import _SlashCommandMixin


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class VibeCLIApp(_SlashCommandMixin, App[None]):

    CSS = """
    Screen { layout: vertical; }
    #main-row  { layout: horizontal; height: 1fr; }
    #right-col { layout: vertical; width: 1fr; height: 1fr; }
    AgentPanel    { height: 1fr; }
    TerminalPanel { height: 20; }
    """

    # Single-key bindings — only active in command mode (Input/TextArea capture when focused)
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("]",     "next_project",     "Next »"),
        Binding("[",     "prev_project",     "« Prev"),
        Binding("n",     "focus_prompt",     "New Agent"),
        Binding("enter", "focus_prompt",     "New Agent",  show=False),
        Binding("o",     "open_project",     "Open Project"),
        Binding("x",     "cancel_agent",     "Cancel"),
        Binding("d",     "detach_agent",     "Detach"),
        Binding("j",     "scroll_down",      "↓"),
        Binding("k",     "scroll_up",        "↑"),
        Binding("f",     "toggle_files",     "Files"),
        Binding("e",     "toggle_editor",    "Editor"),
        Binding("m",     "toggle_graph",     "Memory"),
        Binding("t",     "toggle_terminal",  "Terminal"),
        Binding("ctrl+t","toggle_terminal",  "Terminal", show=False),
        Binding("ctrl+p","open_palette",     "Command Palette", show=False),
        Binding("r",     "reattach_menu",    "Reattach"),
        Binding("c",     "toggle_inbox",     "Channels"),
        Binding("O",     "toggle_obsidian",  "Obsidian", show=False),
        Binding("E",     "cycle_effort",        "Effort"),
        Binding("B",     "import_brain",        "Import Brain"),
        Binding("G",              "toggle_git_commit",     "Git Auto-Commit",             show=False),
        Binding("V",              "toggle_verbose_default","Verbose Output (new agents)", show=False),
        Binding("ctrl+o",         "toggle_agent_verbose",  "Expand/Collapse Output",      show=False),
        Binding("ctrl+backslash", "cycle_permissions",     "Cycle Permission Mode",        show=False),
        Binding("s",     "save_file",         "Save", show=False),
        Binding("escape","exit_mode",        "Back", show=False),
        Binding("q",     "quit",             "Quit"),
    ]

    def __init__(self, config: dict, *, config_path: str | None = None) -> None:
        super().__init__()
        self._config = config
        # Same file main.py loaded — required so theme and other prefs persist to the right config.json
        _default_cfg = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
        self._config_path = os.path.abspath(config_path) if config_path else os.path.abspath(_default_cfg)

        self._pm = ProjectManager()

        vault_root   = config.get("vault", {}).get("root", "vault")
        self._vault  = MemoryVault(vault_root)
        self._session_store = SessionStore(session_path_for_vault(self._vault.root))

        pers_path    = os.path.join(vault_root, "user", "personalization_graph.json")
        self._pers   = PersonalizationGraph(pers_path)
        self._sugg   = PromptSuggestionEngine(self._pers)

        self._auto_commit    = config.get("git", {}).get("auto_commit", False)
        self._commit_prefix  = config.get("git", {}).get("commit_message_prefix", "[VibeCLI] ")

        # Agent type: "claude" | "codex" | "cursor"
        self._agent_type: str = config.get("agent", {}).get("type", "claude")

        # Permission mode: "safe" | "accept_edits" | "bypass"
        self._perm_mode: str = config.get("claude", {}).get("permission_mode", "accept_edits")

        # Effort mode: "low" | "medium" | "high"
        self._effort_mode: str = config.get("agent", {}).get("effort", "medium")

        # Optional model override set via /model command (empty = use agent default)
        self._model_override: str = ""

        # Session-scoped limits / overrides (set via slash commands)
        self._max_turns:       int | None   = None
        self._max_budget_usd:  float | None = None
        self._system_prompt:   str          = ""
        self._allowed_tools:   list[str]    = []
        self._disallowed_tools: list[str]   = []

        # SDK client, profile analyzer, and memory infrastructure
        self._sdk              = ClaudeSDKClient(config)
        self._profile_analyzer = ProfileAnalyzer(self._sdk)
        self._user_profile     = UserProfile(self._vault)
        self._moc              = MOCManager(self._vault)
        self._run_logger       = RunLogger(self._vault, self._moc)

        # Vault compactor — merges redundant run logs after each run
        self._compactor        = Compactor(self._vault, self._moc)

        # PreToolUse HTTP hook server (used in "safe" permission mode)
        self._approval_server  = ApprovalServer(self._on_tool_approval_request)

        # Verbose output default: new agents open with expanded log height
        self._verbose_default: bool = False

        # Open-project mode: next prompt submission opens a project instead of running agent
        self._open_project_mode = False

        self._show_files    = False
        self._show_editor   = False
        self._show_graph    = False
        self._show_terminal = False
        self._show_inbox    = False
        self._last_command: str = ""
        self._detached: dict[str, list[dict]] = {}  # project_path → list of detached agent states

        # Manual prompt shortcuts for keys [6]-[0] — 5 slots, loaded from vault
        self._manual_shortcuts: list[str] = self._load_manual_shortcuts(vault_root)

        # UI theme — persisted to config.json under ui.theme
        self._ui_theme: str = config.get("ui", {}).get("theme", "textual-dark")

        # Obsidian integration (opt-in via /obsidian <path> or config)
        self._obsidian_vault_path: str = config.get("obsidian", {}).get("vault_path", "")
        self._show_obsidian: bool = False
        if self._obsidian_vault_path:
            self._obsidian_vault:  ObsidianVault | None  = ObsidianVault(self._obsidian_vault_path)
            self._obsidian_linker: ObsidianLinker | None = ObsidianLinker(self._vault)
        else:
            self._obsidian_vault  = None
            self._obsidian_linker = None

        # Prompt history shared across PromptBar and all agent reply inputs.
        # Newest entry is at the end.  Per-input browse state is tracked by
        # input widget ID → {"idx": int, "saved": str}
        self._prompt_history: list[str] = []
        self._hist_browse: dict[str, dict] = {}

        # OpenClaw Gateway client and asyncio task
        self._gateway_client: GatewayClient | None = None
        self._gateway_task:   asyncio.Task | None   = None

    # ------------------------------------------------------------------ compose

    def compose(self) -> ComposeResult:
        yield ProjectTabBar(self._pm.projects, self._pm.active_idx, id="tab-bar")
        yield StatusBar(id="status-bar")
        with Horizontal(id="main-row"):
            yield FileBrowserPanel(id="file-browser")
            yield EditorPanel(id="editor-panel")
            with Vertical(id="right-col"):
                yield AgentPanel(id="agent-panel")
                yield TerminalPanel(id="terminal-panel")
            yield GraphPane(self._vault, id="graph-pane")
            yield OpenClawInboxPanel(id="inbox-panel")
            yield ObsidianPanel(id="obsidian-panel")
        yield PromptBar(id="prompt-bar")
        yield ShortcutsBar(id="shortcuts-bar")

    async def on_mount(self) -> None:
        # Register custom themes before session restore so they're available immediately
        for _t in _CUSTOM_THEMES:
            try:
                self.register_theme(_t)
            except Exception:
                pass

        # Re-mount any SSH projects that were saved in projects.json
        self._remount_ssh_projects()

        # Start the PreToolUse HTTP hook server (used in safe permission mode).
        # If the local bind fails, keep the app running and surface a warning.
        try:
            await self._approval_server.start()
        except Exception as exc:
            self.notify(
                f"Approval server unavailable: {exc}",
                severity="warning",
                timeout=6,
            )

        # Start in command mode — agent panel holds focus
        self.query_one("#file-browser").display    = False
        self.query_one("#editor-panel").display   = False
        self.query_one("#graph-pane").display     = False
        self.query_one("#terminal-panel").display = False
        self.query_one("#inbox-panel").display    = False
        self.query_one("#obsidian-panel").display = False

        active = self._pm.active
        if active:
            self.query_one("#agent-panel",    AgentPanel).switch_project(active.name)
            self.query_one("#terminal-panel", TerminalPanel).switch_project(active.name, active.path)
            self._load_active_file(active)
            self.query_one("#file-browser",   FileBrowserPanel).set_root(active.path)
            self.query_one("#status-bar",     StatusBar).update_project(active.name)

        self.query_one("#status-bar", StatusBar).update_agent(self._agent_type)
        self.query_one("#status-bar", StatusBar).update_perm(self._perm_mode)
        self.query_one("#status-bar", StatusBar).update_effort(self._effort_mode)
        self._refresh_limits_bar()
        proj_name = os.path.basename(active.path.rstrip("/")) if active else ""
        self.query_one("#prompt-bar", PromptBar).update_perm_indicator(self._perm_mode, proj_name)

        # If OpenClaw is the active agent, check gateway status in background
        if self._agent_type == "openclaw":
            self._check_openclaw_gateway()

        # Restore previous session (agents, layout flags, active project)
        saved = self._session_store.load()
        if saved:
            self._restore_session(saved)
        else:
            self._apply_layout()

        self._refresh_suggestions()
        self.query_one("#prompt-bar", PromptBar).manual_shortcuts = list(self._manual_shortcuts)

        # Apply the restored session/config theme after the first render cycle so
        # Textual's own CSS initialisation doesn't overwrite it.
        self.call_after_refresh(self._apply_persisted_theme)

        # Focus the agent panel → command mode
        self.query_one("#agent-panel", AgentPanel).focus()

        if not self._pm.projects:
            self.notify(
                "No projects yet.  Press [bold]o[/bold] to open a project directory.",
                title="VibeCLI", timeout=7,
            )
        else:
            restored_count = sum(
                len(saved.get("projects", {}).get(p.path, {}).get("agents", []))
                for p in self._pm.projects
            ) if saved else 0
            suffix = f"  · {restored_count} agent(s) restored" if restored_count else ""
            self.notify(
                f"{self._pm.active.name} — [bold]n[/bold]=new agent  "
                f"[bold]⇧A[/bold]=cycle agent  [bold]o[/bold]=open project  [bold]⇧P[/bold]=permissions{suffix}",
                timeout=5,
            )

    # ------------------------------------------------------------------ key handling

    def on_key(self, event: Key) -> None:
        """Central key handler for command-mode shortcuts and mode exits."""
        key      = event.key
        char     = event.character or ""
        focused  = self.focused
        in_input = isinstance(focused, Input)
        in_edit  = isinstance(focused, TextArea) and not getattr(focused, "read_only", True)
        in_pty   = isinstance(focused, PTYWidget)

        # ── PTY focused: PTYWidget.on_key routes keys to the shell and stops
        #    them so no command shortcuts fire.  ctrl+t is NOT stopped by
        #    PTYWidget, so it bubbles up and hits the Binding("ctrl+t",
        #    "toggle_terminal") binding in BINDINGS — no extra handling needed.
        if in_pty:
            return

        # ── Escape: exit any mode ──────────────────────────────────────────
        if key == "escape":
            if in_input:
                self._exit_to_command()
                event.stop()
                return
            if in_edit:
                # Auto-save on escape (both regular files and audio annotation)
                ep = self.query_one("#editor-panel", EditorPanel)
                ep.save()
                # For non-audio: switch to colorized Rich Syntax view
                if not ep._audio_mode:
                    ep.switch_to_view_mode()
                self._exit_to_command()
                event.stop()
                return

        # ── Backspace / comma: back to command mode (not while typing) ─────
        if key == "backspace" and not in_input and not in_edit:
            self._exit_to_command()
            event.stop()
            return
        if key == "comma" and not in_input and not in_edit:
            self._exit_to_command()
            event.stop()
            return

        # ── ctrl+6–0 (while typing): save current text as manual shortcut ─
        if in_input and key in PromptBar._CTRL_MANUAL:
            slot = PromptBar._CTRL_MANUAL[key]
            text = self.query_one("#prompt-bar", PromptBar).current_input_text().strip()
            if text:
                self._save_manual_shortcut(slot, text)
                display_key = PromptBar._MANUAL_KEYS[slot]
                self.notify(f"Shortcut [{display_key}] saved.", timeout=2)
            event.stop()
            return

        # ── up/down (while typing): browse prompt history ──────────────────
        if in_input and key in ("up", "down") and isinstance(focused, Input):
            iid = focused.id or ""
            if iid == "pb-input" or iid.startswith("agent-reply-"):
                self._browse_input_history(key, focused)
                event.prevent_default()
                event.stop()
                return

        # All remaining shortcuts only apply in command mode
        if in_input or in_edit:
            return

        # ── p: play audio when editor is open on an audio file ─────────────
        if char == "p" and self._show_editor:
            ep = self.query_one("#editor-panel", EditorPanel)
            if ep.try_play_audio():
                event.stop()
                return

        # ── 1–5: fill auto suggestion into prompt ─────────────────────────
        if key in "12345":
            pb = self.query_one("#prompt-bar", PromptBar)
            pb.fill_suggestion(int(key) - 1)
            event.stop()
            return

        # ── 6–0: fill manual shortcut into prompt ─────────────────────────
        if key in "67890":
            pb  = self.query_one("#prompt-bar", PromptBar)
            # key→slot: 6→0, 7→1, 8→2, 9→3, 0→4
            slot = (int(key) - 6) if key != "0" else 4
            pb.fill_manual(slot)
            event.stop()
            return

        # ── A (shift+a): cycle agent type (Claude → Codex → Cursor) ────────
        if char == "A":
            self._cycle_agent_type()
            event.stop()
            return

        # ── R (shift+r): run last detected shell command ──────────────────
        if char == "R":
            self.action_run_last_command()
            event.stop()
            return

        # ── P (shift+p): cycle permission mode ────────────────────────────
        if char == "P":
            self._cycle_permissions()
            event.stop()
            return

        # ── E (shift+e): cycle effort level ──────────────────────────────
        if char == "E":
            self.action_cycle_effort()
            event.stop()
            return

        # ── B (shift+b): import brain/memory folder ───────────────────────
        if char == "B":
            self.action_import_brain()
            event.stop()
            return

    # ------------------------------------------------------------------ actions

    # ------------------------------------------------------------------ helpers

    def _exit_to_command(self) -> None:
        """Return keyboard focus to the agent panel (command mode)."""
        self._open_project_mode = False
        pb = self.query_one("#prompt-bar", PromptBar)
        pb.query_one("#pb-input", Input).placeholder = (
            "› n or Enter to focus · type prompt · Tab=cycle · 1-4=fill suggestion"
        )
        self.query_one("#agent-panel", AgentPanel).focus()

    # ------------------------------------------------------------------ input history

    def _history_add(self, text: str) -> None:
        """Append *text* to the shared prompt history (dedup consecutive entries)."""
        t = text.strip()
        if not t:
            return
        if self._prompt_history and self._prompt_history[-1] == t:
            return
        self._prompt_history.append(t)

    def _browse_input_history(self, key: str, inp: "Input") -> None:
        """Move through history for *inp* based on *key* ('up' or 'down')."""
        iid = inp.id or ""
        state = self._hist_browse.setdefault(iid, {"idx": -1, "saved": ""})
        hist  = self._prompt_history

        if key == "up":
            if not hist:
                return
            if state["idx"] == -1:
                state["saved"] = inp.value
                state["idx"]   = len(hist) - 1
            elif state["idx"] > 0:
                state["idx"] -= 1
            # else: already at oldest — stay there
            inp.value = hist[state["idx"]]
            inp.action_end()

        elif key == "down":
            if state["idx"] == -1:
                return   # not browsing
            if state["idx"] < len(hist) - 1:
                state["idx"] += 1
                inp.value = hist[state["idx"]]
                inp.action_end()
            else:
                # Past newest → restore what was typed before browsing
                state["idx"] = -1
                inp.value    = state["saved"]
                inp.action_end()

    def _cycle_agent_type(self) -> None:
        idx = _AGENT_CYCLE.index(self._agent_type) if self._agent_type in _AGENT_CYCLE else 0
        self._agent_type = _AGENT_CYCLE[(idx + 1) % len(_AGENT_CYCLE)]
        sb = self.query_one("#status-bar", StatusBar)
        sb.update_agent(self._agent_type)
        sb.clear_openclaw_status()
        label, _ = _AGENT_LABELS[self._agent_type]

        # Check availability
        available = {
            "claude":   ClaudeSession.is_available(),
            "codex":    CodexSession.is_available(),
            "cursor":   CursorSession.is_available(),
            "openclaw": OpenClawSession.is_available(),
        }
        if not available.get(self._agent_type, True):
            self.notify(
                f"Agent → {label}  [dim](not installed — will show install hint on run)[/dim]",
                timeout=4,
            )
        else:
            self.notify(f"Agent → {label}", timeout=3)

        # For OpenClaw: check gateway in background and update status bar;
        # also start the gateway client if the inbox panel is already open.
        if self._agent_type == "openclaw":
            self._check_openclaw_gateway()
            if self._show_inbox:
                self._start_gateway_client()
        else:
            # Switched away from OpenClaw — stop gateway client
            self._stop_gateway_client()

    def action_cycle_effort(self) -> None:
        idx = _EFFORT_CYCLE.index(self._effort_mode) if self._effort_mode in _EFFORT_CYCLE else 1
        self._effort_mode = _EFFORT_CYCLE[(idx + 1) % len(_EFFORT_CYCLE)]
        self.query_one("#status-bar", StatusBar).update_effort(self._effort_mode)
        label, _ = _EFFORT_LABELS[self._effort_mode]
        self.notify(f"Effort → {label}", timeout=3)

    def _toggle_git_commit(self) -> None:
        self._auto_commit = not self._auto_commit
        state = "ON" if self._auto_commit else "OFF"
        self.notify(f"Git auto-commit+push: {state}", timeout=3)

    def action_toggle_git_commit(self) -> None:
        self._toggle_git_commit()

    def action_toggle_verbose_default(self) -> None:
        """Toggle whether new agents open in verbose (expanded) output mode."""
        self._verbose_default = not self._verbose_default
        state = "ON" if self._verbose_default else "OFF"
        self.notify(f"Verbose output default: {state}  ·  applies to next agent  (ctrl+o toggles per-agent)", timeout=4)

    def action_toggle_agent_verbose(self) -> None:
        """Expand or collapse the output area of the currently selected agent (ctrl+o)."""
        ap = self.query_one("#agent-panel", AgentPanel)
        agent = ap.selected_agent()
        if agent:
            agent.toggle_verbose()

    def action_cycle_permissions(self) -> None:
        """Cycle permission mode — works from any context including agent inputs (ctrl+\\)."""
        self._cycle_permissions()

    def _cycle_permissions(self) -> None:
        idx = _PERM_CYCLE.index(self._perm_mode) if self._perm_mode in _PERM_CYCLE else 0
        self._perm_mode = _PERM_CYCLE[(idx + 1) % len(_PERM_CYCLE)]
        self.query_one("#status-bar", StatusBar).update_perm(self._perm_mode)
        label, _ = _PERM_LABELS[self._perm_mode]

        # Apply to every open project immediately — Claude reads settings.local.json
        # before each tool call, so running agents pick this up on their next tool use.
        # Both "safe" and "accept_edits" use the PreToolUse HTTP hook; the difference
        # is that "accept_edits" auto-approves file tools in _on_tool_approval_request.
        # Only "bypass" removes the hook entirely (--dangerously-skip-permissions).
        for project in self._pm.projects:
            if self._agent_type == "claude":
                if self._perm_mode in ("safe", "accept_edits"):
                    self._write_pretooluse_hook(project.path)
                else:
                    self._remove_pretooluse_hook(project.path)
            elif self._agent_type == "cursor":
                self._write_cursor_permissions(project.path, self._perm_mode)

        # Update inline permission indicators on all visible AgentWidgets and PromptBar
        active = self._pm.active
        proj = os.path.basename(active.path.rstrip("/")) if active else ""
        try:
            self.query_one("#prompt-bar", PromptBar).update_perm_indicator(
                self._perm_mode, proj
            )
        except Exception:
            pass
        try:
            for widget in self.query(AgentWidget):
                widget.update_perm_indicator(self._perm_mode)
        except Exception:
            pass

        self.notify(
            f"Permission mode → {label}  "
            "[dim](applied to all open projects)[/dim]",
            timeout=4,
        )

    # ------------------------------------------------------------------ project actions

    def action_next_project(self) -> None:
        self._pm.next_project()
        self._on_project_changed()

    def action_prev_project(self) -> None:
        self._pm.prev_project()
        self._on_project_changed()

    def action_open_project(self) -> None:
        """Push the directory-picker modal; result is a path string, ssh dict, or None."""
        active = self._pm.active
        start  = active.path if (active and not active.is_remote) else None

        def _handle(result: str | dict | None) -> None:
            if not result:
                return
            if isinstance(result, dict):
                # SSH project — mount already done by the picker
                path     = result["path"]
                ssh_info = result["ssh_info"]
                if os.path.isdir(path):
                    proj = self._pm.add_ssh_project(path, ssh_info)
                    self._pm.set_active(len(self._pm.projects) - 1)
                    self._on_project_changed()
                    host = ssh_info.get("host", "?")
                    self.notify(f"Opened remote: {proj.name} ({host})", timeout=4)
                else:
                    self.notify(f"Mount path not found: {path}", severity="error", timeout=5)
            else:
                path = os.path.expanduser(result)
                if os.path.isdir(path):
                    self._pm.add_project(path)
                    self._pm.set_active(len(self._pm.projects) - 1)
                    self._on_project_changed()
                    self.notify(f"Opened: {self._pm.active.name}", timeout=3)
                else:
                    self.notify(f"Not a valid directory: {path}", severity="error", timeout=5)

        self.push_screen(DirectoryPickerScreen(start_path=start), _handle)

    def action_focus_prompt(self) -> None:
        self.query_one("#prompt-bar", PromptBar).focus_input()

    def action_cancel_agent(self) -> None:
        self.query_one("#agent-panel", AgentPanel).cancel_last()

    def action_detach_agent(self) -> None:
        active = self._pm.active
        if active is None:
            return
        panel = self.query_one("#agent-panel", AgentPanel)
        state = panel.detach_selected()
        if state is None:
            self.notify("No agent to detach.", timeout=2)
            return
        bucket = self._detached.setdefault(active.path, [])
        bucket.append(state)
        prompt = state.get("prompt", "")[:40]
        self.notify(f"Detached: {prompt}…  (r=reattach)", timeout=3)

    def action_reattach_menu(self) -> None:
        active = self._pm.active
        if active is None:
            return
        # Pass the LIVE list — the screen mutates it directly so _detached stays
        # in sync without any dismiss callback.
        detached_list = self._detached.setdefault(active.path, [])

        def _on_reattach(state: dict) -> None:
            panel = self.query_one("#agent-panel", AgentPanel)
            panel.restore_agents(active.name, [state], self._vault)
            self._apply_layout()
            self.notify(f"Reattached: {state.get('prompt','')[:40]}…", timeout=3)

        def _on_kill(state: dict) -> None:
            prompt = state.get("prompt", "")[:40]
            self.notify(f"Killed: {prompt}…", timeout=2)

        self.push_screen(DetachMenuScreen(detached_list, _on_reattach, _on_kill))

    def action_import_brain(self) -> None:
        """Open the brain import modal, then run import + profiling in background."""
        def _on_path(path: str | None) -> None:
            if not path:
                return
            path = os.path.expanduser(path.strip())
            self.notify(f"Importing brain: {path}", timeout=3)
            self._run_brain_import(path)

        self.push_screen(BrainImportScreen(), _on_path)

    @work(thread=True)
    def _run_brain_import(self, path: str) -> None:
        """Background worker: import .md files then re-run profiling."""
        try:
            importer = BrainImporter(self._vault)

            if os.path.isfile(path):
                result = importer.import_file(path)
            else:
                result = importer.import_folder(path)

            if not result.imported:
                self.call_from_thread(
                    self.notify,
                    f"Brain import: no .md files found in {path}",
                    severity="warning",
                    timeout=5,
                )
                return

            # Update MOC index to include imported brain notes
            try:
                self._moc.update_index_moc()
            except Exception:
                pass

            # Re-run profiling on the imported corpus + existing prompts
            try:
                existing_prompts = self._sugg.get_all_prompts(n=80)
                # Blend existing prompts with imported text chunks
                combined_corpus = existing_prompts + result.corpus

                current_profile = self._user_profile.read_json()
                if not current_profile:
                    current_profile = self._profile_analyzer.build_basic_profile(combined_corpus)

                if self._profile_analyzer.is_available():
                    enriched = self._profile_analyzer.build_forensic_profile(
                        prompt="[brain import]",
                        project="brain",
                        all_prompts=combined_corpus,
                        current_profile=current_profile,
                    )
                    new_profile = enriched if enriched else current_profile
                else:
                    basic = self._profile_analyzer.build_basic_profile(combined_corpus)
                    new_profile = {**basic, **{
                        k: current_profile.get(k, basic.get(k, {}))
                        for k in ("demographics", "personality", "inferences")
                    }}
                    new_profile["technical_interests"] = basic["technical_interests"]
                    new_profile["behavioral_patterns"] = basic["behavioral_patterns"]
                    new_profile["prompting_style"]     = basic["prompting_style"]

                self._user_profile.write_json(new_profile)
            except Exception:
                pass

            skip_msg = f"  ({len(result.skipped)} skipped)" if result.skipped else ""
            self.call_from_thread(
                self.notify,
                f"Brain imported: {len(result.imported)} notes{skip_msg}. Profile updated.",
                timeout=6,
            )

            # Refresh graph pane if visible
            if self._show_graph:
                self.call_from_thread(
                    self.query_one("#graph-pane", GraphPane).refresh_tree
                )

        except Exception as exc:
            self.call_from_thread(
                self.notify,
                f"Brain import failed: {exc}",
                severity="error",
                timeout=6,
            )

    def action_scroll_down(self) -> None:
        self.query_one("#agent-panel", AgentPanel).scroll_down()

    def action_scroll_up(self) -> None:
        self.query_one("#agent-panel", AgentPanel).scroll_up()

    def action_toggle_files(self) -> None:
        self._show_files = not self._show_files
        self._apply_layout()
        if self._show_files:
            self.query_one("#file-browser", FileBrowserPanel).focus_tree()

    def action_toggle_editor(self) -> None:
        ep = self.query_one("#editor-panel", EditorPanel)
        if self._show_editor and ep._in_view_mode:
            # Editor is visible in colorized read-only view: e closes the panel,
            # so the next e press reopens it in edit mode (two-key read→close→edit).
            self._show_editor = False
            self._apply_layout()
        else:
            self._show_editor = not self._show_editor
            self._apply_layout()

    def action_toggle_graph(self) -> None:
        self._show_graph = not self._show_graph
        self._apply_layout()

    def action_toggle_terminal(self) -> None:
        self._show_terminal = not self._show_terminal
        self._apply_layout()
        if self._show_terminal:
            self.query_one("#terminal-panel", TerminalPanel).focus_input()

    def action_toggle_inbox(self) -> None:
        """Toggle the OpenClaw channel inbox panel."""
        self._show_inbox = not self._show_inbox
        self._apply_layout()
        if self._show_inbox:
            # If OpenClaw is the active agent, make sure the gateway is running
            if self._agent_type == "openclaw":
                self._start_gateway_client()
        else:
            # Stop gateway when panel is closed to save resources
            self._stop_gateway_client()

    def action_toggle_obsidian(self) -> None:
        """Toggle the Obsidian vault panel (O)."""
        if not self._obsidian_vault_path:
            self.notify(
                "No Obsidian vault connected.  "
                "Use [bold]/obsidian <path>[/bold] to attach one.",
                timeout=5,
            )
            return
        self._show_obsidian = not self._show_obsidian
        if self._show_obsidian:
            self._show_graph = False   # mutually exclusive full-screen views
        self._apply_layout()

    def action_open_palette(self) -> None:
        """Open the command palette (ctrl+p)."""
        commands = self._build_palette_commands()

        def _on_result(key: str | None) -> None:
            if key is None:
                return
            handler = self._palette_handlers().get(key)
            if handler:
                handler()

        self.push_screen(CommandPaletteScreen(commands), _on_result)

    def _build_palette_commands(self) -> "list[tuple[str, str, str | None]]":
        """
        Build the full palette command list.

        Each entry: (display name, description, id_key)
        Entries with id_key=None are visual separators (filtered out).
        The currently active option gets a ✓ prefix.
        """
        perm  = self._perm_mode
        agent = self._agent_type
        eff   = self._effort_mode

        def mark(active: bool) -> str:
            return "✓ " if active else "  "

        cmds: list[tuple[str, str, str | None]] = []

        # ── Agent ────────────────────────────────────────────────────────
        cmds += [
            (f"{mark(agent=='claude')}Agent: Claude",    "use Claude Code CLI",         "agent:claude"),
            (f"{mark(agent=='codex')}Agent: Codex",      "use OpenAI Codex CLI",        "agent:codex"),
            (f"{mark(agent=='cursor')}Agent: Cursor",    "use Cursor CLI",              "agent:cursor"),
            (f"{mark(agent=='openclaw')}Agent: OpenClaw","use OpenClaw agent",           "agent:openclaw"),
        ]

        # ── Permission mode ──────────────────────────────────────────────
        cmds += [
            (f"{mark(perm=='plan')}Permission: Plan",           "read-only, no edits",              "perm:plan"),
            (f"{mark(perm=='safe')}Permission: Safe",           "approve every tool call",          "perm:safe"),
            (f"{mark(perm=='accept_edits')}Permission: Accept Edits", "auto-approve file edits",   "perm:accept_edits"),
            (f"{mark(perm=='bypass')}Permission: Bypass All",   "no restrictions",                  "perm:bypass"),
        ]

        # ── Effort level ─────────────────────────────────────────────────
        cmds += [
            (f"{mark(eff=='low')}Effort: Low",    "faster, lighter reasoning",  "effort:low"),
            (f"{mark(eff=='medium')}Effort: Medium", "balanced (default)",       "effort:medium"),
            (f"{mark(eff=='high')}Effort: High",  "deep, thorough reasoning",   "effort:high"),
        ]

        # ── Panels ───────────────────────────────────────────────────────
        cmds += [
            ("Toggle File Browser",   "f — show / hide file tree",       "panel:files"),
            ("Toggle Editor",         "e — show / hide file editor",     "panel:editor"),
            ("Toggle Memory Graph",   "m — show / hide memory graph",    "panel:graph"),
            ("Toggle Terminal",       "t — show / hide embedded shell",  "panel:terminal"),
            ("Toggle Channels",       "c — show / hide OpenClaw inbox",  "panel:channels"),
            ("Toggle Obsidian Panel", "O — show / hide Obsidian vault",  "panel:obsidian"),
        ]

        # ── Theme ────────────────────────────────────────────────────────
        for _group_label, _group_themes in self._THEME_GROUPS:
            for _tid, _tlabel in _group_themes:
                active_mark = "✓ " if self._ui_theme == _tid else "  "
                cmds.append((
                    f"{active_mark}Theme: {_tlabel}",
                    f"{_group_label.lower()} · {_tid}",
                    f"theme:{_tid}",
                ))

        # ── Config & actions ─────────────────────────────────────────────
        git_state  = "ON" if self._auto_commit  else "OFF"
        verb_state = "ON" if self._verbose_default else "OFF"
        cmds += [
            (f"Git Auto-Commit+Push: {git_state}", "G to toggle",         "toggle:git"),
            (f"Verbose Output Default: {verb_state}", "V to toggle",      "toggle:verbose"),
            ("Open Project…",         "open a directory as a project",   "action:open_project"),
            ("Import Brain / Memory…","import .md notes into vault",     "action:import_brain"),
            ("Connect Obsidian Vault…","set external Obsidian vault path","action:obsidian"),
            ("Quit",                  "save session and exit",            "action:quit"),
        ]

        return cmds

    def _palette_handlers(self) -> dict[str, Callable[[], None]]:
        """Map palette id_key → zero-arg callable."""
        return {
            # Agent
            "agent:claude":    lambda: self._scmd_agent("claude"),
            "agent:codex":     lambda: self._scmd_agent("codex"),
            "agent:cursor":    lambda: self._scmd_agent("cursor"),
            "agent:openclaw":  lambda: self._scmd_agent("openclaw"),
            # Permission
            "perm:plan":          lambda: self._scmd_perm("plan"),
            "perm:safe":          lambda: self._scmd_perm("safe"),
            "perm:accept_edits":  lambda: self._scmd_perm("accept_edits"),
            "perm:bypass":        lambda: self._scmd_perm("bypass"),
            # Effort
            "effort:low":     lambda: self._scmd_effort("low"),
            "effort:medium":  lambda: self._scmd_effort("medium"),
            "effort:high":    lambda: self._scmd_effort("high"),
            # Panels
            "panel:files":    self.action_toggle_files,
            "panel:editor":   self.action_toggle_editor,
            "panel:graph":    self.action_toggle_graph,
            "panel:terminal": self.action_toggle_terminal,
            "panel:channels": self.action_toggle_inbox,
            "panel:obsidian": self.action_toggle_obsidian,
            # Themes
            **{
                f"theme:{tid}": (lambda t=tid: self._set_theme(t))
                for _, group in self._THEME_GROUPS
                for tid, _ in group
            },
            # Toggles
            "toggle:git":     self._toggle_git_commit,
            "toggle:verbose": self.action_toggle_verbose_default,
            # Actions
            "action:open_project":  self.action_open_project,
            "action:import_brain":  self.action_import_brain,
            "action:obsidian":      lambda: self._prompt_obsidian_path(),
            "action:quit":          self.action_quit,
        }

    def _prompt_obsidian_path(self) -> None:
        """Push a path-input modal to set the Obsidian vault path."""
        def _on_path(path: str | None) -> None:
            if path:
                self._scmd_obsidian(path.strip())

        self.push_screen(_ObsidianPathScreen(), _on_path)

    def action_run_last_command(self) -> None:
        if not self._last_command:
            self.notify("No command detected yet.", timeout=2)
            return
        # Show terminal if hidden
        if not self._show_terminal:
            self._show_terminal = True
            self._apply_layout()
        tp = self.query_one("#terminal-panel", TerminalPanel)
        tp.focus_input()
        tp.run_command(self._last_command)

    def action_save_file(self) -> None:
        ep = self.query_one("#editor-panel", EditorPanel)
        if ep.current_path:
            if ep.save():
                self.notify("Saved.", timeout=2)

    def action_exit_mode(self) -> None:
        self._exit_to_command()

    # ------------------------------------------------------------------ layout

    def _apply_layout(self) -> None:
        fb       = self.query_one("#file-browser",   FileBrowserPanel)
        ep       = self.query_one("#editor-panel",   EditorPanel)
        ap       = self.query_one("#agent-panel",    AgentPanel)
        tp       = self.query_one("#terminal-panel", TerminalPanel)
        graph    = self.query_one("#graph-pane",     GraphPane)
        inbox    = self.query_one("#inbox-panel",    OpenClawInboxPanel)
        obsidian = self.query_one("#obsidian-panel", ObsidianPanel)

        if self._show_graph:
            fb.display       = False
            ep.display       = False
            ap.display       = False
            tp.display       = False
            graph.display    = True
            inbox.display    = False
            obsidian.display = False
            graph.query_one("#gp-tree", Tree).focus()
        elif self._show_obsidian and self._obsidian_vault_path:
            fb.display       = False
            ep.display       = False
            ap.display       = False
            tp.display       = False
            graph.display    = False
            inbox.display    = False
            obsidian.display = True
            # Refresh panel with current project + vault state
            active = self._pm.active
            obsidian.refresh_for_project(
                project_name  = active.name if active else "",
                project_path  = active.path if active else "",
                obsidian_vault = self._obsidian_vault,
                linker        = self._obsidian_linker,
            )
            obsidian.query_one("#op-tree", _ObsidianTree).focus()
        else:
            graph.display    = False
            obsidian.display = False
            fb.display       = self._show_files
            ep.display       = self._show_editor
            ap.display       = True
            tp.display       = self._show_terminal
            inbox.display    = self._show_inbox
            ap.focus()

    # ------------------------------------------------------------------ project switching

    def _on_project_changed(self) -> None:
        active = self._pm.active
        self.query_one("#tab-bar", ProjectTabBar).refresh_tabs(
            self._pm.projects, self._pm.active_idx
        )
        if active:
            # Switch per-project containers — does NOT cancel running agents
            self.query_one("#agent-panel",    AgentPanel).switch_project(active.name)
            self.query_one("#terminal-panel", TerminalPanel).switch_project(active.name, active.path)
            self._load_active_file(active)
            self.query_one("#file-browser",   FileBrowserPanel).set_root(active.path)
            self.query_one("#status-bar",     StatusBar).update_project(active.name)
            proj_name = os.path.basename(active.path.rstrip("/"))
            self.query_one("#prompt-bar", PromptBar).update_perm_indicator(
                self._perm_mode, proj_name
            )
        self._refresh_suggestions()
        self.query_one("#agent-panel", AgentPanel).focus()

    @on(FileBrowserPanel.FileSelected)
    def _file_browser_selected(self, event: FileBrowserPanel.FileSelected) -> None:
        self._show_editor = True
        self._apply_layout()
        self.query_one("#editor-panel", EditorPanel).load_file(event.path)
        active = self._pm.active
        if active:
            try:
                self._pm.set_active_file(os.path.relpath(event.path, active.path))
            except ValueError:
                pass

    @on(ProjectTabBar.TabPressed)
    def _tab_pressed(self, event: ProjectTabBar.TabPressed) -> None:
        self._pm.set_active(event.idx)
        self._on_project_changed()

    @on(ProjectTabBar.AddPressed)
    def _add_project(self, _: ProjectTabBar.AddPressed) -> None:
        self.action_open_project()

    # ------------------------------------------------------------------ prompt → agent

    @on(PromptSubmitted)
    def _on_prompt(self, event: PromptSubmitted) -> None:
        prompt = event.prompt.strip()
        if not prompt:
            return
        self._history_add(prompt)
        # Reset browse state for the PromptBar input after submission
        self._hist_browse.pop("pb-input", None)

        # ── Slash commands ─────────────────────────────────────────────────
        if prompt.startswith("/"):
            consumed = self._handle_slash_command(prompt)
            if consumed:
                self._exit_to_command()
                return
            # Not consumed → falls through to normal agent dispatch below
            # (Claude handles its own native slash commands)

        # ── Open-project mode ──────────────────────────────────────────────
        if self._open_project_mode:
            self._open_project_mode = False
            self._exit_to_command()
            path = os.path.expanduser(prompt)
            if os.path.isdir(path):
                proj = self._pm.add_project(path)
                self._pm.set_active(len(self._pm.projects) - 1)
                self._on_project_changed()
                self.notify(f"Opened: {proj.name}", timeout=3)
            else:
                self.notify(
                    f"Not a valid directory: {path}",
                    severity="error", timeout=5,
                )
            return

        active = self._pm.active

        # No project open yet — also treat as path
        if active is None:
            self.action_open_project()
            return

        # ── Normal: launch agent ──────────────────────────────────────────
        self._sugg.record(active.name, prompt)
        self._pers.save()

        # Claude: PreToolUse HTTP hook for interactive approval (not needed in plan
        #         mode — Claude enforces read-only natively via --permission-mode plan).
        # Cursor: write .cursor/cli.json deny rules (native permission config).
        # Codex:  permission enforced via --sandbox flag in CodexSession.
        if self._agent_type == "claude":
            if self._perm_mode in ("safe", "accept_edits"):
                self._write_pretooluse_hook(active.path)
            else:
                self._remove_pretooluse_hook(active.path)
        elif self._agent_type == "cursor":
            self._write_cursor_permissions(active.path, self._perm_mode)

        session = self._make_session(prompt, active.path, verbose_output=self._verbose_default)
        self.query_one("#agent-panel", AgentPanel).add_agent(
            session, vault=self._vault, agent_type=self._agent_type,
            verbose=self._verbose_default,
        )

        if self._show_graph:
            self._show_graph = False
        self._apply_layout()
        self._exit_to_command()
        self._refresh_suggestions()

    def _make_session(
        self,
        prompt: str,
        project_path: str,
        resume_session_id: str | None = None,
        verbose_output: bool = False,
    ) -> AgentSession:
        """Create the correct AgentSession subclass for the active agent type."""
        kwargs = dict(
            prompt=prompt,
            project_path=project_path,
            permission_mode=self._perm_mode,
            effort_mode=self._effort_mode,
            model_override=self._model_override,
            max_turns=self._max_turns,
            max_budget_usd=self._max_budget_usd,
            system_prompt=self._system_prompt,
            allowed_tools=list(self._allowed_tools),
            disallowed_tools=list(self._disallowed_tools),
            resume_session_id=resume_session_id,
            verbose_output=verbose_output,
        )
        if self._agent_type == "codex":
            return CodexSession(**kwargs)
        if self._agent_type == "cursor":
            return CursorSession(**kwargs)
        if self._agent_type == "openclaw":
            return OpenClawSession(**kwargs)
        return ClaudeSession(**kwargs)

    # ------------------------------------------------------------------ agent completion

    @on(AgentPanel.AgentComplete)
    def _agent_done(self, event: AgentPanel.AgentComplete) -> None:
        w      = event.agent_widget
        active = self._pm.active
        if event.exit_code == 0:
            self.notify(f"#{w.number} done: {w.session.prompt[:45]}", timeout=4)
            if self._auto_commit and active and active.is_git_repo():
                self._auto_git_commit(active.path, w.session.prompt)
            if active:
                self._post_run_hook(w, active)
        else:
            self.notify(f"#{w.number} failed (exit {event.exit_code})",
                        severity="error", timeout=5)

    @work(thread=True)
    def _post_run_hook(self, w, active) -> None:
        """Background hook that runs after every successful agent completion.

        Steps:
        1. Record prompt in suggestion engine
        2. Generate LLM summary + semantic tags for the run note
        3. Log the run to vault (timestamped note with summary + tags)
        4. Update MOCs + index MOC (always)
        5. Run vault linter and write any issues to vault
        6. Update global user profile (demographics, personality, interests)
        7. Update per-project profile (summary, tech stack, current focus)
        8. Generate personalized next-prompt predictions, blended with graph
        9. Push updated suggestions to the prompt bar
        """
        prompt      = w.session.prompt
        output_tail: list[str] = list(w.session.output_tail)
        project     = active.name

        # 1. Record in suggestion engine
        self._sugg.record(project, prompt)

        # 2. Generate summary + semantic tags via LLM
        summary    = ""
        extra_tags: list[str] = []
        if self._profile_analyzer.is_available():
            try:
                summary, extra_tags = self._profile_analyzer.summarize_run(
                    prompt=prompt,
                    output_tail=output_tail,
                    project=project,
                )
            except Exception:
                pass

        # 3. Log run to vault (also updates project MOC + Run Outputs MOC internally)
        try:
            action_id = prompt[:40].replace(" ", "_").replace("/", "-").lower()
            self._run_logger.log(
                action_id=action_id,
                action_label=prompt[:60],
                project=project,
                prompt=prompt,
                output="\n".join(output_tail),
                summary=summary,
                extra_tags=extra_tags,
            )
        except Exception:
            pass

        # 4. Update master index MOC (run_logger already updates project + run_outputs MOCs)
        try:
            self._moc.update_index_moc()
        except Exception:
            pass

        # 5. Vault linter + auto-clean (delete empty notes, compact redundant logs)
        try:
            linker = Linker(self._vault)
            linter = VaultLinter(self._vault, linker)
            report = linter.run()

            # Auto-clean: remove empty notes and compact related run logs
            deleted_empty, compacted = linter.auto_clean(self._compactor)

            # Write compact lint report only when there are real structural issues
            structural_issues = bool(report.broken_links or report.stale_mocs)
            if structural_issues or deleted_empty or compacted:
                from datetime import datetime as _dt
                lines = [f"_Updated {_dt.utcnow().strftime('%Y-%m-%d %H:%M')} UTC_\n"]
                if deleted_empty:
                    lines.append(f"- Removed {deleted_empty} empty note(s)")
                if compacted:
                    lines.append(f"- Compacted {compacted} redundant run log(s)")
                for src, tgt in report.broken_links[:20]:
                    lines.append(f"- Broken link: `{src}` → `{tgt}`")
                for t in report.stale_mocs[:10]:
                    lines.append(f"- Stale MOC: {t}")
                body = "\n".join(lines)
                lint_rel = os.path.join("meta", "lint_report")
                existing = self._vault.get_note(lint_rel)
                if existing is None:
                    self._vault.create_note(
                        lint_rel, title="Vault Lint Report", body=body,
                        tags=["meta", "lint"], note_type="meta",
                    )
                else:
                    from memory.note import FRONTMATTER_RE as _FM_RE
                    fm_m = _FM_RE.match(existing.content)
                    existing.content = (
                        existing.content[:fm_m.end()] if fm_m else ""
                    ) + body
                    self._vault.save_note(existing)
        except Exception:
            pass

        # 6. Forensic profile update — always writes, LLM enriches when available
        try:
            all_prompts = self._sugg.get_all_prompts(n=80)
            # Start from existing profile or build a fresh basic one
            current_profile = self._user_profile.read_json()
            if not current_profile:
                current_profile = self._profile_analyzer.build_basic_profile(all_prompts)

            if self._profile_analyzer.is_available():
                # Full LLM forensic analysis
                enriched = self._profile_analyzer.build_forensic_profile(
                    prompt=prompt,
                    project=project,
                    all_prompts=all_prompts,
                    current_profile=current_profile,
                )
                new_profile_json = enriched if enriched else current_profile
            else:
                # Non-LLM keyword analysis — merge over existing
                basic = self._profile_analyzer.build_basic_profile(all_prompts)
                # Merge: keep existing rich fields, update observable ones
                new_profile_json = {**basic, **{
                    k: current_profile.get(k, basic.get(k, {}))
                    for k in ("demographics", "personality", "inferences")
                }}
                new_profile_json["technical_interests"] = basic["technical_interests"]
                new_profile_json["behavioral_patterns"] = basic["behavioral_patterns"]
                new_profile_json["prompting_style"]     = basic["prompting_style"]

            self._user_profile.write_json(new_profile_json)
        except Exception:
            pass

        # 7. Update per-project profile (summary, tech stack, current focus)
        if self._profile_analyzer.is_available():
            try:
                current_proj_profile = self._user_profile.read_project(project)
                recent_proj_prompts  = self._sugg.get_recent_prompts(project, n=20)
                new_proj_profile = self._profile_analyzer.update_project_profile(
                    prompt=prompt,
                    output_tail=output_tail,
                    project=project,
                    recent_prompts=recent_proj_prompts,
                    current_profile=current_proj_profile,
                )
                if new_proj_profile:
                    self._user_profile.write_project(project, new_proj_profile)
            except Exception:
                pass

        # 8. Personalized next-prompt predictions
        suggestions: list[str] = []
        if self._profile_analyzer.is_available():
            try:
                profile_dict   = self._user_profile.read_json()
                recent_prompts = self._sugg.get_recent_prompts(project, n=12)
                suggestions    = self._profile_analyzer.predict_prompts(
                    profile=profile_dict,
                    project=project,
                    last_prompt=prompt,
                    output_tail=output_tail,
                    recent_prompts=recent_prompts,
                    n=5,
                )
            except Exception:
                pass

        # Blend with graph-based fallbacks
        graph_suggestions = self._sugg.get_suggestions(
            project_name=project,
            last_prompt=prompt,
            n=5,
        )
        blended = suggestions + [s for s in graph_suggestions if s not in suggestions]
        blended = blended[:5]

        # 9. Push to prompt bar
        if blended:
            self.call_from_thread(
                self.query_one("#prompt-bar", PromptBar).__setattr__,
                "suggestions",
                blended,
            )

        # 10. Refresh memory graph pane if visible
        if self._show_graph:
            try:
                self.call_from_thread(
                    self.query_one("#graph-pane", GraphPane)._populate
                )
            except Exception:
                pass

    # ------------------------------------------------------------------ permission decisions

    @on(PermissionPrompt.Decision)
    async def _permission_decision(self, event: PermissionPrompt.Decision) -> None:
        """Handle approve/deny from the inline permission prompt."""
        rid  = event.request_id   # HTTP server request ID to unblock
        tool = event.tool_name
        proj_path = (self._pm.active.path if self._pm.active
                     else event.session.project_path)

        if event.allow:
            if event.always:
                # Persist the allow rule so future runs don't prompt
                self._write_permission_allow(proj_path, tool)
                self.notify(
                    f"[bold]{tool}[/bold] added to settings.local.json permanently.",
                    timeout=5,
                )
            else:
                self.notify(f"[bold]{tool}[/bold] approved.", timeout=2)
            # Unblock the waiting HTTP hook — Claude continues immediately
            self._approval_server.respond(rid, allow=True)
        else:
            self.notify(f"Denied: [bold]{tool}[/bold]", severity="warning", timeout=3)
            self._approval_server.respond(rid, allow=False,
                                          reason=f"User denied {tool}")

    # ------------------------------------------------------------------ approval server

    def _on_tool_approval_request(self, request_id: str, payload: dict) -> None:
        """
        Called by ApprovalServer when Claude's PreToolUse hook fires.

        In "accept_edits" mode, file-manipulation tools (Read, Write, Edit, …)
        are silently auto-approved.  Only Bash, network, and other potentially
        destructive tools surface the TUI PermissionPrompt.

        In "safe" mode, every tool surfaces the TUI PermissionPrompt.
        """
        from terminal.claude_session import ACCEPT_EDITS_AUTO_APPROVE

        tool_name  = payload.get("tool_name", "unknown")
        tool_input = payload.get("tool_input", {})

        # "accept_edits": silently allow file-only tools without showing the prompt
        if self._perm_mode == "accept_edits" and tool_name in ACCEPT_EDITS_AUTO_APPROVE:
            self._approval_server.respond(request_id, allow=True)
            return

        # Find the most-recently-mounted agent that is still running
        agent = self._find_running_agent()
        if agent is None:
            self._approval_server.respond(request_id, allow=False,
                                          reason="No running agent found")
            return

        request = {
            "type":       "permission_request",
            "tool_name":  tool_name,
            "tool_input": tool_input,
            "request_id": request_id,
        }

        # call_later schedules the mount on the next Textual event-loop iteration.
        # _on_tool_approval_request is called synchronously from an asyncio
        # callback (ApprovalServer._handle), which is outside Textual's normal
        # message-handler context.  Deferring via call_later ensures Textual
        # processes the DOM change correctly while _handle is already suspended
        # at `await event.wait()`.
        def _do_mount() -> None:
            try:
                prompt_widget = PermissionPrompt(agent, agent.session, request)
                if agent._status:
                    agent.mount(prompt_widget, before=agent._status)
                else:
                    agent.mount(prompt_widget)
                # Scroll the prompt into view without stealing focus or
                # jumping — scroll_visible moves the nearest scrollable
                # ancestor just enough to reveal the widget.
                self.call_later(
                    lambda: prompt_widget.scroll_visible(animate=False)
                )
            except Exception as exc:
                self._approval_server.respond(
                    request_id, allow=False, reason=f"Prompt mount error: {exc}"
                )

        self.call_later(_do_mount)

    def _find_running_agent(self) -> "AgentWidget | None":
        """Return the most recently mounted AgentWidget (active project) that has not finished."""
        try:
            ap     = self.query_one("#agent-panel", AgentPanel)
            agents = ap.active_agents()
            for w in reversed(agents):
                # _mark_complete shows the reply input — if it's hidden the agent is running
                sid   = w.session.session_id
                reply = w.query_one(f"#agent-reply-{sid}", Input)
                if not reply.display:
                    return w
            return agents[-1] if agents else None
        except Exception:
            return None

    def _write_permission_allow(self, project_path: str, tool_name: str) -> None:
        """Append tool_name to .claude/settings.local.json permissions.allow."""
        import json as _json
        claude_dir    = os.path.join(project_path, ".claude")
        os.makedirs(claude_dir, exist_ok=True)
        settings_path = os.path.join(claude_dir, "settings.local.json")
        try:
            with open(settings_path) as f:
                settings = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            settings = {}
        perms = settings.setdefault("permissions", {})
        allow = perms.setdefault("allow", [])
        if tool_name not in allow:
            allow.append(tool_name)
        settings.setdefault("autoCompact", True)
        with open(settings_path, "w") as f:
            _json.dump(settings, f, indent=2)

    def _write_pretooluse_hook(self, project_path: str) -> None:
        """Write the PreToolUse HTTP hook into .claude/settings.local.json."""
        import json as _json
        claude_dir    = os.path.join(project_path, ".claude")
        os.makedirs(claude_dir, exist_ok=True)
        settings_path = os.path.join(claude_dir, "settings.local.json")
        try:
            with open(settings_path) as f:
                settings = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            settings = {}
        port = self._approval_server.port
        settings.setdefault("hooks", {})["PreToolUse"] = [
            {
                "hooks": [
                    {
                        "type":    "http",
                        "url":     f"http://127.0.0.1:{port}/pre-tool",
                        "timeout": 300,
                    }
                ]
            }
        ]
        settings.setdefault("autoCompact", True)
        with open(settings_path, "w") as f:
            _json.dump(settings, f, indent=2)

    def _remove_pretooluse_hook(self, project_path: str) -> None:
        """Remove the PreToolUse hook from .claude/settings.local.json."""
        import json as _json
        settings_path = os.path.join(project_path, ".claude", "settings.local.json")
        try:
            with open(settings_path) as f:
                settings = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            return
        hooks = settings.get("hooks", {})
        hooks.pop("PreToolUse", None)
        if not hooks:
            settings.pop("hooks", None)
        with open(settings_path, "w") as f:
            _json.dump(settings, f, indent=2)

    # Cursor permission modes → .cursor/cli.json deny rules
    # safe:         deny all writes + shell commands (read-only)
    # accept_edits: deny shell commands only (file edits OK; --force enables writes in print mode)
    # bypass:       no deny rules (--force allows everything)
    _CURSOR_PERM_DENY: dict[str, list[str]] = {
        "plan":         ["Shell(*)", "Write(**/**)"],  # read-only; --plan flag also enforces this
        "safe":         ["Shell(*)", "Write(**/**)"],
        "accept_edits": ["Shell(*)"],
        "bypass":       [],
    }

    def _write_cursor_permissions(self, project_path: str, perm_mode: str) -> None:
        """Write .cursor/cli.json with deny rules matching the vibe-cli permission mode."""
        import json as _json
        cursor_dir = os.path.join(project_path, ".cursor")
        os.makedirs(cursor_dir, exist_ok=True)
        cli_json   = os.path.join(cursor_dir, "cli.json")
        deny       = self._CURSOR_PERM_DENY.get(perm_mode, self._CURSOR_PERM_DENY["accept_edits"])
        config     = {"permissions": {"allow": [], "deny": deny}}
        with open(cli_json, "w") as f:
            _json.dump(config, f, indent=2)

    @work(thread=True)
    def _auto_git_commit(self, project_path: str, prompt: str) -> None:
        import subprocess
        msg = self._commit_prefix + prompt[:72]
        try:
            subprocess.run(["git", "-C", project_path, "add", "-A"],
                           capture_output=True, timeout=10)
            r = subprocess.run(["git", "-C", project_path, "commit", "-m", msg],
                               capture_output=True, text=True, timeout=15)
            if r.returncode == 0:
                self.call_from_thread(self.notify, f"Committed: {msg[:50]}", timeout=3)
                p = subprocess.run(["git", "-C", project_path, "push"],
                                   capture_output=True, text=True, timeout=30)
                if p.returncode == 0:
                    self.call_from_thread(self.notify, "Pushed to remote", timeout=3)
                else:
                    err = p.stderr.strip().splitlines()[-1] if p.stderr.strip() else "push failed"
                    self.call_from_thread(self.notify, f"Push failed: {err}", severity="warning", timeout=5)
        except Exception:
            pass

    # ------------------------------------------------------------------ command detection

    @on(CommandDetected)
    def _command_detected(self, event: CommandDetected) -> None:
        """Track the last detected shell command from agent output."""
        self._last_command = event.cmd

    # ------------------------------------------------------------------ graph

    @on(GraphPane.NodeOpened)
    def _graph_node(self, event: GraphPane.NodeOpened) -> None:
        # Show in editor
        self._show_graph  = False
        self._show_editor = True
        self._apply_layout()
        self.query_one("#editor-panel", EditorPanel).load_file(event.path)

    @on(ObsidianPanel.NoteOpened)
    def _obsidian_note_opened(self, event: ObsidianPanel.NoteOpened) -> None:
        """Open a selected Obsidian note in the editor."""
        self._show_obsidian = False
        self._show_editor   = True
        self._apply_layout()
        self.query_one("#editor-panel", EditorPanel).load_file(event.path)

    # ------------------------------------------------------------------ editor / file

    def _load_active_file(self, project: Project) -> None:
        path = project.resolve_active_file()
        if path:
            self.query_one("#editor-panel", EditorPanel).load_file(path)

    # ------------------------------------------------------------------ suggestions

    # ------------------------------------------------------------------ theme

    #: All themes available in the palette, grouped for display.
    _THEME_GROUPS: ClassVar[list[tuple[str, list[tuple[str, str]]]]] = [
        ("Dark – Classic", [
            ("textual-dark",         "Textual Dark"),
            ("dracula",              "Dracula"),
            ("monokai",              "Monokai"),
            ("nord",                 "Nord"),
            ("gruvbox",              "Gruvbox"),
            ("tokyo-night",          "Tokyo Night"),
            ("atom-one-dark",        "Atom One Dark"),
            ("solarized-dark",       "Solarized Dark"),
        ]),
        ("Dark – Catppuccin & Rosé", [
            ("catppuccin-mocha",     "Catppuccin Mocha"),
            ("catppuccin-frappe",    "Catppuccin Frappé"),
            ("catppuccin-macchiato", "Catppuccin Macchiato"),
            ("rose-pine",            "Rosé Pine"),
            ("rose-pine-moon",       "Rosé Pine Moon"),
            ("flexoki",              "Flexoki"),
        ]),
        ("Dark – Vivid & Neon", [
            ("cyberpunk",            "Cyberpunk"),
            ("synthwave",            "Synthwave"),
            ("night-owl",            "Night Owl"),
            ("cobalt",               "Cobalt"),
            ("horizon",              "Horizon"),
            ("panda",                "Panda"),
            ("matrix",               "Matrix"),
            ("hacker",               "Hacker"),
        ]),
        ("Dark – Natural & Muted", [
            ("everforest",           "Everforest"),
            ("ayu-dark",             "Ayu Dark"),
            ("ayu-mirage",           "Ayu Mirage"),
            ("material-ocean",       "Material Ocean"),
            ("palenight",            "Palenight"),
            ("kanagawa",             "Kanagawa"),
            ("mellow",               "Mellow"),
            ("midnight-blue",        "Midnight Blue"),
        ]),
        ("Light", [
            ("textual-light",        "Textual Light"),
            ("textual-ansi",         "Textual ANSI"),
            ("atom-one-light",       "Atom One Light"),
            ("catppuccin-latte",     "Catppuccin Latte"),
            ("rose-pine-dawn",       "Rosé Pine Dawn"),
            ("solarized-light",      "Solarized Light"),
            ("ayu-light",            "Ayu Light"),
            ("paper",                "Paper"),
            ("everforest-light",     "Everforest Light"),
            ("warm-light",           "Warm Light"),
        ]),
    ]

    def _apply_persisted_theme(self) -> None:
        """Apply the restored theme to the live app."""
        self._apply_theme(
            self._ui_theme,
            persist_config=False,
            persist_session=False,
            notify=False,
        )

    def _apply_theme(
        self,
        name: str,
        *,
        persist_config: bool,
        persist_session: bool,
        notify: bool,
    ) -> bool:
        if not isinstance(name, str) or not name:
            return False
        try:
            self.theme = name
        except Exception:
            if notify:
                self.notify(f"Unknown theme: {name}", severity="warning", timeout=3)
            if persist_config or persist_session:
                self._ui_theme = "textual-dark"
            return False
        self._ui_theme = name
        if persist_config:
            self._persist_theme_config(name)
        if persist_session:
            self._session_store.patch_global(ui_theme=name)
        if notify:
            self.notify(f"Theme: {name}", timeout=2)
        return True

    def _set_theme(self, name: str) -> None:
        """Switch the app theme live and persist it."""
        self._apply_theme(
            name,
            persist_config=True,
            persist_session=True,
            notify=True,
        )

    def _persist_theme_config(self, name: str) -> None:
        import json as _json
        config_path = self._config_path
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            cfg = {}
        cfg.setdefault("ui", {})["theme"] = name
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(cfg, f, indent=2)
        except Exception:
            pass

    def _persist_obsidian_config(self, vault_path: str) -> None:
        """Write the obsidian.vault_path key back to config.json."""
        import json as _json
        config_path = self._config_path
        try:
            with open(config_path, encoding="utf-8") as f:
                cfg = _json.load(f)
        except (FileNotFoundError, _json.JSONDecodeError):
            cfg = {}
        cfg.setdefault("obsidian", {})["vault_path"] = vault_path
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                _json.dump(cfg, f, indent=2)
        except Exception:
            pass

    # ------------------------------------------------------------------ manual shortcuts

    @staticmethod
    def _manual_shortcuts_path(vault_root: str) -> str:
        return os.path.join(vault_root, "user", "manual_shortcuts.json")

    @staticmethod
    def _load_manual_shortcuts(vault_root: str) -> list[str]:
        import json as _json
        path = VibeCLIApp._manual_shortcuts_path(vault_root)
        try:
            with open(path) as f:
                data = _json.load(f)
            if isinstance(data, list):
                slots = list(data)[:5]
                # Pad to 5 slots
                slots += [""] * (5 - len(slots))
                return slots
        except (FileNotFoundError, Exception):
            pass
        return [""] * 5

    def _save_manual_shortcut(self, slot: int, text: str) -> None:
        """Persist one manual shortcut slot and update the PromptBar."""
        import json as _json
        if not (0 <= slot <= 4):
            return
        self._manual_shortcuts[slot] = text
        vault_root = self._config.get("vault", {}).get("root", "vault")
        path = self._manual_shortcuts_path(vault_root)
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                _json.dump(self._manual_shortcuts, f, indent=2)
        except Exception:
            pass
        # Refresh the PromptBar display
        self.query_one("#prompt-bar", PromptBar).manual_shortcuts = list(self._manual_shortcuts)

    def _refresh_limits_bar(self) -> None:
        self.query_one("#status-bar", StatusBar).update_limits(
            max_turns       = self._max_turns,
            max_budget_usd  = self._max_budget_usd,
            system_prompt   = self._system_prompt,
            allowed_tools   = self._allowed_tools,
            disallowed_tools= self._disallowed_tools,
            model_override  = self._model_override,
        )

    def _refresh_suggestions(self) -> None:
        active = self._pm.active
        if not active:
            return
        ep = self.query_one("#editor-panel", EditorPanel)
        suggestions = self._sugg.get_suggestions(
            project_name=active.name,
            active_file=ep.current_path,
            n=5,
        )
        self.query_one("#prompt-bar", PromptBar).suggestions = suggestions

    # ------------------------------------------------------------------ SSH mount lifecycle

    def _remount_ssh_projects(self) -> None:
        """On startup, re-mount any SSH projects from projects.json (best-effort)."""
        from core.ssh_mount import SSHInfo, mount as ssh_mount, is_available as sshfs_ok
        if not sshfs_ok():
            return
        for proj in self._pm.projects:
            if not proj.ssh_info:
                continue
            try:
                info  = SSHInfo.from_dict(proj.ssh_info)
                local = ssh_mount(info, timeout=20)
                proj.path = local   # update in case mount dir changed
            except Exception as exc:
                self.notify(
                    f"Could not remount {proj.ssh_info.get('host','?')}: {exc}",
                    severity="warning", timeout=6,
                )

    def _unmount_ssh_projects(self) -> None:
        """On quit, unmount all sshfs-mounted projects (best-effort)."""
        from core.ssh_mount import unmount as ssh_unmount
        for proj in self._pm.projects:
            if proj.ssh_info:
                try:
                    ssh_unmount(proj.path)
                except Exception:
                    pass

    # ------------------------------------------------------------------ OpenClaw gateway

    @work(thread=True)
    def _check_openclaw_gateway(self) -> None:
        """Background worker: ping the OpenClaw Gateway and update the status bar."""
        from core.openclaw_config import OpenClawConfig, is_gateway_reachable, gateway_port
        try:
            cfg       = OpenClawConfig.load()
            port      = gateway_port(cfg)
            reachable = is_gateway_reachable(port)
            channels  = cfg.channels if reachable else []
            self.call_from_thread(
                self.query_one("#status-bar", StatusBar).update_openclaw_status,
                reachable,
                channels,
            )
            if not reachable:
                self.call_from_thread(
                    self.notify,
                    "OpenClaw Gateway is not running. "
                    "Start it with: openclaw gateway  "
                    "or: openclaw onboard --install-daemon",
                    severity="warning",
                    timeout=8,
                )
        except Exception:
            pass

    def _start_gateway_client(self) -> None:
        """Start the GatewayClient as an asyncio task (no-op if already running)."""
        if self._gateway_task and not self._gateway_task.done():
            return   # already running

        try:
            from core.openclaw_config import OpenClawConfig, gateway_port, is_gateway_reachable
            cfg  = OpenClawConfig.load()
            port = gateway_port(cfg)
            if not is_gateway_reachable(port):
                return   # gateway not up — don't bother
        except Exception:
            return

        client = make_client(port=port)

        # Wire callbacks — all call into the UI thread via call_from_thread
        def _on_message(msg: ChannelMessage) -> None:
            self.call_from_thread(
                self.query_one("#inbox-panel", OpenClawInboxPanel).add_message, msg
            )

        def _on_device(dev: DeviceEvent) -> None:
            self.call_from_thread(
                self.query_one("#inbox-panel", OpenClawInboxPanel).add_device_event, dev
            )

        def _on_status(text: str) -> None:
            self.call_from_thread(
                self.query_one("#inbox-panel", OpenClawInboxPanel).set_status, text
            )

        client.on_message      = _on_message
        client.on_device_event = _on_device
        client.on_status       = _on_status

        self._gateway_client = client
        # Schedule as a background asyncio task on the app's event loop
        self._gateway_task = asyncio.ensure_future(client.run())

    def _stop_gateway_client(self) -> None:
        """Stop and clean up the GatewayClient task."""
        if self._gateway_client:
            self._gateway_client.stop()
            self._gateway_client = None
        if self._gateway_task:
            self._gateway_task.cancel()
            self._gateway_task = None

    # ------------------------------------------------------------------ session persistence

    def action_quit(self) -> None:
        """Save session state, stop gateway client, unmount SSH filesystems, then quit."""
        self._save_session()
        self._stop_gateway_client()
        self._unmount_ssh_projects()
        # Remove the PreToolUse hook from every open project so that subsequent
        # Claude Code sessions in those directories are not blocked by a stale
        # hook URL pointing to this (now-dead) approval server.
        for project in self._pm.projects:
            try:
                self._remove_pretooluse_hook(project.path)
            except Exception:
                pass
        self.exit()

    def _save_session(self) -> None:
        """Serialize all open agent widgets + UI state to vault/user/session.json."""
        ap = self.query_one("#agent-panel", AgentPanel)
        projects_state: dict[str, dict] = {}
        for project in self._pm.projects:
            agents = ap.get_agents_for_project(project.name)
            projects_state[project.path] = {
                "agents": [w.to_state() for w in agents],
            }
        state = {
            "version": 1,
            "global": {
                "active_project_idx": self._pm.active_idx,
                "permission_mode":    self._perm_mode,
                "agent_type":         self._agent_type,
                "effort_mode":        self._effort_mode,
                "show_files":         self._show_files,
                "show_editor":        self._show_editor,
                "show_terminal":      self._show_terminal,
                "show_graph":         self._show_graph,
                "show_obsidian":      self._show_obsidian,
                "ui_theme":           self._ui_theme,
            },
            "projects": projects_state,
            "detached": self._detached,
            "prompt_history": self._prompt_history[-500:],  # cap to 500 entries
        }
        try:
            self._session_store.save(state)
        except Exception:
            pass

    def _restore_session(self, state: dict) -> None:
        """Rebuild agent widgets and UI state from a saved session dict."""
        if not state:
            return

        g = state.get("global", {})

        # Restore prompt history
        saved_history = state.get("prompt_history", [])
        if isinstance(saved_history, list):
            self._prompt_history = [h for h in saved_history if isinstance(h, str)]

        # Restore global UI flags
        self._perm_mode   = g.get("permission_mode",  self._perm_mode)
        self._agent_type  = g.get("agent_type",        self._agent_type)
        self._effort_mode = g.get("effort_mode",       self._effort_mode)
        # Theme: session.json is updated immediately on every theme change
        # (via SessionStore.patch_global), so it is always authoritative.
        # Fall back to the config.json value (already in self._ui_theme) only
        # when the session has no theme entry.
        session_theme = g.get("ui_theme")
        if session_theme:
            self._ui_theme = session_theme
        self._show_files    = g.get("show_files",    False)
        self._show_editor   = g.get("show_editor",   False)
        self._show_terminal = g.get("show_terminal", False)
        self._show_graph    = g.get("show_graph",    False)
        self._show_obsidian = g.get("show_obsidian", False)

        self.query_one("#status-bar", StatusBar).update_agent(self._agent_type)
        self.query_one("#status-bar", StatusBar).update_perm(self._perm_mode)
        self.query_one("#status-bar", StatusBar).update_effort(self._effort_mode)
        self._refresh_limits_bar()
        _active_r = self._pm.active
        _proj_r = os.path.basename(_active_r.path.rstrip("/")) if _active_r else ""
        self.query_one("#prompt-bar", PromptBar).update_perm_indicator(self._perm_mode, _proj_r)

        # Restore detached agents
        self._detached = state.get("detached", {})

        # Restore agents per project
        ap      = self.query_one("#agent-panel", AgentPanel)
        saved_p = state.get("projects", {})
        for project in self._pm.projects:
            proj_state = saved_p.get(project.path, {})
            agents_data = proj_state.get("agents", [])
            if not agents_data:
                continue
            # Ensure container exists for this project
            ap.switch_project(project.name)
            ap.restore_agents(project.name, agents_data, self._vault)

        # Re-switch to the last active project
        active_idx = g.get("active_project_idx", self._pm.active_idx)
        if 0 <= active_idx < len(self._pm.projects):
            self._pm.set_active(active_idx)

        # Restore active project's container + panels
        active = self._pm.active
        if active:
            ap.switch_project(active.name)
            self.query_one("#terminal-panel", TerminalPanel).switch_project(
                active.name, active.path
            )
            self.query_one("#file-browser",  FileBrowserPanel).set_root(active.path)
            self.query_one("#status-bar",    StatusBar).update_project(active.name)
            self.query_one("#tab-bar",       ProjectTabBar).refresh_tabs(
                self._pm.projects, self._pm.active_idx
            )

        self._apply_layout()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# _CUSTOM_THEMES, _APP_TO_PYGMENTS_THEME imported from ui.themes
