"""
ui/screens/command_palette.py — CommandPaletteScreen modal.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import ScrollableContainer, Vertical
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Input, Label
from textual import on


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
    .cp-separator {
        color: $text-disabled;
        height: 1;
        padding: 0 1;
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

    _HINT_NORMAL  = "↑↓ navigate  ↵ execute  Esc close"
    _HINT_CONFIRM = "[bold]↵[/bold] Keep theme   [bold]⌫[/bold] Try another   [bold]Esc[/bold] Browse all"

    def __init__(
        self,
        commands: "list[tuple[str, str, str | None]]",
        **kwargs,
    ) -> None:
        """
        *commands* is a list of (name, description, id_key) tuples.
        ``id_key`` is a string key returned on dismiss so the caller can
        identify which command was chosen (None for separators).
        """
        super().__init__(**kwargs)
        self._all:      list[tuple[str, str, str | None]] = commands
        self._filtered: list[tuple[str, str, str | None]] = [
            c for c in commands if c[2] is not None
        ]
        self._cursor:            int  = 0
        self._confirm_mode:      bool = False
        self._confirm_key:       str  = ""
        self._original_theme:    str  = ""
        # Guard that prevents _on_filter from overwriting _filtered/_cursor
        # when we're programmatically filtering to the theme picker.
        self._theme_picker_mode: bool = False

    def compose(self) -> ComposeResult:
        with Vertical(id="cp-container"):
            yield Label("─── Command Palette ───", id="cp-title")
            yield Input(placeholder="type to search…", id="cp-input")
            yield ScrollableContainer(id="cp-list")
            yield Label(self._HINT_NORMAL, id="cp-hint")

    def on_mount(self) -> None:
        self._original_theme = getattr(self.app, "theme", "textual-dark")
        self._rebuild_list()
        self.query_one("#cp-input", Input).focus()

    # ── filtering ────────────────────────────────────────────────────────────

    @on(Input.Changed, "#cp-input")
    def _on_filter(self, event: Input.Changed) -> None:
        # Suppress the next automatic filter event when we're programmatically
        # restoring the theme picker so _filtered / _cursor aren't overwritten.
        if self._theme_picker_mode:
            self._theme_picker_mode = False
            return
        q = event.value.strip().lower()
        if q:
            self._filtered = [
                (name, desc, key)
                for name, desc, key in self._all
                if key is not None and (q in name.lower() or q in desc.lower())
            ]
        else:
            self._filtered = [c for c in self._all if c[2] is not None]
        self._cursor = 0
        self._rebuild_list()

    def _rebuild_list(self) -> None:
        sc = self.query_one("#cp-list", ScrollableContainer)
        for w in list(sc.children):
            w.remove()
        if not self._filtered:
            sc.mount(Label("(no matches)", classes="cp-empty"))
            return
        for i, (name, desc, _) in enumerate(self._filtered):
            is_active  = name.startswith("✓")
            is_focused = i == self._cursor
            classes = "cp-item"
            if is_active:
                classes += " cp-item-active"
            if is_focused:
                classes += " cp-item-focused"
            if is_active:
                display_name = f"[bold]✓ {name[2:]}[/bold]"
            else:
                display_name = f"[bold]  {name[2:]}[/bold]"
            text = f"{display_name}  [dim]{desc}[/dim]"
            sc.mount(Label(text, id=f"cp-item-{i}", classes=classes))

    # ── navigation ───────────────────────────────────────────────────────────

    def _move_cursor(self, delta: int) -> None:
        if not self._filtered:
            return
        old          = self._cursor
        self._cursor = (self._cursor + delta) % len(self._filtered)
        try:
            self.query_one(f"#cp-item-{old}", Label).remove_class("cp-item-focused")
        except Exception:
            pass
        try:
            item = self.query_one(f"#cp-item-{self._cursor}", Label)
            item.add_class("cp-item-focused")
            item.scroll_visible(animate=False)
        except Exception:
            pass

    # ── confirm mode helpers ─────────────────────────────────────────────────

    def _enter_confirm(self, key: str, theme_name: str) -> None:
        """Apply *theme_name* immediately and switch to confirm mode."""
        if theme_name in self.app.available_themes:
            try:
                self.app.theme = theme_name
            except Exception:
                pass
        self._confirm_mode = True
        self._confirm_key  = key
        inp = self.query_one("#cp-input", Input)
        inp.value    = f"✓  {theme_name}"
        inp.disabled = True
        self.query_one("#cp-hint", Label).update(self._HINT_CONFIRM)

    def _exit_confirm(self, show_themes: bool = False) -> None:
        """Revert theme and return to browse mode.

        If *show_themes* is True, pre-filter the list to theme entries and
        place the cursor on the entry that was just previewed so the user can
        quickly pick a different theme.  Otherwise (Escape) the full list is
        restored.
        """
        prev_key           = self._confirm_key
        self._confirm_mode = False
        self._confirm_key  = ""
        try:
            if self.app.theme != self._original_theme:
                self.app.theme = self._original_theme
        except Exception:
            pass
        inp = self.query_one("#cp-input", Input)
        inp.disabled = False
        self.query_one("#cp-hint", Label).update(self._HINT_NORMAL)
        if show_themes:
            self._filtered = [
                (name, desc, key)
                for name, desc, key in self._all
                if key is not None and ("theme" in name.lower() or "theme" in desc.lower())
            ]
            self._cursor = 0
            for i, (_, _, key) in enumerate(self._filtered):
                if key == prev_key:
                    self._cursor = i
                    break
            # Set the flag before updating inp.value so the resulting
            # Input.Changed event is swallowed and doesn't reset _cursor.
            self._theme_picker_mode = True
            inp.value = "theme"
        else:
            inp.value    = ""
            self._filtered = [c for c in self._all if c[2] is not None]
            self._cursor   = 0
        self._rebuild_list()
        try:
            self.query_one(f"#cp-item-{self._cursor}", Label).scroll_visible(animate=False)
        except Exception:
            pass
        inp.focus()

    # ── key handling ─────────────────────────────────────────────────────────

    def on_key(self, event: Key) -> None:
        key = event.key
        if self._confirm_mode:
            if key == "enter":
                event.stop()
                self.dismiss(self._confirm_key)
            elif key in ("backspace", "delete"):
                event.stop()
                self._exit_confirm(show_themes=True)
            elif key == "escape":
                event.stop()
                self._exit_confirm()
            return
        if key == "up":
            self._move_cursor(-1)
            event.prevent_default()
            event.stop()
        elif key == "down":
            self._move_cursor(1)
            event.prevent_default()
            event.stop()
        elif key == "enter":
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
            self._enter_confirm(key, key[len("theme:"):])
        else:
            self.dismiss(key)

    def action_cancel(self) -> None:
        if self._confirm_mode:
            self._exit_confirm()
        else:
            self.dismiss(None)
