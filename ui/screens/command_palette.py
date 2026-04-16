"""
ui/screens/command_palette.py — Searchable command palette with inline theme preview.
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

    Browse mode:   ↑↓ navigate   ↵ execute   Esc close
    Preview mode:  ↵ confirm     Del go back
    """

    DEFAULT_CSS = """
    CommandPaletteScreen {
        align: center middle;
        background: transparent;
    }
    #cp-container {
        width: 86;
        max-height: 36;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #cp-title  { text-align: center; color: $accent; height: 1; margin-bottom: 1; }
    #cp-input  { width: 1fr; margin-bottom: 1; border: tall $accent; }
    #cp-list   { height: auto; max-height: 24; }
    #cp-hint   { color: $text-muted; text-align: center; height: 1; margin-top: 1; }

    .cp-item  { padding: 0 1; color: $text; width: 1fr; height: 1; }
    .cp-item-focused { background: $accent-darken-2; }
    .cp-item-active  { color: $accent; }
    .cp-item-focused.cp-item-active { background: $accent-darken-1; color: $accent-lighten-2; }
    .cp-empty { color: $text-muted; text-align: center; padding: 1; }

    #cp-preview {
        display: none;
        background: $surface 85%;
        border: tall $accent;
        padding: 0 3;
        height: 3;
        width: 64;
        content-align: center middle;
    }
    """

    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, commands: list[tuple[str, str, str | None]], **kwargs) -> None:
        super().__init__(**kwargs)
        self._all      = commands
        self._filtered = [c for c in commands if c[2] is not None]
        self._cursor   = 0
        self._nonce    = 0
        # preview state
        self._previewing    = False
        self._preview_theme = ""
        self._prev_theme    = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="cp-container"):
            yield Label("─── Command Palette ───", id="cp-title")
            yield Input(placeholder="type to search…", id="cp-input")
            yield ScrollableContainer(id="cp-list")
            yield Label("↑↓ navigate  ↵ execute  Esc close", id="cp-hint")
        yield Label("", id="cp-preview")

    def on_mount(self) -> None:
        self._rebuild_list()
        self.query_one("#cp-input", Input).focus()

    # ── filter ──────────────────────────────────────────────────────────────

    @on(Input.Changed, "#cp-input")
    def _on_filter(self, event: Input.Changed) -> None:
        q = event.value.strip().lower()
        self._filtered = [
            c for c in self._all
            if c[2] is not None and (not q or q in c[0].lower() or q in c[1].lower())
        ]
        self._cursor = 0
        self._rebuild_list()

    @on(Input.Submitted, "#cp-input")
    def _on_submit(self, event: Input.Submitted) -> None:
        event.stop()
        self._execute()

    # ── list ────────────────────────────────────────────────────────────────

    def _rebuild_list(self) -> None:
        sc = self.query_one("#cp-list", ScrollableContainer)
        self._nonce += 1
        for w in list(sc.children):
            w.remove()
        if not self._filtered:
            sc.mount(Label("(no matches)", classes="cp-empty"))
            return
        for i, (name, desc, _) in enumerate(self._filtered):
            classes = "cp-item"
            if name.startswith("✓"):
                classes += " cp-item-active"
            if i == self._cursor:
                classes += " cp-item-focused"
            sc.mount(Label(f"[bold]{name}[/bold]  [dim]{desc}[/dim]",
                           id=f"cp-{self._nonce}-{i}", classes=classes))

    def _move_cursor(self, delta: int) -> None:
        if not self._filtered:
            return
        prev = self._cursor
        self._cursor = (self._cursor + delta) % len(self._filtered)
        for idx, active in ((prev, False), (self._cursor, True)):
            try:
                w = self.query_one(f"#cp-{self._nonce}-{idx}", Label)
                w.set_class(active, "cp-item-focused")
                if active:
                    w.scroll_visible(animate=False)
            except Exception:
                pass

    # ── keys ────────────────────────────────────────────────────────────────

    def on_key(self, event: Key) -> None:
        if self._previewing:
            if event.key == "enter":
                self._confirm()
            elif event.key in ("delete", "backspace"):
                self._back_to_browse()
            elif event.key == "escape":
                self._back_to_browse()
                self.dismiss(None)
            event.stop()
            return

        if event.key == "up":
            self._move_cursor(-1)
            event.prevent_default()
            event.stop()
        elif event.key == "down":
            self._move_cursor(1)
            event.prevent_default()
            event.stop()
        elif event.key == "enter":
            inp = self.query_one("#cp-input", Input)
            if self.app.focused is not inp:
                self._execute()
                event.stop()
        elif event.key == "escape":
            self.action_cancel()
            event.stop()

    # ── execution / preview ─────────────────────────────────────────────────

    def _execute(self) -> None:
        if not self._filtered:
            return
        _, _, key = self._filtered[self._cursor]
        if key and key.startswith("theme:"):
            self._start_preview(key[len("theme:"):])
        else:
            self.dismiss(key)

    def _start_preview(self, theme_name: str) -> None:
        """Apply theme visually and show the confirm/back bar."""
        self._preview_theme = theme_name
        self._prev_theme    = getattr(self.app, "_ui_theme", "") or self.app.theme
        try:
            self.app.theme = theme_name   # live preview; watch_theme persists it
        except Exception:
            return                        # invalid theme — stay in browse mode
        self._previewing = True
        # Defer display changes to after the CSS recomputation triggered by the
        # theme change, otherwise the layout engine may reset display: none.
        self.call_after_refresh(self._show_preview_ui)

    def _show_preview_ui(self) -> None:
        self.query_one("#cp-container").display = False
        bar = self.query_one("#cp-preview", Label)
        bar.update(f"[bold]{self._preview_theme}[/bold]  ·  [dim]↵ confirm    Del go back[/dim]")
        bar.display = True

    def _confirm(self) -> None:
        """Persist theme to config.json and close."""
        apply = getattr(self.app, "_apply_theme", None)
        if callable(apply):
            # Theme is already applied; this call writes config.json and notifies.
            apply(self._preview_theme, persist_config=True, persist_session=True, notify=True)
        self.dismiss(None)

    def _back_to_browse(self) -> None:
        """Revert to the previous theme and return to the palette list."""
        try:
            self.app.theme = self._prev_theme   # watch_theme corrects the session entry
        except Exception:
            pass
        self._previewing = False
        self.query_one("#cp-preview").display = False
        self.query_one("#cp-container").display = True
        self.query_one("#cp-input", Input).focus()
        # Cursor stays at the position of the theme that was just previewed.

    def action_cancel(self) -> None:
        self.dismiss(None)
