"""
ui/screens/directory_picker.py — DirectoryPickerScreen modal.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.events import Key
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Input, Label
from textual import on


class DirectoryPickerScreen(ModalScreen):
    """Modal overlay: navigate the filesystem and open a directory as a project.

    Tab bar at the top toggles between local filesystem picker and Remote SSH form.
    Dismisses with:
      str                         — local directory path
      {"path": ..., "ssh_info": ...}  — SSH-mounted project
      None                        — cancelled
    """

    DEFAULT_CSS = """
    DirectoryPickerScreen {
        align: center middle;
    }
    #dp-container {
        width: 72;
        height: 40;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    #dp-tabs    { height: 3; margin-bottom: 1; }
    .dp-tab     { width: 16; background: $surface; color: $text-muted; }
    .dp-tab-active { background: $accent-darken-2; color: $text; }
    #dp-header  { height: 1; color: $text-muted; }
    #dp-tree    { height: 1fr; border: solid $primary-darken-2; margin: 1 0; }
    #dp-input   { height: 3; border: tall $accent; }
    #dp-footer  { height: 1; color: $text-muted; }
    #dp-ssh-pane { height: 1fr; }
    .dp-ssh-label { color: $text-muted; margin-top: 1; height: 1; }
    .dp-ssh-input { margin-bottom: 0; border: tall $primary-darken-2; }
    #dp-ssh-hint  { color: $text-muted; margin-top: 1; }
    """

    BINDINGS = [
        Binding("escape",    "cancel", "Cancel", show=False),
        Binding("backspace", "cancel", "Cancel", show=False),
    ]

    def __init__(self, start_path: str | None = None, **kwargs) -> None:
        super().__init__(**kwargs)
        self._tree_root  = os.path.expanduser("~")
        self._input_init = start_path or self._tree_root
        self._ssh_mode   = False   # False = local, True = SSH
        self._syncing_input = False  # True while node highlight is writing the input

    def compose(self) -> ComposeResult:
        with Container(id="dp-container"):
            with Horizontal(id="dp-tabs"):
                yield Button("📁 Local",      id="dp-tab-local", classes="dp-tab dp-tab-active")
                yield Button("🌐 Remote SSH", id="dp-tab-ssh",   classes="dp-tab")

            # ── Local pane ────────────────────────────────────────────────
            with Container(id="dp-local-pane"):
                yield Label(
                    " OPEN PROJECT  [dim]↑↓ tree · Enter = open · type path · Escape = cancel[/dim]",
                    id="dp-header",
                )
                yield DirectoryTree(self._tree_root, id="dp-tree")
                yield Input(
                    value=self._input_init,
                    placeholder="type a path to jump the tree there…",
                    id="dp-input",
                )
                yield Label(
                    " Tab = focus tree · Enter in input = open · paths update as you type",
                    id="dp-footer",
                )

            # ── SSH pane (hidden by default) ──────────────────────────────
            with Container(id="dp-ssh-pane"):
                yield Label("Host (e.g. myserver.com or 192.168.1.1):", classes="dp-ssh-label")
                yield Input(placeholder="hostname or IP", id="dp-ssh-host", classes="dp-ssh-input")
                yield Label("Username (leave blank to use default):", classes="dp-ssh-label")
                yield Input(placeholder="user", id="dp-ssh-user", classes="dp-ssh-input")
                yield Label("Port (default 22):", classes="dp-ssh-label")
                yield Input(placeholder="22", id="dp-ssh-port", classes="dp-ssh-input")
                yield Label("Remote path:", classes="dp-ssh-label")
                yield Input(placeholder="~/projects/myapp", id="dp-ssh-path", classes="dp-ssh-input")
                yield Label(
                    "↵ = mount & open  ·  requires sshfs  ·  Esc = cancel",
                    id="dp-ssh-hint",
                )

    def on_mount(self) -> None:
        self.query_one("#dp-ssh-pane").display = False
        self.query_one("#dp-tree", DirectoryTree).focus()

    # ── Tab switching ─────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "dp-tab-local":
            self._set_mode(ssh=False)
        elif bid == "dp-tab-ssh":
            self._set_mode(ssh=True)

    def _set_mode(self, ssh: bool) -> None:
        self._ssh_mode = ssh
        self.query_one("#dp-local-pane").display = not ssh
        self.query_one("#dp-ssh-pane").display   = ssh
        self.query_one("#dp-tab-local").set_class(not ssh, "dp-tab-active")
        self.query_one("#dp-tab-ssh").set_class(ssh,      "dp-tab-active")
        if ssh:
            self.query_one("#dp-ssh-host", Input).focus()
        else:
            self.query_one("#dp-tree", DirectoryTree).focus()

    # ── Local tree handlers ───────────────────────────────────────────────

    @on(DirectoryTree.NodeHighlighted, "#dp-tree")
    def _node_highlighted(self, event) -> None:
        data = event.node.data
        if data is not None:
            path = getattr(data, "path", data)
            self._syncing_input = True
            self.query_one("#dp-input", Input).value = str(path)

    @on(Input.Changed, "#dp-input")
    def _input_changed(self, event: Input.Changed) -> None:
        # Ignore changes written by _node_highlighted — those come from tree
        # navigation (arrow keys) and must not re-root the tree.
        if self._syncing_input:
            self._syncing_input = False
            return
        path = os.path.expanduser(event.value.strip())
        if os.path.isdir(path):
            self.query_one("#dp-tree", DirectoryTree).path = Path(path)

    def on_input_key(self, event: Key) -> None:
        if event.key == "tab":
            if self._ssh_mode:
                return
            inp   = self.query_one("#dp-input", Input)
            typed = os.path.expanduser(inp.value.strip())
            completed = self._tab_complete(typed)
            if completed and completed != typed:
                inp.value  = completed
                inp.cursor_position = len(completed)
            else:
                self.query_one("#dp-tree", DirectoryTree).focus()
            event.stop()

    @staticmethod
    def _tab_complete(typed: str) -> str:
        """Return the longest unambiguous completion for a partial path."""
        if not typed:
            return typed
        if os.path.isdir(typed):
            return typed.rstrip("/") + "/"
        parent  = os.path.dirname(typed) or "/"
        prefix  = os.path.basename(typed).lower()
        if not os.path.isdir(parent):
            return typed
        try:
            matches = sorted(
                e for e in os.listdir(parent)
                if e.lower().startswith(prefix)
                and os.path.isdir(os.path.join(parent, e))
            )
        except PermissionError:
            return typed
        if not matches:
            return typed
        if len(matches) == 1:
            return os.path.join(parent, matches[0]) + "/"
        common = os.path.commonprefix(matches)
        return os.path.join(parent, common) if common else typed

    @on(DirectoryTree.FileSelected, "#dp-tree")
    def _file_selected(self, event: DirectoryTree.FileSelected) -> None:
        self.dismiss(str(Path(str(event.path)).parent))

    def on_directory_tree_directory_selected(self, event) -> None:
        try:
            self.dismiss(str(event.path))
        except AttributeError:
            pass

    @on(Input.Submitted, "#dp-input")
    def _local_submitted(self, event: Input.Submitted) -> None:
        path = os.path.expanduser(event.value.strip())
        if os.path.isdir(path):
            self.dismiss(path)
        else:
            self.notify(f"Not a directory: {path}", severity="error", timeout=3)

    # ── SSH form submit ───────────────────────────────────────────────────

    @on(Input.Submitted, "#dp-ssh-host")
    @on(Input.Submitted, "#dp-ssh-user")
    @on(Input.Submitted, "#dp-ssh-port")
    @on(Input.Submitted, "#dp-ssh-path")
    def _ssh_field_submitted(self, event: Input.Submitted) -> None:
        """Tab through SSH fields; submit on the last one."""
        order = ["dp-ssh-host", "dp-ssh-user", "dp-ssh-port", "dp-ssh-path"]
        current = event.input.id
        try:
            next_id = order[order.index(current) + 1]
            self.query_one(f"#{next_id}", Input).focus()
        except IndexError:
            self._submit_ssh()
        event.stop()

    def _submit_ssh(self) -> None:
        from core.ssh_mount import SSHInfo, mount as ssh_mount, is_available as sshfs_available

        host = self.query_one("#dp-ssh-host", Input).value.strip()
        if not host:
            self.notify("Host is required.", severity="error", timeout=3)
            self.query_one("#dp-ssh-host", Input).focus()
            return

        user      = self.query_one("#dp-ssh-user", Input).value.strip()
        port_str  = self.query_one("#dp-ssh-port", Input).value.strip() or "22"
        rpath     = self.query_one("#dp-ssh-path", Input).value.strip() or "~"

        try:
            port = int(port_str)
        except ValueError:
            self.notify("Port must be a number.", severity="error", timeout=3)
            return

        if not sshfs_available():
            self.notify(
                "sshfs not found. Install: brew install macfuse (macOS) or apt install sshfs (Linux)",
                severity="error", timeout=6,
            )
            return

        info = SSHInfo(host=host, user=user, port=port, remote_path=rpath)
        self.notify(f"Mounting {info.display}…", timeout=4)

        def _do_mount():
            try:
                local = ssh_mount(info, timeout=20)
                self.app.call_from_thread(
                    self.dismiss,
                    {"path": local, "ssh_info": info.to_dict()},
                )
            except Exception as exc:
                self.app.call_from_thread(
                    self.notify,
                    f"SSH mount failed: {exc}",
                    severity="error",
                    timeout=8,
                )

        threading.Thread(target=_do_mount, daemon=True).start()

    def action_cancel(self) -> None:
        self.dismiss(None)
