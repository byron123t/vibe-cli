"""VibeCLI TUI — modal, keyboard-first multi-project Claude Code interface.

Modes
─────
  Command mode  (default)  — single key shortcuts, no typing
  Prompt mode              — typing a prompt for a new agent  (n or Enter)

Command mode keys
─────────────────
  ]  /  [       next / prev project
  1 … 9         jump to project by number
  n  or  Enter  open prompt to start a new agent
  x             cancel last running agent
  d             close / dismiss last agent widget
  j / k         scroll agent list down / up
  e             toggle editor panel
  s             save current file  (when editor is open)
  p             play audio file  (when editor shows a supported audio file)
  m             toggle memory / knowledge graph
  t             toggle terminal panel (show/hide, or focus when open)
  r             run last detected shell command in terminal
  q             quit
  Escape / Backspace / ,   back to command mode

Prompt mode  (Input focused)
─────────────────────────────
  type freely   builds the prompt
  Tab           cycle through suggestions
  Enter         submit → launch Claude agent
  Escape / ,    back to command mode  (comma only when Input is empty)
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
from core.session_store import SessionStore
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


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

class PromptSubmitted(Message):
    def __init__(self, prompt: str) -> None:
        self.prompt = prompt
        super().__init__()


class CommandDetected(Message):
    """Emitted by AgentWidget when a shell command is found in output."""
    def __init__(self, cmd: str) -> None:
        self.cmd = cmd
        super().__init__()


# ---------------------------------------------------------------------------
# SelectableLog — TextArea subclass that copies to the system clipboard
# ---------------------------------------------------------------------------

class AgentLog(RichLog):
    """
    RichLog that never captures mouse-scroll events.

    _get_dispatch_methods is a generator that checks event._no_default_action
    before yielding each MRO class's handler.  Calling event.prevent_default()
    here stops Widget._on_mouse_scroll_down/up (which scrolls and stops the
    event) from ever running, so the event bubbles freely to the parent
    ScrollableContainer.  The auto-scroll from write()/scroll_end() is a
    separate code path (scroll_to) and is unaffected.
    """

    def _on_mouse_scroll_down(self, event) -> None:  # type: ignore[override]
        event.prevent_default()  # skip Widget handler; event still bubbles to AgentPanel

    def _on_mouse_scroll_up(self, event) -> None:  # type: ignore[override]
        event.prevent_default()  # skip Widget handler; event still bubbles to AgentPanel


class SelectableLog(TextArea):
    """
    Read-only TextArea for agent output.

    Cmd+C / Ctrl+C copies the current selection (or all text if nothing is
    selected) to the system clipboard via:
      1. OSC 52 terminal escape (works in iTerm2, Alacritty, tmux, …)
      2. pbcopy  fallback for macOS Terminal.app
      3. xclip   fallback for Linux

    Mouse-scroll is only captured when this widget has focus (clicked or
    tabbed into).  When unfocused, prevent_default() stops Widget's handler
    from running so the event bubbles to the parent ScrollableContainer.
    When focused, this handler does nothing and Widget's handler runs normally
    via MRO, scrolling the TextArea and stopping the event.
    """

    def _on_mouse_scroll_down(self, event) -> None:  # type: ignore[override]
        if self.has_focus:
            event.stop()           # focused: contain scroll here, never bubble to AgentPanel
                                   # (Widget's handler still runs via MRO and scrolls the TextArea)
        else:
            event.prevent_default()  # unfocused: skip Widget handler; bubble to AgentPanel

    def _on_mouse_scroll_up(self, event) -> None:  # type: ignore[override]
        if self.has_focus:
            event.stop()           # focused: contain scroll here, never bubble to AgentPanel
        else:
            event.prevent_default()  # unfocused: skip Widget handler; bubble to AgentPanel

    def action_copy(self) -> None:
        text = self.selected_text or self.text
        if not text:
            return
        # OSC 52 (most modern terminals incl. iTerm2)
        self.app.copy_to_clipboard(text)
        # pbcopy / xclip fallback so Terminal.app also works
        try:
            if sys.platform == "darwin":
                subprocess.run(
                    ["pbcopy"], input=text.encode(),
                    capture_output=True, timeout=3, check=False,
                )
            elif sys.platform.startswith("linux"):
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text.encode(),
                    capture_output=True, timeout=3, check=False,
                )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# AgentMemoryWidget — vault notes related to an agent's prompt
# ---------------------------------------------------------------------------

class AgentMemoryWidget(Static):
    """Shows vault notes related to the agent's prompt."""

    DEFAULT_CSS = """
    AgentMemoryWidget {
        height: auto;
        color: $text-muted;
        padding: 0 1;
        background: $surface;
    }
    """

    def __init__(self, prompt: str, vault: "MemoryVault | None",
                 project: str = "", **kwargs) -> None:
        super().__init__(**kwargs)
        self._prompt  = prompt
        self._vault   = vault
        self._project = project

    def on_mount(self) -> None:
        self._populate()

    def _populate(self) -> None:
        if not self._vault:
            self.update("[dim]♦ (no vault)[/dim]")
            return

        parts: list[str] = []

        # 1. Project profile summary (first non-empty content line after headings)
        if self._project:
            prof_path = os.path.join(
                self._vault.root, "projects", self._project, "profile.md"
            )
            if os.path.isfile(prof_path):
                try:
                    with open(prof_path, encoding="utf-8") as f:
                        ptext = f.read()
                    # Extract Summary section value
                    m = re.search(r"## Summary\s+(.+?)(?=\n##|\Z)", ptext, re.DOTALL)
                    if m:
                        snippet = m.group(1).strip().split("\n")[0]
                        if snippet and "_Not yet observed_" not in snippet:
                            parts.append(f"[dim italic]{snippet[:90]}[/dim italic]")
                except Exception:
                    pass

        # 2. Recent tag cloud from this project's run logs
        if self._project:
            proj_notes = self._vault.get_project_notes(self._project)
            run_logs = sorted(
                [n for n in proj_notes if "run_log" in n.tags],
                key=lambda n: n.modified_at, reverse=True,
            )
            tag_counts: dict[str, int] = {}
            skip = {"run_log", self._project, "run_outputs"}
            for note in run_logs[:10]:
                for t in note.tags:
                    if t not in skip:
                        tag_counts[t] = tag_counts.get(t, 0) + 1
            if tag_counts:
                top_tags = sorted(tag_counts, key=lambda t: -tag_counts[t])[:6]
                parts.append("[dim]" + "  ".join(f"#{t}" for t in top_tags) + "[/dim]")

        if parts:
            self.update("  ".join(parts))
        else:
            self.update("[dim]♦ (no project memory yet)[/dim]")


# ---------------------------------------------------------------------------
# PermissionPrompt — inline approve/deny widget inside an AgentWidget
# ---------------------------------------------------------------------------

class PermissionPrompt(Static, can_focus=True):
    """
    Inline permission-request widget with keyboard-navigable text options.

    ◄/► or h/l     move selection between options
    Enter / y       confirm selected option
    n               jump to Deny and confirm

    Options:  [0] Approve   [1] Approve+Remember   [2] Deny
    """

    DEFAULT_CSS = """
    PermissionPrompt {
        border: solid $warning;
        background: $surface-darken-2;
        padding: 1 2;
        height: auto;
        margin: 1 0;
    }
    PermissionPrompt:focus { border: solid $accent; }
    .pp-tool-line { color: $warning;    height: 1; }
    .pp-detail    { color: $text-muted; height: 1; }
    .pp-options   { height: 1; margin-top: 1; }
    .pp-hint      { color: $text-muted; height: 1; }
    """

    # Option definitions: (label, allow, always)
    _OPTIONS: list[tuple[str, bool, bool]] = [
        ("Approve",           True,  False),
        ("Approve+Remember",  True,  True),
        ("Deny",              False, False),
    ]

    class Decision(Message):
        """Bubbles to the App when the user makes a decision."""
        def __init__(
            self,
            agent_widget: "AgentWidget",
            session: AgentSession,
            request_id: str,
            tool_name: str,
            tool_detail: str,
            allow: bool,
            always: bool,
        ) -> None:
            self.agent_widget = agent_widget
            self.session      = session
            self.request_id   = request_id
            self.tool_name    = tool_name
            self.tool_detail  = tool_detail
            self.allow        = allow
            self.always       = always
            super().__init__()

    def __init__(
        self,
        agent_widget: "AgentWidget",
        session: AgentSession,
        request: dict,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._agent   = agent_widget
        self._session = session
        self._request = request
        self._sel     = 0   # currently highlighted option index

    def compose(self) -> ComposeResult:
        tool   = self._request.get("tool_name", "unknown")
        inp    = self._request.get("tool_input", {})
        detail = (
            inp.get("command")
            or inp.get("file_path")
            or inp.get("path")
            or self._request.get("_text", "")
        )[:80]
        yield Label(f" Claude wants to use: [bold]{tool}[/bold]", classes="pp-tool-line")
        if detail:
            yield Label(f" {detail}", classes="pp-detail")
        yield Static(self._render_options(), id="pp-options", classes="pp-options")
        yield Label(" ◄/► select  Enter=confirm  y=approve  n=deny", classes="pp-hint")

    def on_mount(self) -> None:
        self.focus()

    def _render_options(self) -> str:
        parts = []
        for i, (label, _, _) in enumerate(self._OPTIONS):
            if i == self._sel:
                if i == 2:   # Deny
                    parts.append(f"[bold red]❯ {label}[/bold red]")
                elif i == 1:  # Approve+Remember
                    parts.append(f"[bold yellow]❯ {label}[/bold yellow]")
                else:         # Approve
                    parts.append(f"[bold green]❯ {label}[/bold green]")
            else:
                parts.append(f"[dim]  {label}[/dim]")
        return "   ".join(parts)

    def _refresh_options(self) -> None:
        self.query_one("#pp-options", Static).update(self._render_options())

    def on_key(self, event: Key) -> None:
        key = event.key
        if key in ("right", "l"):
            self._sel = (self._sel + 1) % len(self._OPTIONS)
            self._refresh_options()
            event.stop()
        elif key in ("left", "h"):
            self._sel = (self._sel - 1) % len(self._OPTIONS)
            self._refresh_options()
            event.stop()
        elif key in ("enter", "y"):
            if key == "y":
                self._sel = 0   # Approve
            self._confirm()
            event.stop()
        elif key == "n":
            self._sel = 2       # Deny
            self._confirm()
            event.stop()

    def _confirm(self) -> None:
        label, allow, always = self._OPTIONS[self._sel]
        tool   = self._request.get("tool_name", "unknown")
        rid    = self._request.get("request_id", "")
        inp    = self._request.get("tool_input", {})
        detail = (inp.get("command") or inp.get("file_path") or inp.get("path") or "")[:80]
        # Post the Decision BEFORE removing.  Textual messages bubble through
        # self.parent; if the removal is processed first, self.parent becomes
        # None and the Decision never reaches VibeCLIApp._permission_decision.
        self.post_message(
            self.Decision(self._agent, self._session, rid, tool, detail, allow, always)
        )
        self.remove()


# ---------------------------------------------------------------------------
# AgentWidget helpers
# ---------------------------------------------------------------------------

def _fmt_elapsed(secs: float) -> str:
    """Return a compact human-readable elapsed-time string (e.g. '42s', '1m 06s')."""
    s = int(secs)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s:02d}s"


def _short_model(model: str) -> str:
    """Shorten a model ID for display (e.g. 'claude-sonnet-4-6' → 'sonnet-4-6')."""
    if "/" in model:
        model = model.split("/")[-1]
    if model.startswith("claude-"):
        model = model[len("claude-"):]
    return model


# ---------------------------------------------------------------------------
# AgentWidget — one Claude session
# ---------------------------------------------------------------------------

class AgentWidget(Static):
    """
    Displays a Claude agent conversation.

    During a run: RichLog streams output (fast).
    After completion: TextArea (read-only) replaces the log so the user can
    select and copy text with standard keyboard shortcuts (Ctrl/Cmd+C).
    Replies append to the same box via --resume; no new widget is created.
    """

    DEFAULT_CSS = """
    AgentWidget {
        border: solid $primary-darken-2;
        margin: 0 0 1 0;
        height: auto;
        min-height: 7;
        max-height: 36;
    }
    AgentWidget.agent-selected {
        border: solid $accent;
    }
    AgentWidget.agent-verbose {
        max-height: 80;
    }
    .agent-header {
        background: $surface;
        color: $text-muted;
        height: 1;
        padding: 0 1;
    }
    .agent-meta {
        color: $accent;
        height: 1;
        padding: 0 1;
    }
    .agent-log { height: 12; background: $background; }
    .agent-log.agent-verbose { height: 40; }
    .agent-ta  {
        height: 12;
        background: $background;
        border: none;
        padding: 0;
    }
    .agent-ta.agent-verbose  { height: 40; }
    .agent-status-running { color: $warning;  height: 1; padding: 0 1; }
    .agent-status-done    { color: $success;  height: 1; padding: 0 1; }
    .agent-status-error   { color: $error;    height: 1; padding: 0 1; }
    .agent-reply {
        height: 3;
        border: tall $accent-darken-2;
        background: $surface;
        display: none;
    }
    .slash-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
        display: none;
    }
    .agent-perm-indicator {
        height: 1;
        padding: 0 2;
        color: $text-disabled;
        background: $surface;
        display: none;
    }
    """

    class Complete(Message):
        def __init__(self, agent_widget: "AgentWidget", exit_code: int) -> None:
            self.agent_widget = agent_widget
            self.exit_code = exit_code
            super().__init__()

    def __init__(self, session: AgentSession, number: int,
                 vault: "MemoryVault | None" = None,
                 restore: "dict | None" = None,
                 agent_type: str = "claude",
                 verbose: bool = False,
                 **kwargs) -> None:
        super().__init__(**kwargs)
        self.session  = session          # original session — stable, never replaced
        self.number   = number
        self._vault   = vault
        self._restore = restore          # saved state dict; if set, skip running
        self._agent_type = agent_type    # "claude" | "codex" | "cursor"
        self._verbose = verbose          # expanded output height
        self._log:    AgentLog | None = None
        self._ta:           SelectableLog | None = None
        self._status:       Label | None = None
        self._meta:         Label | None = None
        self._slash_hint:   Label | None = None
        self._perm_ind:     Label | None = None
        self._current_perm_mode: str = session.permission_mode
        self._in_code_fence = False
        self._fence_lang    = ""
        self._full_lines:   list[str] = []   # accumulates all output for TextArea
        self._active_session: AgentSession = session  # latest session (updated on reply)

    def compose(self) -> ComposeResult:
        sid   = self.session.session_id
        short = self.session.prompt[:55] + ("…" if len(self.session.prompt) > 55 else "")
        yield Label(f"[bold]#{self.number}[/bold]  {short}", classes="agent-header")
        agent_str = _AGENT_DISPLAY.get(self._agent_type, self._agent_type.title())
        override  = self.session.model_override
        meta_text = f"{agent_str}  ·  {_short_model(override)}" if override else agent_str
        yield Label(meta_text, id=f"agent-meta-{sid}", classes="agent-meta")
        yield AgentLog(id=f"agent-log-{sid}", wrap=True, highlight=False,
                      markup=False, classes="agent-log")
        yield SelectableLog("", read_only=True, id=f"agent-ta-{sid}", classes="agent-ta")
        yield Label("⟳ Running…", classes="agent-status-running",
                    id=f"agent-status-{sid}")
        yield Input(
            placeholder="↵ reply… (/btw context · /compact · follow-up)",
            id=f"agent-reply-{sid}",
            classes="agent-reply",
        )
        yield Label("", id=f"slash-hint-{sid}",   classes="slash-hint")
        yield Label("", id=f"agent-perm-{sid}", classes="agent-perm-indicator")
        proj_name = os.path.basename(self.session.project_path.rstrip("/"))
        yield AgentMemoryWidget(self.session.prompt, self._vault,
                                project=proj_name,
                                id=f"agent-mem-{sid}")

    def on_mount(self) -> None:
        sid               = self.session.session_id
        self._log         = self.query_one(f"#agent-log-{sid}",    AgentLog)
        self._ta          = self.query_one(f"#agent-ta-{sid}",     SelectableLog)
        self._status      = self.query_one(f"#agent-status-{sid}", Label)
        self._meta        = self.query_one(f"#agent-meta-{sid}",   Label)
        self._slash_hint  = self.query_one(f"#slash-hint-{sid}",   Label)
        self._perm_ind    = self.query_one(f"#agent-perm-{sid}",   Label)
        self._ta.display = False          # hidden until first completion
        # Show permission indicator immediately — visible during run and after
        proj = os.path.basename(self.session.project_path.rstrip("/") or ".")
        self._perm_ind.update(_perm_indicator_text(self._current_perm_mode, proj))
        self._perm_ind.display = True
        if self._verbose:
            self._apply_verbose(True)
        if self._restore:
            self._restore_state()
        else:
            self._run_session()

    # ------------------------------------------------------------------ verbose toggle

    def toggle_verbose(self) -> None:
        """Toggle expanded/compact output height for this agent (ctrl+o)."""
        self._verbose = not self._verbose
        self._apply_verbose(self._verbose)

    def _apply_verbose(self, verbose: bool) -> None:
        if verbose:
            self.add_class("agent-verbose")
            if self._log: self._log.add_class("agent-verbose")
            if self._ta:  self._ta.add_class("agent-verbose")
        else:
            self.remove_class("agent-verbose")
            if self._log: self._log.remove_class("agent-verbose")
            if self._ta:  self._ta.remove_class("agent-verbose")

    # ------------------------------------------------------------------ perm indicator

    def update_perm_indicator(self, mode: str) -> None:
        """Called by the app when permission mode is cycled globally."""
        self._current_perm_mode = mode
        if self._perm_ind is not None and self._perm_ind.display:
            proj = os.path.basename(self.session.project_path.rstrip("/") or ".")
            self._perm_ind.update(_perm_indicator_text(mode, proj))

    # ------------------------------------------------------------------ restore

    def _restore_state(self) -> None:
        """Populate from saved data without running the subprocess."""
        output = (self._restore or {}).get("output", "")
        if output:
            self._full_lines = output.split("\n")
            if self._log:
                for line in self._full_lines:
                    self._log.write(line)
        exit_code = self.session.exit_code
        if exit_code is None:
            exit_code = -1
        self._mark_complete(exit_code, restored=True)

    def to_state(self) -> dict:
        """Serialize current widget state for session persistence."""
        from core.session_store import SessionStore
        active = self._active_session
        return {
            "number":             self.number,
            "prompt":             self.session.prompt,
            "session_id":         self.session.session_id,
            "captured_session_id": (active.captured_session_id
                                    or self.session.captured_session_id),
            "project_path":       self.session.project_path,
            "permission_mode":    self.session.permission_mode,
            "agent_type":         self._agent_type,
            "exit_code":          active.exit_code,
            "output":             SessionStore.cap_output(self._full_lines),
        }

    # ------------------------------------------------------------------ streaming

    @work(exclusive=False)
    async def _run_session(self) -> None:
        # Hide the reply input while the initial session runs so that
        # _find_running_agent() can correctly identify this widget as running
        # (it checks reply.display == False; _mark_complete re-shows it when done).
        sid = self.session.session_id
        try:
            self.query_one(f"#agent-reply-{sid}", Input).display = False
        except Exception:
            pass
        self._ticker()
        exit_code = await self._stream(self.session)
        self._mark_complete(exit_code)

    async def _stream(self, session: ClaudeSession) -> int:
        """Stream a session into the RichLog, return exit code."""
        def append(line: str) -> None:
            self._full_lines.append(line)
            if self._log is not None:
                self._log.write(line)
            cmd = self._check_for_command(line)
            if cmd:
                self.post_message(CommandDetected(cmd))
            # Refresh elapsed + token count on every output line
            if self._status:
                elapsed_str = _fmt_elapsed(session.elapsed)
                tokens = session.output_tokens
                if tokens:
                    self._status.update(f"⟳ Running…  ({elapsed_str} · ↑ {tokens:,} tokens)")
                else:
                    self._status.update(f"⟳ Running…  ({elapsed_str})")
            # Update agent/model label once the session reports its model name
            if self._meta and session.active_model:
                agent_str = _AGENT_DISPLAY.get(self._agent_type, self._agent_type.title())
                self._meta.update(f"{agent_str}  ·  {_short_model(session.active_model)}")

        def on_perm(request: dict) -> None:
            prompt_widget = PermissionPrompt(self, session, request)
            if self._status:
                self.mount(prompt_widget, before=self._status)

        return await session.run(on_line=append, on_permission_request=on_perm)

    # ------------------------------------------------------------------ live status ticker

    @work(exclusive=False)
    async def _ticker(self) -> None:
        """Tick once per second while a session is running; self-terminates when done."""
        import asyncio as _asyncio
        session = self._active_session   # capture the session we're ticking for
        while True:
            await _asyncio.sleep(1.0)
            # Stop if the session finished or a newer session replaced it
            if session.is_done or self._active_session is not session:
                break
            if self._status is None:
                break
            elapsed_str = _fmt_elapsed(session.elapsed)
            tokens = session.output_tokens
            if tokens:
                self._status.update(f"⟳ Running…  ({elapsed_str} · ↑ {tokens:,} tokens)")
            else:
                self._status.update(f"⟳ Running…  ({elapsed_str})")

    # ------------------------------------------------------------------ completion

    def _mark_complete(self, exit_code: int, restored: bool = False) -> None:
        sid = self.session.session_id

        if self._status:
            if exit_code == 0:
                if restored:
                    suffix = "restored"
                else:
                    elapsed_str = _fmt_elapsed(self._active_session.elapsed)
                    tokens = self._active_session.output_tokens
                    token_str = f" · ↑ {tokens:,} tokens" if tokens else ""
                    suffix = f"{elapsed_str}{token_str}"
                self._status.update(f"✓ Done  ({suffix})")
                self._status.remove_class("agent-status-running")
                self._status.add_class("agent-status-done")
            elif exit_code == -1:
                self._status.update("⚠ Interrupted  (session restored)")
                self._status.remove_class("agent-status-running")
                self._status.add_class("agent-status-error")
            else:
                self._status.update(f"✗ Failed (exit {exit_code})")
                self._status.remove_class("agent-status-running")
                self._status.add_class("agent-status-error")

        # Switch to selectable TextArea
        if self._log and self._ta:
            self._log.display = False
            self._ta.load_text("\n".join(self._full_lines))
            self._ta.display = True
            self._ta.scroll_end(animate=False)

        # Show reply input (perm indicator is already visible from on_mount)
        reply_input = self.query_one(f"#agent-reply-{sid}", Input)
        reply_input.display = True

        self.post_message(self.Complete(self, exit_code))

    # ------------------------------------------------------------------ slash hint

    @on(Input.Changed)
    def _reply_changed(self, event: Input.Changed) -> None:
        """Show passive slash-command hint while user types / commands."""
        if not (event.input.id or "").startswith("agent-reply-"):
            return
        if self._slash_hint is None:
            return
        value = event.value
        if value.startswith("/"):
            hint = _slash_hint_text(value.split()[0] if value.split() else value)
            if hint:
                self._slash_hint.update(hint)
                self._slash_hint.display = True
                return
        self._slash_hint.display = False

    # ------------------------------------------------------------------ reply / multi-turn

    @on(Input.Submitted)
    def _reply_submitted(self, event: Input.Submitted) -> None:
        if not (event.input.id or "").startswith("agent-reply-"):
            return
        reply = event.value.strip()
        if not reply:
            return
        event.input.value = ""
        if self._slash_hint is not None:
            self._slash_hint.display = False
        # Add to shared history and clear browse state for this input
        hist = getattr(self.app, "_prompt_history", None)
        if hist is not None:
            t = reply.strip()
            if t and (not hist or hist[-1] != t):
                hist.append(t)
        hist_browse = getattr(self.app, "_hist_browse", None)
        if hist_browse is not None:
            hist_browse.pop(event.input.id, None)
        self._run_reply(reply)

    @work(exclusive=False)
    async def _run_reply(self, reply: str) -> None:
        """Continue the conversation in-place."""
        sid = self.session.session_id

        # Switch back to streaming log
        if self._ta and self._log:
            self._ta.display = False
            self._log.display = True

        # Show separator in log
        sep = f"─── ↵ {reply[:60]} ───"
        self._full_lines += ["", sep, ""]
        if self._log:
            self._log.write("")
            self._log.write(sep)
            self._log.write("")

        # Reset status to running
        if self._status:
            self._status.update("⟳ Running…")
            self._status.remove_class("agent-status-done", "agent-status-error")
            self._status.add_class("agent-status-running")

        # Hide reply input while running (perm indicator stays visible)
        reply_input = self.query_one(f"#agent-reply-{sid}", Input)
        reply_input.display = False

        # Use the stored agent type — never use type(self._active_session) since
        # restored sessions are RestoredSession which returns instantly on run().
        from terminal.claude_session   import ClaudeSession
        from terminal.codex_session    import CodexSession
        from terminal.cursor_session   import CursorSession
        from terminal.openclaw_session import OpenClawSession
        _cls_map = {"claude": ClaudeSession, "codex": CodexSession, "cursor": CursorSession, "openclaw": OpenClawSession}
        session_cls = _cls_map.get(self._agent_type, ClaudeSession)
        continuation = session_cls(
            prompt=reply,
            project_path=self._active_session.project_path,
            permission_mode=self._active_session.permission_mode,
            resume_session_id=self._active_session.captured_session_id,
            verbose_output=self._verbose,
        )
        self._active_session = continuation
        self._ticker()

        exit_code = await self._stream(continuation)
        self._mark_complete(exit_code)

    # ------------------------------------------------------------------ helpers

    def _check_for_command(self, line: str) -> str | None:
        stripped = line.strip()
        if stripped.startswith("```"):
            if self._in_code_fence:
                self._in_code_fence = False
                self._fence_lang = ""
            else:
                lang = stripped[3:].lower().strip()
                self._in_code_fence = lang in ("bash", "shell", "sh", "zsh")
                self._fence_lang = lang
            return None
        if self._in_code_fence and stripped:
            return stripped
        if stripped.startswith("$ "):
            return stripped[2:]
        return None


# ---------------------------------------------------------------------------
# AgentPanel
# ---------------------------------------------------------------------------

class AgentPanel(Static, can_focus=True):
    """
    Scrollable stack of agent widgets, isolated per project.
    Each project gets its own ScrollableContainer; switching projects
    hides the old one and shows (or creates) the new one.
    can_focus=True so it holds focus in command mode (keys bubble to App).
    """

    DEFAULT_CSS = """
    AgentPanel {
        width: 1fr;
        background: $surface;
    }
    .ap-header {
        height: 1;
        background: $primary-darken-3;
        color: $text-muted;
        padding: 0 1;
    }
    .ap-scroll { height: 1fr; }
    .ap-empty {
        color: $text-muted;
        padding: 1 2;
    }
    """

    class AgentComplete(Message):
        def __init__(self, agent_widget: AgentWidget, exit_code: int) -> None:
            self.agent_widget = agent_widget
            self.exit_code = exit_code
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._active_project: str = ""
        self._project_counts: dict[str, int] = {}
        self._selected_idx: dict[str, int] = {}  # project → selected agent index (-1 = none)

    def compose(self) -> ComposeResult:
        yield Label(" AGENTS  [dim](n = new prompt · x = cancel · d = detach  r = reattach · j/k = scroll)[/dim]",
                    classes="ap-header")
        # Per-project ScrollableContainers are created lazily in switch_project()

    @staticmethod
    def _safe_id(project_name: str) -> str:
        import re as _re
        return _re.sub(r"[^a-zA-Z0-9_-]", "_", project_name)

    def _container_id(self, project_name: str) -> str:
        return f"ap-scroll-{self._safe_id(project_name)}"

    def switch_project(self, project_name: str) -> None:
        """Hide all project containers and show (or create) the one for project_name."""
        for c in self.query(".ap-project-scroll"):
            c.display = False

        cid = self._container_id(project_name)
        try:
            container = self.query_one(f"#{cid}", ScrollableContainer)
        except Exception:
            container = ScrollableContainer(id=cid, classes="ap-scroll ap-project-scroll")
            self.mount(container)
            container.mount(Static(
                "  No agents yet.\n\n  Press [bold]n[/bold] or [bold]Enter[/bold] to type a prompt.",
                classes="ap-empty ap-empty-hint",
            ))

        container.display = True
        self._active_project = project_name
        self._update_hint()

    def _active_container(self) -> ScrollableContainer:
        cid = self._container_id(self._active_project)
        return self.query_one(f"#{cid}", ScrollableContainer)

    def _update_hint(self) -> None:
        """Show or hide the empty hint based on whether any AgentWidgets exist."""
        try:
            container = self._active_container()
        except Exception:
            return
        has_agents = bool(list(container.query(AgentWidget)))
        for hint in container.query(".ap-empty-hint"):
            hint.display = not has_agents

    def add_agent(self, session: AgentSession,
                  vault: "MemoryVault | None" = None,
                  agent_type: str = "claude",
                  verbose: bool = False) -> AgentWidget:
        container = self._active_container()
        self._project_counts[self._active_project] = (
            self._project_counts.get(self._active_project, 0) + 1
        )
        count  = self._project_counts[self._active_project]
        widget = AgentWidget(session, number=count, vault=vault,
                             agent_type=agent_type, verbose=verbose,
                             id=f"agent-{session.session_id}")
        container.mount(widget)
        container.scroll_end(animate=False)
        self._update_hint()
        return widget

    def _get_selected_idx(self) -> int:
        return self._selected_idx.get(self._active_project, -1)

    def _set_selected_idx(self, idx: int) -> None:
        agents = self.active_agents()
        if not agents:
            self._selected_idx[self._active_project] = -1
            return
        idx = max(0, min(idx, len(agents) - 1))
        self._selected_idx[self._active_project] = idx
        # Update highlight CSS class
        for i, w in enumerate(agents):
            if i == idx:
                w.add_class("agent-selected")
            else:
                w.remove_class("agent-selected")
        # Scroll selected widget into view
        agents[idx].scroll_visible()

    def selected_agent(self) -> "AgentWidget | None":
        agents = self.active_agents()
        if not agents:
            return None
        idx = self._get_selected_idx()
        if idx < 0:
            return agents[-1]  # default to last
        return agents[min(idx, len(agents) - 1)]

    def select_next(self) -> None:
        agents = self.active_agents()
        if not agents:
            return
        idx = self._get_selected_idx()
        self._set_selected_idx(idx + 1 if idx >= 0 else len(agents) - 1)

    def select_prev(self) -> None:
        agents = self.active_agents()
        if not agents:
            return
        idx = self._get_selected_idx()
        if idx <= 0:
            idx = 0
        else:
            idx -= 1
        self._set_selected_idx(idx)

    def detach_selected(self) -> "dict | None":
        """Remove the selected AgentWidget and return its saved state dict."""
        w = self.selected_agent()
        if w is None:
            return None
        state = w.to_state()
        state["agent_type"] = w._agent_type
        # Update selection
        agents = self.active_agents()
        idx = self._get_selected_idx()
        w.remove()
        remaining = self.active_agents()
        if remaining:
            new_idx = max(0, min(idx, len(remaining) - 1))
            self._set_selected_idx(new_idx)
        else:
            self._selected_idx[self._active_project] = -1
        prev = self._project_counts.get(self._active_project, 0)
        self._project_counts[self._active_project] = max(0, prev - 1)
        self._update_hint()
        return state

    def remove_last(self) -> None:
        try:
            container = self._active_container()
        except Exception:
            return
        agents = list(container.query(AgentWidget))
        if agents:
            agents[-1].remove()
            prev = self._project_counts.get(self._active_project, 0)
            self._project_counts[self._active_project] = max(0, prev - 1)
        self._update_hint()

    def cancel_last(self) -> None:
        try:
            container = self._active_container()
        except Exception:
            return
        agents = list(container.query(AgentWidget))
        for w in reversed(agents):
            if not w.session.is_done:
                w.session.cancel()
                break

    def cancel_all(self) -> None:
        """Cancel every running agent across all projects."""
        for w in self.query(AgentWidget):
            w.session.cancel()

    def clear_active(self) -> None:
        """Cancel and remove all AgentWidgets for the current project."""
        try:
            container = self._active_container()
            for w in list(container.query(AgentWidget)):
                w.session.cancel()
                w.remove()
        except Exception:
            pass
        self._project_counts[self._active_project] = 0
        self._selected_idx[self._active_project] = -1
        self._update_hint()

    def last_agent_context(self, n: int = 20) -> list[str]:
        """Return the output_tail of the most recent agent (used for /fork)."""
        agents = self.active_agents()
        if agents:
            tail = agents[-1].session.output_tail
            return tail[-n:] if len(tail) > n else tail
        return []

    def active_agents(self) -> list["AgentWidget"]:
        """Return AgentWidgets for the currently active project."""
        try:
            return list(self._active_container().query(AgentWidget))
        except Exception:
            return []

    def get_agents_for_project(self, project_name: str) -> list["AgentWidget"]:
        """Return AgentWidgets for any project (used during session save)."""
        cid = self._container_id(project_name)
        try:
            return list(self.query_one(f"#{cid}", ScrollableContainer).query(AgentWidget))
        except Exception:
            return []

    def restore_agents(
        self,
        project_name: str,
        agents_data: list[dict],
        vault: "MemoryVault | None",
    ) -> None:
        """Create AgentWidgets from saved session data for the given project."""
        from terminal.agent_session import RestoredSession
        # Ensure the project container exists (switch_project must have been called first)
        try:
            container = self._active_container()
        except Exception:
            return
        max_num = 0
        for data in agents_data:
            session = RestoredSession.from_saved(data)
            num     = data.get("number", max_num + 1)
            max_num = max(max_num, num)
            widget  = AgentWidget(
                session, number=num, vault=vault,
                restore=data,
                agent_type=data.get("agent_type", "claude"),
                id=f"agent-{session.session_id}",
            )
            container.mount(widget)
        self._project_counts[project_name] = max_num
        self._update_hint()

    def scroll_down(self) -> None:
        self.select_next()

    def scroll_up(self) -> None:
        self.select_prev()

    @on(AgentWidget.Complete)
    def _bubble(self, event: AgentWidget.Complete) -> None:
        self.post_message(self.AgentComplete(event.agent_widget, event.exit_code))


# ---------------------------------------------------------------------------
# TerminalPanel
# ---------------------------------------------------------------------------

class TerminalPanel(Static):
    """
    Embedded real PTY terminal panel, isolated per project.

    Each project gets its own PTYWidget; switching projects hides the old
    one and shows (or creates) the new one.  Press ctrl+t to toggle.
    """

    DEFAULT_CSS = """
    TerminalPanel {
        height: 18;
        border-top: solid $primary-darken-2;
        background: $background;
    }
    .tp-header { height: 1; background: $primary-darken-3; color: $text-muted; padding: 0 1; }
    PTYWidget   { height: 1fr; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._active_project: str = ""

    def compose(self) -> ComposeResult:
        yield Label(
            " TERMINAL  [dim](ctrl+t to close)[/dim]",
            classes="tp-header",
        )
        # Per-project PTYWidgets are created lazily in switch_project()

    @staticmethod
    def _safe_id(project_name: str) -> str:
        import re as _re
        return _re.sub(r"[^a-zA-Z0-9_-]", "_", project_name)

    def _pty_id(self, project_name: str) -> str:
        return f"tp-pty-{self._safe_id(project_name)}"

    def switch_project(self, project_name: str, cwd: str) -> None:
        """Hide all PTY widgets and show (or create) the one for project_name."""
        for pty in self.query(PTYWidget):
            pty.display = False

        pid = self._pty_id(project_name)
        try:
            pty = self.query_one(f"#{pid}", PTYWidget)
        except Exception:
            pty = PTYWidget(cwd=cwd, id=pid)
            self.mount(pty)

        pty.display = True
        self._active_project = project_name

    def _active_pty(self) -> PTYWidget:
        return self.query_one(f"#{self._pty_id(self._active_project)}", PTYWidget)

    def focus_input(self) -> None:
        try:
            self._active_pty().focus()
        except Exception:
            pass

    def run_command(self, cmd: str) -> None:
        """Type a command and press Enter in the active terminal."""
        try:
            self._active_pty().run_command(cmd)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# FileBrowserPanel — directory tree for the active project
# ---------------------------------------------------------------------------

class FileBrowserPanel(Static):
    """Left sidebar with a DirectoryTree for the active project. Toggle with f."""

    DEFAULT_CSS = """
    FileBrowserPanel {
        width: 26;
        border-right: solid $primary-darken-2;
        background: $surface;
    }
    .fb-header {
        height: 1;
        background: $primary-darken-3;
        color: $text-muted;
        padding: 0 1;
    }
    .fb-tree { height: 1fr; }
    .fb-footer {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    class FileSelected(Message):
        def __init__(self, path: str) -> None:
            self.path = path
            super().__init__()

    def __init__(self, root: str = ".", **kwargs) -> None:
        super().__init__(**kwargs)
        self._root = root

    def compose(self) -> ComposeResult:
        yield Label(" FILES  [dim](f=close · Enter=open)[/dim]", classes="fb-header")
        yield DirectoryTree(self._root, id="fb-tree", classes="fb-tree")
        yield Label(" ↑↓ navigate · Enter open file", classes="fb-footer")

    def set_root(self, path: str) -> None:
        self._root = path
        tree = self.query_one("#fb-tree", DirectoryTree)
        tree.path = Path(path)

    def focus_tree(self) -> None:
        self.query_one("#fb-tree", DirectoryTree).focus()

    @on(DirectoryTree.FileSelected)
    def _file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.post_message(self.FileSelected(str(event.path)))


# ---------------------------------------------------------------------------
# EditorPanel
# ---------------------------------------------------------------------------

# _AUDIO_EXTS imported from ui.constants


def _is_audio_file(path: str) -> bool:
    return Path(path).suffix.lower() in _AUDIO_EXTS


def _audio_annotation_path(audio_path: str) -> str:
    p = Path(audio_path)
    return str(p.with_name(f"{p.stem}.vibe-annotate.txt"))


def _format_hms(seconds: float) -> str:
    if seconds < 0:
        seconds = 0.0
    s = int(round(seconds))
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def _probe_audio_duration_sec(path: str) -> float | None:
    try:
        r = subprocess.run(
            [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", path,
            ],
            capture_output=True, text=True, timeout=8,
        )
        if r.returncode == 0 and r.stdout.strip():
            return float(r.stdout.strip())
    except Exception:
        pass
    if sys.platform == "darwin":
        try:
            r = subprocess.run(
                ["afinfo", path], capture_output=True, text=True, timeout=8,
            )
            m = re.search(r"estimated duration:\s*([\d.]+)\s*sec", r.stdout, re.I)
            if m:
                return float(m.group(1))
        except Exception:
            pass
    return None


def _audio_meta_line(path: str) -> str:
    p = Path(path)
    try:
        st = p.stat()
        size_kb = st.st_size / 1024.0
        size_s = f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024.0:.1f} MB"
    except OSError:
        size_s = "?"
    dur = _probe_audio_duration_sec(path)
    dur_s = _format_hms(dur) if dur is not None else "duration unknown"
    return f" [dim]{size_s} · {dur_s} · notes → {Path(_audio_annotation_path(path)).name}[/dim]"


class EditorPanel(Static):
    """Read-only file viewer.

    Audio files open an annotator: timestamped notes live in a sidecar
    ``<name>.vibe-annotate.txt`` next to the file. Command mode ``p`` plays
    the file (afplay / ffplay) when this panel is visible.
    """

    DEFAULT_CSS = """
    EditorPanel {
        width: 50%;
        border-right: solid $primary-darken-2;
        background: $background;
    }
    .ep-header {
        height: 1;
        background: $primary-darken-3;
        color: $text-muted;
        padding: 0 1;
    }
    .ep-audio-meta {
        height: auto;
        max-height: 3;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    /* read-only syntax-highlighted view */
    #ep-scroll { height: 1fr; overflow-y: auto; }
    #ep-view   { width: 1fr; }
    /* audio annotation TextArea */
    #ep-area   { height: 1fr; }
    .ep-lint {
        height: auto;
        max-height: 8;
        background: $surface;
        border-top: solid $error-darken-2;
        padding: 0 1;
        overflow-y: auto;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._current_path = ""
        self._audio_mode   = False
        self._in_view_mode = False  # True = Rich Syntax shown; False = TextArea shown

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(" (no file)  [dim]e=close[/dim]",
                        id="ep-label", classes="ep-header")
            yield Static("", id="ep-audio-meta", classes="ep-audio-meta")
            # Read-only view — Rich Syntax rendered by Pygments (works on all Python versions)
            with ScrollableContainer(id="ep-scroll"):
                yield Static("", id="ep-view", markup=False)
            # Audio annotation TextArea (shown only for audio files)
            yield TextArea("", id="ep-area", read_only=False, show_line_numbers=True)
            yield Static("", id="ep-lint", classes="ep-lint")

    def on_mount(self) -> None:
        self.query_one("#ep-audio-meta", Static).display = False
        self.query_one("#ep-lint",       Static).display = False
        self.query_one("#ep-area",     TextArea).display = False

    # ------------------------------------------------------------------ load

    def load_file(self, path: str) -> None:
        self._current_path = path
        self._audio_mode   = _is_audio_file(path)
        label = self.query_one("#ep-label",     Label)
        meta  = self.query_one("#ep-audio-meta", Static)
        ta    = self.query_one("#ep-area",       TextArea)
        sc    = self.query_one("#ep-scroll",     ScrollableContainer)

        if self._audio_mode:
            meta.display = True
            meta.update(_audio_meta_line(path))
            label.update(
                f" {os.path.basename(path)}  "
                "[dim]audio · p=play · e=close[/dim]"
            )
            ann = _audio_annotation_path(path)
            try:
                content = (
                    Path(ann).read_text(encoding="utf-8", errors="replace")
                    if os.path.isfile(ann)
                    else "# Audio notes (plain text)\n"
                         "# Use timestamps like 0:42 or 1:23:05 — one line per cue.\n\n"
                )
            except Exception as e:
                content = f"[Error loading notes: {e}]"
            # Audio annotation: editable TextArea
            ta.load_text(content)
            ta.read_only = False
            sc.display = False
            ta.display = True
        else:
            meta.display = False
            meta.update("")
            label.update(
                f" {os.path.basename(path)}  [dim]s=save · Esc=view · e=close[/dim]"
            )
            try:
                content = Path(path).read_text(errors="replace")
            except Exception as e:
                content = f"[Error: {e}]"
            ta.load_text(content)
            _set_ta_language(ta, _language_for(path))
            ta.read_only = False
            sc.display = False
            ta.display = True
            self._in_view_mode = False

        self._start_lint()

    def save(self) -> bool:
        if not self._current_path:
            return False
        ta     = self.query_one("#ep-area", TextArea)
        target = _audio_annotation_path(self._current_path) if self._audio_mode else self._current_path
        try:
            Path(target).write_text(ta.text, encoding="utf-8")
            self._start_lint()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ syntax view

    def _render_to_view(self, content: str) -> None:
        """Render *content* into the read-only Rich Syntax pane."""
        from rich.syntax import Syntax
        view = self.query_one("#ep-view", Static)
        lang = _language_for(self._current_path)
        if lang:
            try:
                syn = Syntax(
                    content, lang,
                    theme=self._pygments_theme(),
                    line_numbers=True,
                    word_wrap=False,
                    indent_guides=True,
                )
                view.update(syn)
                return
            except Exception:
                pass
        # Fallback: plain text (escape markup so Rich doesn't interpret it)
        from rich.text import Text
        view.update(Text(content))

    def _pygments_theme(self) -> str:
        """Return a Pygments style name that matches the current app theme."""
        app_theme = getattr(self.app, "theme", "textual-dark")
        return _APP_TO_PYGMENTS_THEME.get(app_theme, "monokai")

    def switch_to_view_mode(self) -> None:
        """Save content and display the colorized Rich Syntax read-only view."""
        ta  = self.query_one("#ep-area",   TextArea)
        sc  = self.query_one("#ep-scroll", ScrollableContainer)
        self._render_to_view(ta.text)
        ta.display = False
        sc.display = True
        self._in_view_mode = True
        if self._current_path:
            label = self.query_one("#ep-label", Label)
            label.update(
                f" {os.path.basename(self._current_path)}  "
                "[dim]e=close[/dim]"
            )

    def enter_edit_mode(self) -> None:
        """Switch from the colorized view back to the editable TextArea."""
        ta  = self.query_one("#ep-area",   TextArea)
        sc  = self.query_one("#ep-scroll", ScrollableContainer)
        sc.display = False
        ta.display = True
        self._in_view_mode = False
        if self._current_path:
            label = self.query_one("#ep-label", Label)
            label.update(
                f" {os.path.basename(self._current_path)}  "
                "[dim]s=save · Esc=view · e=close[/dim]"
            )
        ta.focus()

    # ------------------------------------------------------------------ linting

    def _start_lint(self) -> None:
        """Kick off a background lint run if the current file is lintable."""
        if not self._current_path or self._audio_mode:
            return
        if Path(self._current_path).suffix.lower() not in _LINTABLE_EXTS:
            self.query_one("#ep-lint", Static).display = False
            return
        path = self._current_path

        def _run() -> None:
            try:
                issues = _lint_file(path)
            except Exception:
                issues = []
            self.call_from_thread(self._display_lint, path, issues)

        threading.Thread(target=_run, daemon=True).start()

    def _display_lint(self, path: str, issues: list[LintIssue]) -> None:
        """Render lint results; called on the main thread via call_from_thread."""
        # Guard: file may have changed since the lint started
        if path != self._current_path:
            return
        lint_bar = self.query_one("#ep-lint", Static)
        if not issues:
            lint_bar.display = False
            return

        errors   = [i for i in issues if i.severity == "error"]
        warnings = [i for i in issues if i.severity == "warning"]

        parts: list[str] = []
        if errors:
            parts.append(f"[bold red]{len(errors)} error{'s' if len(errors) != 1 else ''}[/bold red]")
        if warnings:
            parts.append(f"[bold yellow]{len(warnings)} warning{'s' if len(warnings) != 1 else ''}[/bold yellow]")
        lines = ["  ".join(parts)]

        for issue in issues[:8]:
            color = "red" if issue.severity == "error" else "yellow"
            loc = f"L{issue.line}" + (f":{issue.col}" if issue.col else "")
            lines.append(f"  [{color}]{loc}[/{color}]  {issue.message}")

        if len(issues) > 8:
            lines.append(f"  [dim]… {len(issues) - 8} more issue(s)[/dim]")

        lint_bar.update("\n".join(lines))
        lint_bar.display = True

    def try_play_audio(self) -> bool:
        """If the current file is audio, spawn a system player. Returns True if handled."""
        if not self._audio_mode or not self._current_path:
            return False
        path = self._current_path
        try:
            if sys.platform == "darwin":
                subprocess.Popen(
                    ["afplay", path],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
            else:
                last_err: Exception | None = None
                for cmd in (
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path],
                    ["mpv", "--no-video", path],
                ):
                    try:
                        subprocess.Popen(
                            cmd,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        break
                    except FileNotFoundError as e:
                        last_err = e
                else:
                    raise FileNotFoundError from last_err
        except FileNotFoundError:
            self.app.notify(
                "No player found (install ffmpeg/ffplay or mpv; macOS has afplay).",
                title="Audio",
                timeout=4,
            )
            return True
        except Exception as e:
            self.app.notify(f"Could not play: {e}", title="Audio", timeout=4)
            return True
        self.app.notify(f"Playing {os.path.basename(path)}", title="Audio", timeout=2)
        return True

    @property
    def current_path(self) -> str:
        return self._current_path

    @property
    def is_audio_mode(self) -> bool:
        return self._audio_mode


# ---------------------------------------------------------------------------
# GraphPane
# ---------------------------------------------------------------------------

class GraphPane(Static):
    """Memory / knowledge graph as a navigable tree. Toggle with m."""

    DEFAULT_CSS = """
    GraphPane {
        width: 1fr;
        height: 1fr;
        background: $background;
    }
    .gp-header {
        height: 1;
        background: $primary-darken-3;
        color: $text-muted;
        padding: 0 1;
    }
    .gp-tree  { height: 1fr; }
    .gp-footer {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    class NodeOpened(Message):
        def __init__(self, path: str) -> None:
            self.path = path
            super().__init__()

    def __init__(self, vault: MemoryVault | None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._vault = vault

    def compose(self) -> ComposeResult:
        yield Label(" MEMORY GRAPH", classes="gp-header")
        yield Tree("Notes", id="gp-tree", classes="gp-tree")
        yield Label(" ↑↓ navigate · Enter open in editor · m back",
                    classes="gp-footer")

    def on_mount(self) -> None:
        self._populate()

    def _populate(self) -> None:
        tree = self.query_one("#gp-tree", Tree)
        tree.clear()
        if not self._vault:
            tree.root.add_leaf("(no vault)")
            tree.root.expand()
            return

        # --- Per-project branches ---
        projects = self._vault.list_projects()
        if projects:
            proj_root = tree.root.add("[bold]Projects[/bold]")
            for proj in sorted(projects):
                pnode = proj_root.add(f"[cyan]{proj}[/cyan]")
                # Project profile (if it exists)
                prof_path = os.path.join(
                    self._vault.root, "projects", proj, "profile.md"
                )
                if os.path.isfile(prof_path):
                    leaf = pnode.add_leaf("[dim]Profile[/dim]")
                    leaf.data = prof_path
                # Recent run logs (last 8, newest first)
                proj_notes = self._vault.get_project_notes(proj)
                run_logs = sorted(
                    [n for n in proj_notes if "run_log" in n.tags],
                    key=lambda n: n.modified_at,
                    reverse=True,
                )
                for note in run_logs[:8]:
                    label = note.title
                    # Show tags after the title if present
                    semantic = [t for t in note.tags
                                if t not in ("run_log", proj, "run_outputs")]
                    if semantic:
                        label += f"  [dim]{' '.join('#'+t for t in semantic[:3])}[/dim]"
                    leaf = pnode.add_leaf(label)
                    leaf.data = note.path
                proj_root.expand()
                pnode.expand()

        # --- User profile ---
        prof_path = os.path.join(self._vault.root, "user", "profile.md")
        if os.path.isfile(prof_path):
            leaf = tree.root.add_leaf("[bold]User Profile[/bold]")
            leaf.data = prof_path

        # --- MOCs (collapsed by default) ---
        mocs = self._vault.list_mocs()
        if mocs:
            moc_root = tree.root.add("[bold]MOCs[/bold]")
            for moc in sorted(mocs, key=lambda m: m.title):
                leaf = moc_root.add_leaf(f"[dim]{moc.title}[/dim]")
                leaf.data = moc.path

        tree.root.expand()

    @on(Tree.NodeSelected)
    def _selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data:
            self.post_message(self.NodeOpened(event.node.data))


# ---------------------------------------------------------------------------
# ProjectTabBar
# ---------------------------------------------------------------------------

class ProjectTabBar(Static):
    """macOS Terminal-style project tab bar.

    Layout (height=3):
      ┌──────────────┐ ┌──────────────┐   ┌──────────────┐  ⊕
      │  1 · project │ │  2 · other   │   │  3 · third   │
      └──────────────┘ └──────────────┘   └──────────────┘

    Active tab shares background with content area giving the "open tab"
    illusion.  Inactive tabs are recessed in $panel.
    """

    DEFAULT_CSS = """
    ProjectTabBar {
        height: 3;
        background: $panel-darken-2;
        layout: horizontal;
        align: left bottom;
    }

    /* ── inactive tab ─────────────────────────────────────── */
    ProjectTabBar Button.tab {
        height: 3;
        min-width: 16;
        max-width: 28;
        padding: 0 2;
        background: $panel-darken-1;
        color: $text-muted;
        border-top: solid $panel-darken-3;
        border-bottom: solid $panel-darken-2;
        border-left: none;
        border-right: solid $panel-darken-3;
        content-align: center middle;
    }

    /* ── active tab ───────────────────────────────────────── */
    ProjectTabBar Button.tab.active {
        background: $surface;
        color: $text;
        text-style: bold;
        border-top: wide $accent;
        border-bottom: solid $surface;
        border-left: none;
        border-right: solid $panel-darken-3;
    }

    /* ── + / new-project button ───────────────────────────── */
    ProjectTabBar Button.tab-add {
        height: 3;
        min-width: 5;
        padding: 0 2;
        background: $panel-darken-2;
        color: $text-muted;
        border: none;
        content-align: center middle;
    }
    ProjectTabBar Button.tab-add:hover {
        color: $text;
    }

    /* ── empty-state hint ─────────────────────────────────── */
    ProjectTabBar .tab-hint {
        height: 3;
        color: $text-muted;
        padding: 0 3;
        width: 1fr;
        content-align: left middle;
    }
    """

    _MAX_NAME = 16   # chars before truncation

    class TabPressed(Message):
        def __init__(self, idx: int) -> None:
            self.idx = idx
            super().__init__()

    class AddPressed(Message):
        pass

    def __init__(self, projects: list[Project], active_idx: int, **kwargs) -> None:
        super().__init__(**kwargs)
        self._projects = projects
        self._active   = active_idx

    # ── label helpers ─────────────────────────────────────────────────

    @staticmethod
    def _trunc(name: str, limit: int) -> str:
        return name if len(name) <= limit else name[: limit - 1] + "…"

    def _tab_label(self, i: int, name: str) -> str:
        num  = str(i + 1) if i < 9 else "·"
        disp = self._trunc(name, self._MAX_NAME)
        return f"{num}  {disp}"

    # ── compose ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield Label(
            "  No projects  ·  press [bold]o[/bold] to open a directory",
            id="tb-hint",
            classes="tab-hint",
        )
        yield from self._make_buttons()

    def _make_buttons(self):
        for i, p in enumerate(self._projects):
            cls = "tab active" if i == self._active else "tab"
            btn = Button(self._tab_label(i, p.display_name), id=f"ptab-{i}", classes=cls)
            btn.can_focus = False
            yield btn
        add_btn = Button("⊕", id="ptab-add", classes="tab-add")
        add_btn.can_focus = False
        yield add_btn

    # ── update ───────────────────────────────────────────────────────

    def refresh_tabs(self, projects: list[Project], active_idx: int) -> None:
        self._projects = projects
        self._active   = active_idx

        hint = self.query_one("#tb-hint", Label)
        hint.display = not bool(projects)

        existing = {b.id: b for b in self.query(Button)}
        new_btns  = list(self._make_buttons())
        new_ids   = {b.id for b in new_btns}

        for bid, btn in list(existing.items()):
            if bid not in new_ids:
                btn.remove()

        for btn in new_btns:
            if btn.id in existing:
                old = existing[btn.id]
                old.label = btn.label
                old.set_class("active" in btn.classes, "active")
            else:
                self.mount(btn)

    def on_mount(self) -> None:
        self.query_one("#tb-hint", Label).display = not bool(self._projects)

    # ── events ───────────────────────────────────────────────────────

    @on(Button.Pressed)
    def _pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id or ""
        if bid == "ptab-add":
            self.post_message(self.AddPressed())
        elif bid.startswith("ptab-"):
            self.post_message(self.TabPressed(int(bid[5:])))


# ---------------------------------------------------------------------------
# PromptBar — suggestion row + input (prompt mode)
# ---------------------------------------------------------------------------

class PromptBar(Static):
    """
    Bottom bar.  Typing is only active when the Input is focused (prompt mode).

    Two suggestion rows:
      Row 1  [1]–[5]  auto-generated by the personalization engine (read-only)
      Row 2  [6]–[0]  manually configured by the user
                       → while typing, ctrl+6…ctrl+0 saves current text to that slot

    Tab  cycles through auto suggestions.
    Escape / ,  blurs input → returns to command mode.
    """

    DEFAULT_CSS = """
    PromptBar {
        height: auto;
        min-height: 7;
        background: $surface;
        border-top: solid $accent;
    }
    .pb-auto-sugg {
        height: 1;
        background: $primary-darken-3;
        padding: 0 1;
        color: $text-muted;
    }
    .pb-manual-sugg {
        height: 1;
        background: $primary-darken-2;
        padding: 0 1;
        color: $text-muted;
    }
    .pb-sugg-label {
        color: $text-disabled;
        padding: 0 0;
    }
    .pb-input {
        height: 3;
        background: $background;
        border: tall $accent;
        color: $text;
        padding: 0 1;
    }
    .pb-input:focus { border: tall $accent; }
    .pb-slash-hint {
        height: 1;
        padding: 0 1;
        color: $text-muted;
        background: $surface;
        display: none;
    }
    .pb-perm-indicator {
        height: 1;
        padding: 0 1;
        color: $text-disabled;
        background: $surface;
    }
    """

    # [1-5] auto suggestions from the personalization engine
    suggestions: reactive[list[str]] = reactive(list, always_update=True)
    # [6-0] manually configured shortcuts (5 slots, indices 0-4)
    manual_shortcuts: reactive[list[str]] = reactive(
        lambda: [""] * 5, always_update=True
    )

    # Maps ctrl+digit key name → manual slot index (0–4)
    _CTRL_MANUAL: ClassVar[dict[str, int]] = {
        "ctrl+6": 0, "ctrl+7": 1, "ctrl+8": 2, "ctrl+9": 3, "ctrl+0": 4,
    }
    # Display key labels for manual slots
    _MANUAL_KEYS: ClassVar[list[str]] = ["6", "7", "8", "9", "0"]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._sugg_idx = -1   # for Tab cycling

    def compose(self) -> ComposeResult:
        yield Static("", id="pb-auto-sugg",   classes="pb-auto-sugg")
        yield Static("", id="pb-manual-sugg", classes="pb-manual-sugg")
        inp = Input(
            placeholder=(
                "› n or Enter to focus · type prompt · Tab=cycle · "
                "ctrl+6…0=save shortcut · Escape=back"
            ),
            id="pb-input",
            classes="pb-input",
        )
        inp.can_focus = False
        yield inp
        yield Static("", id="pb-slash-hint",     classes="pb-slash-hint")
        yield Static("", id="pb-perm-indicator", classes="pb-perm-indicator")

    # ── reactive watchers ────────────────────────────────────────────────────

    def watch_suggestions(self, suggestions: list[str]) -> None:
        row = self.query_one("#pb-auto-sugg", Static)
        parts = [
            f"[bold cyan][{i}][/bold cyan] {s[:22]}{'…' if len(s) > 22 else ''}"
            for i, s in enumerate(suggestions[:5], 1)
        ]
        row.update("  ".join(parts) if parts else " [dim](no suggestions yet)[/dim]")

    def watch_manual_shortcuts(self, manual: list[str]) -> None:
        row = self.query_one("#pb-manual-sugg", Static)
        parts = []
        for i, (key, text) in enumerate(zip(self._MANUAL_KEYS, manual)):
            if text:
                label = f"{text[:22]}{'…' if len(text) > 22 else ''}"
                parts.append(f"[bold yellow]\[{key}][/bold yellow] {label}")
            else:
                parts.append(f"[dim]\[{key}] —[/dim]")
        hint = "  [dim italic]ctrl+6…0 to assign[/dim italic]"
        row.update("  ".join(parts) + hint)

    # ── public API ───────────────────────────────────────────────────────────

    def focus_input(self) -> None:
        inp = self.query_one("#pb-input", Input)
        inp.can_focus = True   # re-enable so .focus() succeeds; restored on blur
        inp.focus()
        self._sugg_idx = -1

    def on_click(self) -> None:
        """Clicking anywhere on the prompt bar activates the input."""
        self.focus_input()

    @on(Input.Blurred, "#pb-input")
    def _input_blurred(self, _event) -> None:
        """Exclude from tab cycle as soon as focus leaves the input."""
        self.query_one("#pb-input", Input).can_focus = False
        hint = self.query_one("#pb-slash-hint", Static)
        hint.display = False

    @on(Input.Changed, "#pb-input")
    def _pb_input_changed(self, event: Input.Changed) -> None:
        """Show passive slash-command hint while user types / commands."""
        hint = self.query_one("#pb-slash-hint", Static)
        value = event.value
        if value.startswith("/"):
            text = _slash_hint_text(value.split()[0] if value.split() else value)
            if text:
                hint.update(text)
                hint.display = True
                return
        hint.display = False

    def fill_suggestion(self, idx: int) -> None:
        """Fill auto suggestion[idx] (0-based) into the input and focus it."""
        if idx < len(self.suggestions):
            inp = self.query_one("#pb-input", Input)
            inp.can_focus = True
            inp.value = self.suggestions[idx]
            inp.focus()
            inp.action_end()
            self._sugg_idx = idx

    def fill_manual(self, slot: int) -> None:
        """Fill manual shortcut slot (0-based, maps to keys 6-0) into the input."""
        text = (self.manual_shortcuts or [""] * 5)[slot] if slot < 5 else ""
        if text:
            inp = self.query_one("#pb-input", Input)
            inp.can_focus = True
            inp.value = text
            inp.focus()
            inp.action_end()
            self._sugg_idx = -1

    def current_input_text(self) -> str:
        return self.query_one("#pb-input", Input).value

    def update_perm_indicator(self, mode: str, project: str = "") -> None:
        """Update the inline permission indicator below the prompt input."""
        self.query_one("#pb-perm-indicator", Static).update(
            _perm_indicator_text(mode, project)
        )

    # ── key handling ─────────────────────────────────────────────────────────

    def on_input_key(self, event: Key) -> None:
        """Intercept Tab and Escape inside the Input widget."""
        if event.key == "tab":
            self._cycle_suggestion()
            event.prevent_default()
            event.stop()
        elif event.key == "escape":
            pass   # let it bubble to App

    def _cycle_suggestion(self) -> None:
        if not self.suggestions:
            return
        self._sugg_idx = (self._sugg_idx + 1) % len(self.suggestions)
        inp = self.query_one("#pb-input", Input)
        inp.value = self.suggestions[self._sugg_idx]
        inp.action_end()

    @on(Input.Submitted, "#pb-input")
    def _submitted(self, event: Input.Submitted) -> None:
        prompt = event.value.strip()
        if prompt:
            self.post_message(PromptSubmitted(prompt))
            event.input.value = ""
            self._sugg_idx = -1
            self.query_one("#pb-slash-hint", Static).display = False


# DirectoryPickerScreen imported from ui.screens


# ---------------------------------------------------------------------------
# StatusBar — permission mode indicator + current project
# ---------------------------------------------------------------------------

# _PERM_LABELS, _PERM_CYCLE, _PERM_INDICATOR_NAMES, _perm_indicator_text,
# _AGENT_LABELS, _AGENT_CYCLE, _EFFORT_LABELS, _EFFORT_CYCLE
# all imported from ui.constants


class OpenClawInboxPanel(Static):
    """
    Sidebar panel that displays messages received from OpenClaw channels and
    paired devices.  Hidden unless the user presses `c` (channels toggle).
    """

    DEFAULT_CSS = """
    OpenClawInboxPanel {
        width: 36;
        height: 1fr;
        background: $surface-darken-1;
        border-left: solid $primary-darken-2;
        overflow-y: auto;
    }
    .oc-header {
        background: $primary-darken-3;
        color: $accent;
        padding: 0 1;
        height: 1;
    }
    .oc-message {
        padding: 0 1;
        margin-bottom: 1;
        border-bottom: dashed $primary-darken-2;
    }
    .oc-channel {
        color: $accent;
        text-style: bold;
    }
    .oc-peer {
        color: $text-muted;
    }
    .oc-body {
        color: $text;
        margin-top: 0;
    }
    .oc-device {
        color: $warning;
        padding: 0 1;
        margin-bottom: 0;
    }
    .oc-status {
        color: $text-muted;
        padding: 0 1;
        text-style: italic;
    }
    .oc-empty {
        color: $text-muted;
        padding: 1 1;
        text-style: italic;
    }
    """

    def compose(self) -> ComposeResult:
        yield Label(" OpenClaw Inbox", classes="oc-header")
        yield Label("Waiting for gateway…", classes="oc-empty", id="oc-empty")
        yield ScrollableContainer(id="oc-scroll")

    # ── public API called via call_from_thread ──────────────────────────────

    def add_message(self, msg: ChannelMessage) -> None:
        """Append a ChannelMessage entry to the inbox."""
        self._remove_empty()
        direction = "↓" if msg.direction == "inbound" else "↑"
        channel   = msg.display_channel or "main"
        peer      = msg.display_peer
        text      = msg.text[:500]   # truncate very long messages

        entry = Static(
            f"[bold $accent]{direction} {channel}[/bold $accent]"
            f"[dim]  {peer}[/dim]\n{text}",
            classes="oc-message",
        )
        try:
            self.query_one("#oc-scroll", ScrollableContainer).mount(entry)
            self.query_one("#oc-scroll", ScrollableContainer).scroll_end(animate=False)
        except Exception:
            pass

    def add_device_event(self, dev: DeviceEvent) -> None:
        """Append a device/node event notification."""
        self._remove_empty()
        label = Static(
            f"[yellow]⟁ {dev.node_name}[/yellow]  {dev.event_type}",
            classes="oc-device",
        )
        try:
            self.query_one("#oc-scroll", ScrollableContainer).mount(label)
        except Exception:
            pass

    def set_status(self, text: str) -> None:
        """Update the status line (gateway connection state)."""
        try:
            existing = self.query(".oc-status")
            for w in existing:
                w.remove()
            label = Static(text, classes="oc-status")
            self.query_one("#oc-scroll", ScrollableContainer).mount(label)
        except Exception:
            pass

    def clear(self) -> None:
        """Clear all messages."""
        try:
            sc = self.query_one("#oc-scroll", ScrollableContainer)
            for child in list(sc.children):
                child.remove()
        except Exception:
            pass
        try:
            self.query_one("#oc-empty", Label).display = True
        except Exception:
            pass

    def _remove_empty(self) -> None:
        try:
            self.query_one("#oc-empty", Label).display = False
        except Exception:
            pass


# ---------------------------------------------------------------------------
# ObsidianPanel — external Obsidian vault file tree + todo extractor
# ---------------------------------------------------------------------------

class _ObsidianTree(Tree):
    """Tree subclass that lets Space mark/unmark file nodes instead of expanding."""

    class ToggleMark(Message):
        def __init__(self, path: str) -> None:
            self.path = path
            super().__init__()

    def on_key(self, event: Key) -> None:
        if event.key == "space":
            node = self.cursor_node
            if node and node.data:
                # It's a file leaf — toggle mark instead of expanding
                self.post_message(self.ToggleMark(str(node.data)))
                event.prevent_default()
                event.stop()
                return
        # Directory nodes or any other key: default Tree behaviour


class ObsidianPanel(Static):
    """
    External Obsidian vault file tree + todo extractor. Toggle with O (shift+o).

    File view:
      ↑↓   navigate tree
      Space mark / unmark current note for this project
      a     auto-detect and mark relevant notes
      T     switch to todos view
      O     close panel

    Todos view:
      ↑↓   navigate
      T     back to file view
    """

    DEFAULT_CSS = """
    ObsidianPanel {
        width: 1fr;
        height: 1fr;
        background: $background;
    }
    .op-header {
        height: 1;
        background: $primary-darken-3;
        color: $text-muted;
        padding: 0 1;
    }
    .op-tree  { height: 1fr; }
    .op-footer {
        height: 1;
        background: $surface;
        color: $text-muted;
        padding: 0 1;
    }
    """

    class NoteOpened(Message):
        """Posted when the user selects a note (Enter/click) — open in editor."""
        def __init__(self, path: str) -> None:
            self.path = path
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._obsidian_vault:  ObsidianVault | None  = None
        self._linker:          ObsidianLinker | None = None
        self._project_name:    str = ""
        self._project_path:    str = ""
        self._view:            str = "files"   # "files" | "todos"

    def compose(self) -> ComposeResult:
        yield Label(
            " OBSIDIAN  [dim](Space=mark/unmark · a=auto-detect · T=todos · O=close)[/dim]",
            id="op-header", classes="op-header",
        )
        yield _ObsidianTree("Vault", id="op-tree", classes="op-tree")
        yield Label("", id="op-footer", classes="op-footer")

    def on_mount(self) -> None:
        self._populate()

    # ── public API ────────────────────────────────────────────────────────

    def refresh_for_project(
        self,
        project_name: str,
        project_path: str,
        obsidian_vault: ObsidianVault | None,
        linker: ObsidianLinker | None,
    ) -> None:
        self._project_name   = project_name
        self._project_path   = project_path
        self._obsidian_vault = obsidian_vault
        self._linker         = linker
        self._populate()

    # ── internal ─────────────────────────────────────────────────────────

    def _populate(self) -> None:
        tree   = self.query_one("#op-tree",   _ObsidianTree)
        footer = self.query_one("#op-footer", Label)
        tree.clear()

        if not self._obsidian_vault or not self._obsidian_vault.exists():
            leaf = tree.root.add_leaf("(no Obsidian vault connected)")
            leaf.data = None
            tree.root.add_leaf("  Use /obsidian <path> to connect one")
            tree.root.expand()
            footer.update(" /obsidian <path> to enable  ·  O to close")
            return

        if self._view == "files":
            self._populate_files(tree)
            n = len(self._linker.get_project_notes(self._project_name)) if self._linker else 0
            proj = self._project_name or "project"
            footer.update(f" {n} marked for {proj}  ·  T=todos  ·  a=auto-detect")
        else:
            self._populate_todos(tree)
            footer.update(" T=back to files  ·  O to close")

    def _populate_files(self, tree: _ObsidianTree) -> None:
        vault_root = self._obsidian_vault.root  # type: ignore[union-attr]
        marked     = set(self._linker.get_project_notes(self._project_name)) if self._linker else set()
        tree.root.label = os.path.basename(vault_root) or "Obsidian"
        self._build_dir_node(tree.root, vault_root, marked)
        tree.root.expand()

    def _build_dir_node(self, parent_node, dir_path: str, marked: set) -> None:
        try:
            entries = sorted(
                os.scandir(dir_path),
                key=lambda e: (not e.is_dir(), e.name.lower()),
            )
        except PermissionError:
            return
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                node = parent_node.add(f"  {entry.name}")
                node.data = None
                self._build_dir_node(node, entry.path, marked)
            elif entry.name.endswith(".md"):
                title = entry.name[:-3]
                label = f"● {title}" if entry.path in marked else f"  {title}"
                leaf  = parent_node.add_leaf(label)
                leaf.data = entry.path

    def _populate_todos(self, tree: _ObsidianTree) -> None:
        tree.root.label = "Todos"
        if not self._linker or not self._obsidian_vault:
            tree.root.add_leaf("(no marked notes)")
            tree.root.expand()
            return

        marked_paths = self._linker.get_project_notes(self._project_name)
        if not marked_paths:
            tree.root.add_leaf("(no marked notes — mark files in file view first)")
            tree.root.expand()
            return

        has_todos = False
        for path in marked_paths:
            try:
                from memory.obsidian import ObsidianNote
                note  = ObsidianNote.from_file(path)
                todos = note.todos()
                if todos:
                    note_node = tree.root.add(f"  {note.title}")
                    note_node.data = path
                    for todo in todos:
                        leaf      = note_node.add_leaf(f"  ☐ {todo[:100]}")
                        leaf.data = path
                    note_node.expand()
                    has_todos = True
            except Exception:
                pass

        if not has_todos:
            tree.root.add_leaf("(no unchecked todos in marked notes)")
        tree.root.expand()

    # ── key handling ─────────────────────────────────────────────────────

    def on_key(self, event: Key) -> None:
        key  = event.key
        char = event.character or ""
        if char == "T":
            self._view = "todos" if self._view == "files" else "files"
            self._populate()
            event.stop()
        elif char == "a":
            self._auto_detect()
            event.stop()

    @on(_ObsidianTree.ToggleMark)
    def _toggle_mark(self, event: _ObsidianTree.ToggleMark) -> None:
        if not self._linker or self._view != "files":
            return
        path = event.path
        if self._linker.is_marked(self._project_name, path):
            self._linker.unmark(self._project_name, path)
        else:
            self._linker.mark(self._project_name, path)
        self._populate()

    @on(Tree.NodeSelected, "#op-tree")
    def _node_selected(self, event: Tree.NodeSelected) -> None:
        if event.node.data:
            self.post_message(ObsidianPanel.NoteOpened(str(event.node.data)))

    def _auto_detect(self) -> None:
        if not self._obsidian_vault or not self._linker or not self._project_name:
            self.app.notify("No vault or project configured.", severity="warning", timeout=3)
            return
        notes   = self._obsidian_vault.all_notes()
        marked  = 0
        for note in notes:
            score = self._obsidian_vault.score_relevance(
                note, self._project_name, self._project_path
            )
            if score >= 0.5:
                self._linker.mark(self._project_name, note.path)
                marked += 1
        self._populate()
        self.app.notify(
            f"Auto-detected {marked} relevant note(s) for {self._project_name}",
            timeout=4,
        )


class StatusBar(Static):
    """Thin bar showing agent type, permission mode, active project, and effort."""

    DEFAULT_CSS = """
    StatusBar {
        height: 1;
        background: $surface;
        layout: horizontal;
        border-bottom: solid $primary-darken-2;
    }
    .sb-project { color: $text-muted; padding: 0 2; width: 1fr; }
    .sb-agent   { padding: 0 2; }
    .sb-perm    { padding: 0 2; }
    .sb-limits  { padding: 0 2; color: $text-muted; }
    .sb-effort  { padding: 0 2; text-align: right; }
    """

    def compose(self) -> ComposeResult:
        yield Label("", id="sb-project", classes="sb-project")
        yield Label("", id="sb-agent",   classes="sb-agent")
        yield Label("", id="sb-perm",    classes="sb-perm")
        yield Label("", id="sb-limits",  classes="sb-limits")
        yield Label("", id="sb-effort",  classes="sb-effort")

    def update_project(self, name: str) -> None:
        self.query_one("#sb-project", Label).update(f" ⬡ {name}")

    def update_agent(self, agent_type: str) -> None:
        label, color = _AGENT_LABELS.get(agent_type, ("?", "$text"))
        self.query_one("#sb-agent", Label).update(f"[{color}]● {label}[/{color}]")

    def update_perm(self, mode: str) -> None:
        label, color = _PERM_LABELS.get(mode, ("?", "$text"))
        self.query_one("#sb-perm", Label).update(f"[{color}]{label}[/{color}]")

    def update_limits(
        self,
        max_turns: int | None,
        max_budget_usd: float | None,
        system_prompt: str,
        allowed_tools: list[str],
        disallowed_tools: list[str],
        model_override: str,
    ) -> None:
        parts: list[str] = []
        if max_budget_usd is not None:
            parts.append(f"${max_budget_usd:.2f}")
        if max_turns is not None:
            parts.append(f"{max_turns}t")
        if model_override:
            short = model_override.split("/")[-1][:12]
            parts.append(short)
        if system_prompt:
            parts.append("sys")
        if allowed_tools:
            parts.append(f"+{len(allowed_tools)}tool")
        if disallowed_tools:
            parts.append(f"-{len(disallowed_tools)}tool")
        text = "  ".join(parts)
        self.query_one("#sb-limits", Label).update(f"[dim]{text}[/dim]" if text else "")

    def update_effort(self, mode: str) -> None:
        label, color = _EFFORT_LABELS.get(mode, ("?", "$text"))
        # Hide "MEDIUM" to keep the bar uncluttered at the default level
        if mode == "medium":
            self.query_one("#sb-effort", Label).update("")
        else:
            self.query_one("#sb-effort", Label).update(f"[{color}]{label}[/{color}]")

    def update_openclaw_status(self, reachable: bool, channels: list[str]) -> None:
        """Append gateway + channel info when OpenClaw is the active agent."""
        if reachable:
            ch = "  ".join(channels[:4]) if channels else "no channels"
            extra = f"  [dim]gateway ✓  {ch}[/dim]"
        else:
            extra = "  [red]gateway ✗ — run: openclaw gateway[/red]"
        existing = self.query_one("#sb-agent", Label).content or ""
        # Replace previous openclaw status suffix
        base = str(existing).split("  [")[0] if "  [" in str(existing) else str(existing)
        self.query_one("#sb-agent", Label).update(base + extra)

    def clear_openclaw_status(self) -> None:
        agent_lbl = self.query_one("#sb-agent", Label)
        text = str(agent_lbl.content or "")
        agent_lbl.update(text.split("  [")[0] if "  [" in text else text)


class ShortcutsBar(Static):
    """Bottom bar showing key bindings at a glance."""

    DEFAULT_CSS = """
    ShortcutsBar {
        height: 1;
        background: $primary-darken-3;
        layout: horizontal;
        padding: 0 1;
    }
    .sc-key  { color: $accent; }
    .sc-sep  { color: $text-muted; padding: 0 1; }
    .sc-rest { color: $text-muted; width: 1fr; }
    """

    _SHORTCUTS = [
        ("ctrl+p", "palette"),
        ("n/↵",    "new agent"),
        ("⇧A",     "cycle agent"),
        ("⇧P",     "permissions"),
        ("⇧E",     "effort"),
        ("] [",    "projects"),
        ("d",      "detach"),
        ("r",      "reattach"),
        ("⇧R",     "run cmd"),
        ("f",      "files"),
        ("e",      "editor"),
        ("m",      "graph"),
        ("t",      "terminal"),
        ("c",      "channels"),
        ("⇧O",     "obsidian"),
        ("q",      "quit"),
    ]

    def compose(self) -> ComposeResult:
        parts = []
        for key, desc in self._SHORTCUTS:
            # Escape [ and ] so Rich doesn't treat them as markup tags
            safe_key = key.replace("[", r"\[").replace("]", r"\]")
            parts.append(f"{safe_key} {desc}")
        yield Label("  ·  ".join(parts), classes="sc-rest")


# BrainImportScreen, DetachMenuScreen, _ObsidianPathScreen, CommandPaletteScreen
# imported from ui.screens


# ---------------------------------------------------------------------------
# Main App
# ---------------------------------------------------------------------------

class VibeCLIApp(App[None]):

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

    def __init__(self, config: dict) -> None:
        super().__init__()
        self._config = config

        self._pm = ProjectManager()

        vault_root   = config.get("vault", {}).get("root", "vault")
        self._vault  = MemoryVault(vault_root)

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

        # Start the PreToolUse HTTP hook server (used in safe permission mode)
        await self._approval_server.start()

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
        saved = SessionStore().load()
        if saved:
            self._restore_session(saved)
        else:
            self._apply_layout()

        self._refresh_suggestions()
        self.query_one("#prompt-bar", PromptBar).manual_shortcuts = list(self._manual_shortcuts)

        # Apply persisted UI theme after first render so all widgets exist
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
        """Apply the UI theme stored in _ui_theme after the first render pass.

        Called via call_after_refresh from on_mount so all widgets are ready
        and custom themes have been registered.
        """
        name = self._ui_theme
        if not name or name == "textual-dark":
            return
        try:
            self.theme = name
        except Exception:
            # Theme not available — clear the stored value so we don't keep retrying
            self._ui_theme = "textual-dark"

    def _set_theme(self, name: str) -> None:
        """Switch the app theme live and persist to both config.json and session."""
        try:
            self.theme = name
        except Exception:
            self.notify(f"Unknown theme: {name}", severity="warning", timeout=3)
            return
        self._ui_theme = name
        self._persist_theme_config(name)
        SessionStore().patch_global(ui_theme=name)
        self.notify(f"Theme: {name}", timeout=2)

    def _persist_theme_config(self, name: str) -> None:
        import json as _json
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
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
        config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
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
            SessionStore().save(state)
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

