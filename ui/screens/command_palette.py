"""
ui/screens/command_palette.py — CommandPaletteScreen modal.
"""
from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, Label


class CommandPaletteScreen(ModalScreen):
    """
    Searchable command palette.

    ↑ / ↓ or ctrl+p / ctrl+n   navigate list
    Enter                        execute selected command
    Escape                       close
    """

    DEFAULT_CSS = """
    CommandPaletteScreen { align: center middle; }
    #cp-container {
        width: 86;
        max-height: 36;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #cp-title {
        text-align: center;
        color: $accent;
        height: 1;
        margin-bottom: 1;
    }
    #cp-input  { width: 1fr; margin-bottom: 1; border: tall $accent; }
    #cp-list   { height: auto; max-height: 24; }
    .cp-item {
        padding: 0 1;
        color: $text;
        width: 1fr;
        height: 1;
    }
    .cp-item-active {
        color: $accent;
    }
    .cp-item-focused {
        background: $accent-darken-2;
        color: $text;
    }
    .cp-item-focused.cp-item-active {
        background: $accent-darken-1;
        color: $accent-lighten-2;
    }
    .cp-empty {
        color: $text-muted;
        text-align: center;
        padding: 1;
    }
    #cp-hint {
        color: $text-muted;
        text-align: center;
        height: 1;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", show=False),
    ]

    _HINT_NORMAL = "↑↓ navigate  ↵ execute  Esc close"

    def __init__(
        self,
        commands: "list[tuple[str, str, str | None]]",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._all: list[tuple[str, str, str | None]] = commands
        self._filtered: list[tuple[str, str, str | None]] = [
            command for command in commands if command[2] is not None
        ]
        self._cursor = 0
        self._render_nonce = 0

    def compose(self) -> ComposeResult:
        with Vertical(id="cp-container"):
            yield Label("─── Command Palette ───", id="cp-title")
            yield Input(placeholder="type to search…", id="cp-input")
            yield ScrollableContainer(id="cp-list")
            yield Label(self._HINT_NORMAL, id="cp-hint")

    def on_mount(self) -> None:
        self._rebuild_list()
        self.query_one("#cp-input", Input).focus()

    def _set_app_theme(self, theme_name: str) -> None:
        apply_theme = getattr(self.app, "_apply_theme", None)
        if callable(apply_theme):
            apply_theme(
                theme_name,
                persist_config=True,
                persist_session=True,
                notify=False,
            )
            return
        if theme_name in self.app.available_themes:
            try:
                self.app.theme = theme_name
            except Exception:
                pass

    @on(Input.Changed, "#cp-input")
    def _on_filter(self, event: Input.Changed) -> None:
        query = event.value.strip().lower()
        if query:
            self._filtered = [
                (name, desc, key)
                for name, desc, key in self._all
                if key is not None and (query in name.lower() or query in desc.lower())
            ]
        else:
            self._filtered = [command for command in self._all if command[2] is not None]
        self._cursor = 0
        self._rebuild_list()

    @on(Input.Submitted, "#cp-input")
    def _on_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self._execute()

    def _rebuild_list(self) -> None:
        sc = self.query_one("#cp-list", ScrollableContainer)
        self._render_nonce += 1
        for child in list(sc.children):
            child.remove()
        if not self._filtered:
            sc.mount(Label("(no matches)", classes="cp-empty"))
            return
        for i, (name, desc, _) in enumerate(self._filtered):
            is_active = name.startswith("✓")
            is_focused = i == self._cursor
            classes = "cp-item"
            if is_active:
                classes += " cp-item-active"
            if is_focused:
                classes += " cp-item-focused"
            display_name = f"[bold]{name}[/bold]"
            text = f"{display_name}  [dim]{desc}[/dim]"
            sc.mount(Label(text, id=self._item_id(i), classes=classes))

    def _item_id(self, index: int) -> str:
        return f"cp-item-{self._render_nonce}-{index}"

    def _move_cursor(self, delta: int) -> None:
        if not self._filtered:
            return
        old = self._cursor
        self._cursor = (self._cursor + delta) % len(self._filtered)
        try:
            self.query_one(f"#{self._item_id(old)}", Label).remove_class("cp-item-focused")
        except Exception:
            pass
        try:
            item = self.query_one(f"#{self._item_id(self._cursor)}", Label)
            item.add_class("cp-item-focused")
            item.scroll_visible(animate=False)
        except Exception:
            pass

    def on_key(self, event: Key) -> None:
        key = event.key
        input_focused = self.app.focused is self.query_one("#cp-input", Input)
        if key == "up":
            self._move_cursor(-1)
            event.prevent_default()
            event.stop()
        elif key == "down":
            self._move_cursor(1)
            event.prevent_default()
            event.stop()
        elif key == "enter":
            if input_focused:
                return
            self._execute()
            event.stop()
        elif key == "escape":
            self.action_cancel()
            event.stop()

    def _execute(self) -> None:
        if not self._filtered:
            return
        _, _, key = self._filtered[self._cursor]
        if key and key.startswith("theme:"):
            self._set_app_theme(key[len("theme:"):])
            self.dismiss(None)
        else:
            self.dismiss(key)

    def action_cancel(self) -> None:
        self.dismiss(None)
