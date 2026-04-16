"""ui/widgets.py — All widget and helper classes for VibeCLI TUI."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Container, Vertical
from textual.events import Key
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import (
    Button, DirectoryTree, Input, Label, RichLog, Static, TextArea, Tree,
)
from textual import on, work

from core.project_manager import Project
from terminal.agent_session import AgentSession
from terminal.pty_widget import PTYWidget
from memory.vault import MemoryVault
from memory.obsidian import ObsidianVault, ObsidianLinker
from core.openclaw_gateway import ChannelMessage, DeviceEvent

from ui.themes import APP_TO_PYGMENTS_THEME as _APP_TO_PYGMENTS_THEME
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
# ExpandingInput — auto-growing TextArea with Input-compatible API
# ---------------------------------------------------------------------------

class ExpandingInput(TextArea):
    """Multi-line text input that grows vertically as text wraps.

    Enter submits; Shift+Enter inserts a newline.  Provides .value, .placeholder,
    and .action_end() so existing call-sites need minimal changes.
    """

    BINDINGS = [
        Binding("enter",       "submit_text",  show=False, priority=True),
        Binding("shift+enter", "newline",      show=False, priority=True),
        Binding("tab",         "tab_press",    show=False, priority=True),
        Binding("shift+up",    "history_up",   show=False, priority=True),
        Binding("shift+down",  "history_down", show=False, priority=True),
    ]

    DEFAULT_CSS = """
    ExpandingInput {
        height: auto;
        min-height: 3;
        max-height: 12;
        background: $background;
        color: $text;
        padding: 0 1;
        scrollbar-size: 0 0;
        border: tall $accent;
    }
    ExpandingInput:focus { border: tall $accent; }
    ExpandingInput .text-area--gutter { display: none; width: 0; }
    ExpandingInput .text-area--cursor-line { background: transparent; }
    ExpandingInput .text-area--selection { background: $accent 35%; }
    """

    class Submitted(Message):
        def __init__(self, widget: "ExpandingInput", value: str) -> None:
            self.input = widget   # named 'input' for compatibility with existing handlers
            self.value = value
            super().__init__()

        @property
        def control(self) -> "ExpandingInput":
            return self.input

    class Blurred(Message):
        def __init__(self, widget: "ExpandingInput") -> None:
            self.input = widget
            super().__init__()

        @property
        def control(self) -> "ExpandingInput":
            return self.input

    class TabPressed(Message):
        def __init__(self, widget: "ExpandingInput") -> None:
            self.input = widget
            super().__init__()

        @property
        def control(self) -> "ExpandingInput":
            return self.input

    class HistoryBrowse(Message):
        def __init__(self, widget: "ExpandingInput", direction: str) -> None:
            self.input = widget
            self.direction = direction  # "up" or "down"
            super().__init__()

        @property
        def control(self) -> "ExpandingInput":
            return self.input

    def __init__(
        self,
        placeholder: str = "",
        *,
        id: str | None = None,
        classes: str | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            text="",
            language=None,
            theme="css",
            show_line_numbers=False,
            soft_wrap=True,
            id=id,
            classes=classes,
        )
        self._placeholder = placeholder

    # ── Input-compatible API ────────────────────────────────────────────────

    @property
    def value(self) -> str:
        return self.text.rstrip("\n")

    @value.setter
    def value(self, v: str) -> None:
        self.load_text(v)

    @property
    def placeholder(self) -> str:
        return self._placeholder

    @placeholder.setter
    def placeholder(self, v: str) -> None:
        self._placeholder = v

    def action_end(self) -> None:
        """Move cursor to end of last line (mirrors Input.action_end)."""
        lines = self.text.split("\n")
        last_row = max(0, len(lines) - 1)
        last_col = len(lines[last_row])
        self.move_cursor((last_row, last_col))

    # ── actions ─────────────────────────────────────────────────────────────

    def action_submit_text(self) -> None:
        val = self.text.rstrip("\n").strip()
        if val:
            self.post_message(self.Submitted(self, val))

    def action_newline(self) -> None:
        self.insert("\n")

    def action_tab_press(self) -> None:
        self.post_message(self.TabPressed(self))

    def action_history_up(self) -> None:
        self.post_message(self.HistoryBrowse(self, "up"))

    def action_history_down(self) -> None:
        self.post_message(self.HistoryBrowse(self, "down"))

    def _on_blur(self, event) -> None:
        super()._on_blur(event)
        self.post_message(self.Blurred(self))


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
        height: auto;
        min-height: 3;
        max-height: 8;
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
        yield ExpandingInput(
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
            self.query_one(f"#agent-reply-{sid}", ExpandingInput).display = False
        except Exception:
            pass
        self._ticker()
        exit_code = await self._stream(self.session)
        self._mark_complete(exit_code)

    async def _stream(self, session) -> int:
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
        reply_input = self.query_one(f"#agent-reply-{sid}", ExpandingInput)
        reply_input.display = True

        self.post_message(self.Complete(self, exit_code))

    # ------------------------------------------------------------------ slash hint

    @on(TextArea.Changed)
    def _reply_changed(self, event: TextArea.Changed) -> None:
        """Show passive slash-command hint while user types / commands."""
        if not (event.text_area.id or "").startswith("agent-reply-"):
            return
        if self._slash_hint is None:
            return
        value = event.text_area.text.rstrip("\n")
        if value.startswith("/"):
            hint = _slash_hint_text(value.split()[0] if value.split() else value)
            if hint:
                self._slash_hint.update(hint)
                self._slash_hint.display = True
                return
        self._slash_hint.display = False

    # ------------------------------------------------------------------ reply / multi-turn

    @on(ExpandingInput.Submitted)
    def _reply_submitted(self, event: ExpandingInput.Submitted) -> None:
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
        reply_input = self.query_one(f"#agent-reply-{sid}", ExpandingInput)
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

    def pop_project_state(self, project_name: str) -> list[dict]:
        """Snapshot all agent states for project_name, remove its DOM container, return states."""
        agents = self.get_agents_for_project(project_name)
        states = [w.to_state() for w in agents]
        cid = self._container_id(project_name)
        try:
            self.query_one(f"#{cid}", ScrollableContainer).remove()
        except Exception:
            pass
        return states

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
                f" {os.path.basename(path)}  [dim]i=edit · e=close[/dim]"
            )
            try:
                content = Path(path).read_text(errors="replace")
            except Exception as e:
                content = f"[Error: {e}]"
            # Pre-load into TextArea so edit mode is ready when `i` is pressed
            ta.load_text(content)
            _set_ta_language(ta, _language_for(path))
            ta.read_only = False
            ta.display = False
            # Default to colorized Rich Syntax read-only view
            self._render_to_view(content)
            sc.display = True
            self._in_view_mode = True

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

    _LIGHT_THEMES = frozenset({
        "textual-light", "atom-one-light", "catppuccin-latte", "rose-pine-dawn",
        "solarized-light", "ayu-light", "paper", "everforest-light", "warm-light",
    })

    def _ta_theme(self) -> str:
        """Return a Textual TextArea theme name matching the current app theme.

        Available built-ins: monokai, dracula, vscode_dark, github_light, css.
        """
        app_theme = getattr(self.app, "theme", "textual-dark")
        if app_theme == "dracula":
            return "dracula"
        if app_theme in self._LIGHT_THEMES:
            return "github_light"
        return "vscode_dark"

    def switch_to_view_mode(self) -> None:
        """Save content and display the colorized Rich Syntax read-only view."""
        ta  = self.query_one("#ep-area",   TextArea)
        sc  = self.query_one("#ep-scroll", ScrollableContainer)
        # Capture TextArea scroll position (cursor row ≈ first visible line)
        saved_scroll_y = ta.scroll_y
        self._render_to_view(ta.text)
        ta.display = False
        sc.display = True
        self._in_view_mode = True
        if self._current_path:
            label = self.query_one("#ep-label", Label)
            label.update(
                f" {os.path.basename(self._current_path)}  "
                "[dim]i=edit · e=close[/dim]"
            )
        # Restore scroll so the same lines stay in view
        def _restore_scroll() -> None:
            sc.scroll_to(y=saved_scroll_y, animate=False)
        self.call_after_refresh(_restore_scroll)

    def enter_edit_mode(self) -> None:
        """Switch from the colorized view back to the editable TextArea."""
        ta  = self.query_one("#ep-area",   TextArea)
        sc  = self.query_one("#ep-scroll", ScrollableContainer)
        # Capture the read-only view's scroll offset before hiding it
        saved_scroll_y = sc.scroll_y
        sc.display = False
        try:
            ta.theme = self._ta_theme()
        except Exception:
            pass
        ta.display = True
        self._in_view_mode = False
        if self._current_path:
            label = self.query_one("#ep-label", Label)
            label.update(
                f" {os.path.basename(self._current_path)}  "
                "[dim]s=save · Esc=view · e=close[/dim]"
            )
        # Restore scroll position after the TextArea has been laid out
        def _restore_scroll() -> None:
            ta.scroll_to(y=saved_scroll_y, animate=False)
        self.call_after_refresh(_restore_scroll)
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

    def refresh_tree(self) -> None:
        """Public alias for _populate — called from background threads."""
        self._populate()

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
                if btn.id == "ptab-add":
                    self.mount(btn)          # ⊕ always goes last
                else:
                    # Mount project tab before the ⊕ button so it stays rightmost
                    try:
                        self.mount(btn, before=self.query_one("#ptab-add", Button))
                    except Exception:
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
        background: $background;
        color: $text;
    }
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
        inp = ExpandingInput(
            placeholder=(
                "› n or Enter to focus · type prompt · Shift+Enter=newline · "
                "Tab=cycle · ctrl+6…0=save shortcut · Escape=back"
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
        inp = self.query_one("#pb-input", ExpandingInput)
        inp.can_focus = True   # re-enable so .focus() succeeds; restored on blur
        inp.focus()
        self._sugg_idx = -1

    def on_click(self) -> None:
        """Clicking anywhere on the prompt bar activates the input."""
        self.focus_input()

    @on(ExpandingInput.Blurred, "#pb-input")
    def _input_blurred(self, _event) -> None:
        """Exclude from tab cycle as soon as focus leaves the input."""
        # If the OS took focus away from the terminal (alt-tab etc.), don't
        # disable can_focus — Textual will restore focus here via AppFocus with
        # from_app_focus=True, which skips select-all.  Setting can_focus=False
        # would prevent that restoration and strand the user in command mode.
        if not self.app.app_focus:
            return
        self.query_one("#pb-input", ExpandingInput).can_focus = False
        hint = self.query_one("#pb-slash-hint", Static)
        hint.display = False

    @on(TextArea.Changed, "#pb-input")
    def _pb_input_changed(self, event: TextArea.Changed) -> None:
        """Show passive slash-command hint while user types / commands."""
        hint = self.query_one("#pb-slash-hint", Static)
        value = event.text_area.text.rstrip("\n")
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
            inp = self.query_one("#pb-input", ExpandingInput)
            inp.can_focus = True
            inp.value = self.suggestions[idx]
            inp.focus()
            inp.action_end()
            self._sugg_idx = idx

    def fill_manual(self, slot: int) -> None:
        """Fill manual shortcut slot (0-based, maps to keys 6-0) into the input."""
        text = (self.manual_shortcuts or [""] * 5)[slot] if slot < 5 else ""
        if text:
            inp = self.query_one("#pb-input", ExpandingInput)
            inp.can_focus = True
            inp.value = text
            inp.focus()
            inp.action_end()
            self._sugg_idx = -1

    def current_input_text(self) -> str:
        return self.query_one("#pb-input", ExpandingInput).value

    def update_perm_indicator(self, mode: str, project: str = "") -> None:
        """Update the inline permission indicator below the prompt input."""
        self.query_one("#pb-perm-indicator", Static).update(
            _perm_indicator_text(mode, project)
        )

    # ── key handling ─────────────────────────────────────────────────────────

    @on(ExpandingInput.TabPressed, "#pb-input")
    def _tab_pressed(self, _event) -> None:
        self._cycle_suggestion()

    def _cycle_suggestion(self) -> None:
        if not self.suggestions:
            return
        self._sugg_idx = (self._sugg_idx + 1) % len(self.suggestions)
        inp = self.query_one("#pb-input", ExpandingInput)
        inp.value = self.suggestions[self._sugg_idx]
        inp.action_end()

    @on(ExpandingInput.Submitted, "#pb-input")
    def _submitted(self, event: ExpandingInput.Submitted) -> None:
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
        ("⇧W",     "close tab"),
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
