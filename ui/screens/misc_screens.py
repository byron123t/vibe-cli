"""
ui/screens/misc_screens.py — BrainImportScreen, DetachMenuScreen, _ObsidianPathScreen.
"""
from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, Label
from textual import on


# ---------------------------------------------------------------------------
# BrainImportScreen — modal for importing a brain/memory folder into the vault
# ---------------------------------------------------------------------------

class BrainImportScreen(ModalScreen):
    """Prompt for a folder path and import its .md files into the vault."""

    DEFAULT_CSS = """
    BrainImportScreen {
        align: center middle;
    }
    #brain-import-container {
        width: 72;
        height: auto;
        background: $surface;
        border: solid $accent;
        padding: 1 2;
    }
    #brain-import-title {
        text-align: center;
        color: $accent;
        margin-bottom: 1;
    }
    #brain-import-label {
        color: $text-muted;
        margin-bottom: 0;
    }
    #brain-import-input {
        width: 1fr;
        margin-bottom: 1;
    }
    #brain-import-hint {
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="brain-import-container"):
            yield Label("─── Import Brain / Memory Folder ───", id="brain-import-title")
            yield Label("Folder path (or single .md file):", id="brain-import-label")
            yield Input(placeholder="/path/to/your/brain", id="brain-import-input")
            yield Label("↵=import  Esc=cancel", id="brain-import-hint")

    def on_mount(self) -> None:
        self.query_one("#brain-import-input", Input).focus()

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        path = event.value.strip()
        if path:
            self.dismiss(path)
        else:
            self.dismiss(None)


# ---------------------------------------------------------------------------
# DetachMenuScreen — modal listing detached agents for reattachment
# ---------------------------------------------------------------------------

class DetachMenuScreen(ModalScreen):
    """Modal listing detached agents — Enter to reattach, Delete to kill.

    Actions execute in-place: the list updates immediately and the screen
    stays open so the user can perform multiple operations before closing.
    """

    DEFAULT_CSS = """
    DetachMenuScreen {
        align: center middle;
    }
    #detach-menu-container {
        width: 72;
        max-height: 24;
        background: $surface;
        border: solid $accent;
        padding: 1 2;
    }
    .dm-title {
        text-align: center;
        color: $accent;
        margin-bottom: 1;
    }
    .dm-item {
        padding: 0 1;
        color: $text;
        width: 1fr;
    }
    .dm-item-focused {
        background: $accent-darken-2;
        color: $text;
    }
    .dm-empty {
        color: $text-muted;
        text-align: center;
        padding: 1;
    }
    .dm-hint {
        color: $text-muted;
        text-align: center;
        margin-top: 1;
    }
    """

    def __init__(
        self,
        detached: list[dict],
        on_reattach: "Callable[[dict], None]",
        on_kill: "Callable[[dict], None]",
    ) -> None:
        super().__init__()
        self._detached = detached
        self._on_reattach = on_reattach
        self._on_kill = on_kill
        self._cursor: int = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="detach-menu-container"):
            yield Label("─── Detached Agents ───", classes="dm-title")
            if not self._detached:
                yield Label("No detached agents for this project.", classes="dm-empty")
            else:
                for i, state in enumerate(self._detached):
                    yield Label(self._item_text(i, state),
                                id=f"dm-item-{i}",
                                classes="dm-item dm-item-focused" if i == 0 else "dm-item")
            yield Label("↑↓/j/k=navigate  ↵=reattach  Del=kill  Esc=close", classes="dm-hint")

    @staticmethod
    def _item_text(i: int, state: dict) -> str:
        prompt     = state.get("prompt", "")[:52]
        agent_type = state.get("agent_type", "claude")
        return f"[{i+1}]  [{agent_type}]  {prompt}"

    def _rebuild_items(self) -> None:
        for lbl in self.query(".dm-item, .dm-empty"):
            lbl.remove()
        container = self.query_one("#detach-menu-container")
        hint      = self.query_one(".dm-hint")
        if not self._detached:
            container.mount(
                Label("No detached agents for this project.", classes="dm-empty"),
                before=hint,
            )
            self._cursor = 0
        else:
            self._cursor = min(self._cursor, len(self._detached) - 1)
            for i, state in enumerate(self._detached):
                classes = "dm-item dm-item-focused" if i == self._cursor else "dm-item"
                container.mount(
                    Label(self._item_text(i, state), id=f"dm-item-{i}", classes=classes),
                    before=hint,
                )

    def _move_cursor(self, delta: int) -> None:
        if not self._detached:
            return
        old = self._cursor
        self._cursor = (self._cursor + delta) % len(self._detached)
        try:
            self.query_one(f"#dm-item-{old}", Label).remove_class("dm-item-focused")
            self.query_one(f"#dm-item-{self._cursor}", Label).add_class("dm-item-focused")
        except Exception:
            pass

    def _do_reattach(self) -> None:
        if not self._detached:
            return
        state = self._detached.pop(self._cursor)
        self._on_reattach(state)
        self._rebuild_items()

    def _do_kill(self) -> None:
        if not self._detached:
            return
        state = self._detached.pop(self._cursor)
        self._on_kill(state)
        self._rebuild_items()

    def on_key(self, event: Key) -> None:
        key  = event.key
        char = event.character or ""

        if key == "escape":
            self.dismiss(None)
            event.stop()
            return

        if key in ("down", "j"):
            self._move_cursor(1)
            event.stop()
            return

        if key in ("up", "k"):
            self._move_cursor(-1)
            event.stop()
            return

        if key == "enter":
            self._do_reattach()
            event.stop()
            return

        if key == "delete":
            self._do_kill()
            event.stop()
            return

        if char.isdigit() and int(char) > 0:
            idx = int(char) - 1
            if idx < len(self._detached):
                old = self._cursor
                self._cursor = idx
                try:
                    self.query_one(f"#dm-item-{old}", Label).remove_class("dm-item-focused")
                    self.query_one(f"#dm-item-{self._cursor}", Label).add_class("dm-item-focused")
                except Exception:
                    pass
            event.stop()
            return


# ---------------------------------------------------------------------------
# _ObsidianPathScreen — tiny modal to enter an Obsidian vault path
# ---------------------------------------------------------------------------

class _ObsidianPathScreen(ModalScreen):
    """Simple path-input modal for connecting an Obsidian vault."""

    DEFAULT_CSS = """
    _ObsidianPathScreen { align: center middle; }
    #obs-container {
        width: 72;
        height: auto;
        background: $surface;
        border: solid $accent;
        padding: 1 2;
    }
    #obs-title  { text-align: center; color: $accent; margin-bottom: 1; }
    #obs-label  { color: $text-muted; margin-bottom: 0; }
    #obs-input  { width: 1fr; margin-bottom: 1; }
    #obs-hint   { color: $text-muted; text-align: center; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical(id="obs-container"):
            yield Label("─── Connect Obsidian Vault ───", id="obs-title")
            yield Label("Path to your Obsidian vault folder:", id="obs-label")
            yield Input(placeholder="~/Documents/ObsidianVault", id="obs-input")
            yield Label("↵ connect  Esc cancel", id="obs-hint")

    def on_mount(self) -> None:
        self.query_one("#obs-input", Input).focus()

    def on_key(self, event: Key) -> None:
        if event.key == "escape":
            self.dismiss(None)
            event.stop()

    @on(Input.Submitted, "#obs-input")
    def _submitted(self, event: Input.Submitted) -> None:
        path = event.value.strip()
        self.dismiss(path if path else None)
