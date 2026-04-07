"""PTYWidget — real PTY terminal for Textual using pyte + ptyprocess.

Design:
  - Extends Static (push model via update()) rather than Widget + render()
    to avoid Textual layout-sizing feedback loops.
  - Shell is spawned on first on_resize (not on_mount) so we always have
    real dimensions before creating the PTY.
  - Reader thread only sets a dirty flag; a 30 fps set_interval poller
    does the actual Static.update() on the main thread.
  - ctrl+t bubbles up to App for terminal toggle; all other keys are
    routed to the PTY stdin and stopped.
"""
from __future__ import annotations

import os
import shutil
import threading
from typing import Optional

from textual.widgets import Static
from textual.events import Key, Resize
from rich.text import Text
from rich.style import Style

try:
    import pyte
    import ptyprocess as _ptyproc
    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False


# ---------------------------------------------------------------------------
# Key name → PTY byte sequence
# ---------------------------------------------------------------------------

_KEY_TO_SEQ: dict[str, bytes] = {
    "enter":          b"\r",
    "backspace":      b"\x7f",
    "delete":         b"\x1b[3~",
    "tab":            b"\t",
    "escape":         b"\x1b",
    "up":             b"\x1b[A",
    "down":           b"\x1b[B",
    "right":          b"\x1b[C",
    "left":           b"\x1b[D",
    "home":           b"\x1b[H",
    "end":            b"\x1b[F",
    "pageup":         b"\x1b[5~",
    "pagedown":       b"\x1b[6~",
    "insert":         b"\x1b[2~",
    "f1":             b"\x1bOP",
    "f2":             b"\x1bOQ",
    "f3":             b"\x1bOR",
    "f4":             b"\x1bOS",
    "f5":             b"\x1b[15~",
    "f6":             b"\x1b[17~",
    "f7":             b"\x1b[18~",
    "f8":             b"\x1b[19~",
    "f9":             b"\x1b[20~",
    "f10":            b"\x1b[21~",
    "f11":            b"\x1b[23~",
    "f12":            b"\x1b[24~",
    "ctrl+a":         b"\x01",
    "ctrl+b":         b"\x02",
    "ctrl+c":         b"\x03",
    "ctrl+d":         b"\x04",
    "ctrl+e":         b"\x05",
    "ctrl+f":         b"\x06",
    "ctrl+g":         b"\x07",
    "ctrl+h":         b"\x08",
    "ctrl+i":         b"\t",
    "ctrl+j":         b"\n",
    "ctrl+k":         b"\x0b",
    "ctrl+l":         b"\x0c",
    "ctrl+m":         b"\r",
    "ctrl+n":         b"\x0e",
    "ctrl+o":         b"\x0f",
    "ctrl+p":         b"\x10",
    "ctrl+q":         b"\x11",
    "ctrl+r":         b"\x12",
    "ctrl+s":         b"\x13",
    # ctrl+t intentionally omitted — reserved for terminal toggle
    "ctrl+u":         b"\x15",
    "ctrl+v":         b"\x16",
    "ctrl+w":         b"\x17",
    "ctrl+x":         b"\x18",
    "ctrl+y":         b"\x19",
    "ctrl+z":         b"\x1a",
    "ctrl+backslash": b"\x1c",
    "ctrl+left":      b"\x1b[1;5D",
    "ctrl+right":     b"\x1b[1;5C",
    "ctrl+up":        b"\x1b[1;5A",
    "ctrl+down":      b"\x1b[1;5B",
    "shift+tab":      b"\x1b[Z",
    "shift+up":       b"\x1b[1;2A",
    "shift+down":     b"\x1b[1;2B",
    "shift+left":     b"\x1b[1;2D",
    "shift+right":    b"\x1b[1;2C",
}

# Pyte named color → Rich color name
_PYTE_NAMED: dict[str, str] = {
    "black":         "black",
    "red":           "red",
    "green":         "green",
    "yellow":        "yellow",
    "blue":          "blue",
    "magenta":       "magenta",
    "cyan":          "cyan",
    "white":         "white",
    "brightblack":   "bright_black",
    "brightred":     "bright_red",
    "brightgreen":   "bright_green",
    "brightyellow":  "bright_yellow",
    "brightblue":    "bright_blue",
    "brightmagenta": "bright_magenta",
    "brightcyan":    "bright_cyan",
    "brightwhite":   "bright_white",
}


def _to_rich_color(color) -> Optional[str]:
    """Convert a pyte color value to a Rich color string (None = terminal default)."""
    if not color or color == "default":
        return None
    c = str(color).lower().strip()
    if c in _PYTE_NAMED:
        return _PYTE_NAMED[c]
    try:
        idx = int(c)
        if 0 <= idx <= 255:
            return f"color({idx})"
    except (ValueError, TypeError):
        pass
    if len(c) == 6:
        try:
            int(c, 16)
            return f"#{c}"
        except ValueError:
            pass
    return None


# ---------------------------------------------------------------------------
# PTYWidget
# ---------------------------------------------------------------------------

class PTYWidget(Static, can_focus=True):
    """
    Real embedded PTY terminal.

    Spawns the user's shell in a pseudo-terminal, emulates VT100 via pyte,
    and pushes the rendered screen into a Static widget at ~30 fps.

    Press ctrl+t to close the terminal panel (returns focus to command mode).
    All other keys are forwarded directly to the shell.
    """

    DEFAULT_CSS = """
    PTYWidget {
        height: 1fr;
        overflow: hidden hidden;
        background: $background;
    }
    PTYWidget:focus {
        border: none;
    }
    """

    def __init__(self, cwd: str = ".", command: Optional[str] = None, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._cwd       = cwd
        self._command   = command
        self._cols      = 80
        self._rows      = 24
        self._screen: Optional["pyte.Screen"]         = None
        self._stream: Optional["pyte.ByteStream"]     = None
        self._proc:   Optional["_ptyproc.PtyProcess"] = None
        self._lock      = threading.Lock()
        self._dirty     = False
        self._spawned   = False

    # ------------------------------------------------------------------ lifecycle

    def on_mount(self) -> None:
        if not _AVAILABLE:
            self.update(
                "[bold red]pyte / ptyprocess not installed.[/bold red]\n"
                "Run: [yellow]pip install pyte ptyprocess[/yellow]"
            )
            return
        # Poll for screen updates at ~30 fps
        self.set_interval(1 / 30, self._poll_update)

    def on_resize(self, event: Resize) -> None:
        new_cols = max(event.size.width, 10)
        new_rows = max(event.size.height, 4)

        if not self._spawned:
            # First valid resize: now we know the real dimensions, spawn shell
            self._cols    = new_cols
            self._rows    = new_rows
            self._spawn()
            self._spawned = True
        else:
            # Resize existing PTY
            changed = (new_cols != self._cols or new_rows != self._rows)
            self._cols = new_cols
            self._rows = new_rows
            if changed and self._screen:
                with self._lock:
                    self._screen.resize(self._rows, self._cols)
                if self._proc and self._proc.isalive():
                    try:
                        self._proc.setwinsize(self._rows, self._cols)
                    except Exception:
                        pass
                self._dirty = True

    def on_focus(self)  -> None: self._dirty = True
    def on_blur(self)   -> None: self._dirty = True

    # ------------------------------------------------------------------ PTY management

    def _shell_cmd(self) -> list[str]:
        if self._command:
            return [self._command]
        shell = (
            os.environ.get("SHELL", "")
            or shutil.which("zsh")
            or shutil.which("bash")
            or "/bin/sh"
        )
        return [shell]

    def _spawn(self) -> None:
        env = {
            **os.environ,
            "TERM":      "xterm-256color",
            "COLORTERM": "truecolor",
            "COLUMNS":   str(self._cols),
            "LINES":     str(self._rows),
        }
        self._screen = pyte.Screen(self._cols, self._rows)
        self._stream = pyte.ByteStream(self._screen)
        try:
            self._proc = _ptyproc.PtyProcess.spawn(
                self._shell_cmd(),
                cwd=self._cwd,
                dimensions=(self._rows, self._cols),
                env=env,
            )
            threading.Thread(target=self._reader_loop, daemon=True).start()
        except Exception as exc:
            # Draw the error into the pyte screen so it shows up in the poll
            if self._screen:
                self._screen.draw(f"[PTY error: {exc}]")
            self._dirty = True

    def _reader_loop(self) -> None:
        """Background thread: reads PTY output and feeds to the pyte stream."""
        while self._proc and self._proc.isalive():
            try:
                data = self._proc.read(4096)
                with self._lock:
                    self._stream.feed(data)
                self._dirty = True
            except (EOFError, OSError):
                break
            except Exception:
                break
        # Shell exited — mark dirty so "Process exited" can be shown
        self._dirty = True

    def _poll_update(self) -> None:
        """Called by set_interval at ~30 fps. Pushes screen to Static if dirty."""
        if not self._dirty or self._screen is None:
            return
        self._dirty = False
        text = self._build_text()
        self.update(text)

    # ------------------------------------------------------------------ public API

    def stop(self) -> None:
        if self._proc and self._proc.isalive():
            try:
                self._proc.terminate(force=True)
            except Exception:
                pass
        self._proc    = None
        self._spawned = False

    def restart(self, cwd: Optional[str] = None) -> None:
        """Kill current shell and start a fresh one (optionally in a new dir)."""
        self.stop()
        if cwd is not None:
            self._cwd = cwd
        # Re-spawn with current dimensions on next resize, or now if already sized
        self._spawned = False
        if self._cols > 0 and self._rows > 0 and _AVAILABLE:
            self._spawn()
            self._spawned = True
            self._dirty   = True

    def set_cwd(self, cwd: str) -> None:
        self.restart(cwd=cwd)

    def run_command(self, cmd: str) -> None:
        """Send a command followed by Enter to the terminal."""
        if self._proc and self._proc.isalive():
            self._proc.write((cmd + "\n").encode("utf-8", errors="replace"))

    def is_alive(self) -> bool:
        return bool(self._proc and self._proc.isalive())

    # ------------------------------------------------------------------ key handling

    def on_key(self, event: Key) -> None:
        """Forward keys to the PTY. ctrl+t is reserved for terminal toggle."""
        key = event.key

        # Let ctrl+t bubble up so the App can close the terminal
        if key == "ctrl+t":
            return

        if not self._proc or not self._proc.isalive():
            return

        seq = _KEY_TO_SEQ.get(key)
        if seq is not None:
            self._proc.write(seq)
            event.stop()
            return

        char = event.character
        if char:
            self._proc.write(char.encode("utf-8", errors="replace"))
            event.stop()

    # ------------------------------------------------------------------ screen rendering

    def _build_text(self) -> Text:
        """Snapshot the pyte screen buffer as a Rich Text object."""
        if self._screen is None:
            return Text("")

        cursor_y    = self._screen.cursor.y
        cursor_x    = self._screen.cursor.x
        show_cursor = self.has_focus and self.is_alive()

        text = Text(no_wrap=True, overflow="crop")
        with self._lock:
            for y in range(self._screen.lines):
                row = self._screen.buffer[y]
                for x in range(self._screen.columns):
                    char = row[x]
                    ch   = char.data if char.data else " "

                    if show_cursor and y == cursor_y and x == cursor_x:
                        style = Style(reverse=True)
                    else:
                        fg = _to_rich_color(char.fg)
                        bg = _to_rich_color(char.bg)
                        style = Style(
                            color=fg,
                            bgcolor=bg,
                            bold=getattr(char, "bold",         False),
                            italic=getattr(char, "italics",    False),
                            underline=getattr(char, "underscore", False),
                            strike=getattr(char, "strikethrough", False),
                            reverse=getattr(char, "reverse",   False),
                            blink=getattr(char, "blink",       False),
                        )
                    text.append(ch, style=style)

                if y < self._screen.lines - 1:
                    text.append("\n")

        return text
