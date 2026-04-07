"""ClaudeCLIBridge — subprocess wrapper for the `claude` CLI tool."""
from __future__ import annotations

import queue
import shutil
import subprocess
import threading
import time
from typing import Iterator


class ClaudeCLIBridge:
    """
    Runs the Claude Code CLI in a subprocess and streams output lines.
    Output is available via a queue (non-blocking for the UI thread).
    """

    def __init__(self, working_dir: str = ".") -> None:
        self.working_dir = working_dir
        self._proc: subprocess.Popen | None = None
        self._output_queue: queue.Queue[str] = queue.Queue()
        self._running = False

    def is_available(self) -> bool:
        return shutil.which("claude") is not None

    def run_async(self, prompt: str,
                  flags: list[str] | None = None,
                  on_line: callable | None = None) -> None:
        """
        Run a claude CLI command in a background thread.
        Lines are put into _output_queue and optionally passed to on_line callback.
        """
        if self._running:
            return  # only one run at a time

        def _worker():
            self._running = True
            cmd = ["claude", "--print", prompt] + (flags or [])
            try:
                proc = subprocess.Popen(
                    cmd,
                    cwd=self.working_dir,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
                self._proc = proc
                for line in proc.stdout:
                    line = line.rstrip("\n")
                    self._output_queue.put(line)
                    if on_line:
                        on_line(line)
                proc.wait()
            except FileNotFoundError:
                err = "[ClaudeCLI] `claude` not found — install Claude Code CLI first."
                self._output_queue.put(err)
                if on_line:
                    on_line(err)
            except Exception as e:
                msg = f"[ClaudeCLI] Error: {e}"
                self._output_queue.put(msg)
                if on_line:
                    on_line(msg)
            finally:
                self._running = False
                self._proc = None

        t = threading.Thread(target=_worker, daemon=True)
        t.start()

    def run_sync(self, prompt: str,
                 flags: list[str] | None = None,
                 timeout: float = 120.0) -> str:
        """Blocking run — returns full output as string."""
        cmd = ["claude", "--print", prompt] + (flags or [])
        try:
            result = subprocess.run(
                cmd,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.stdout + result.stderr
        except FileNotFoundError:
            return "[ClaudeCLI] `claude` not found."
        except subprocess.TimeoutExpired:
            return "[ClaudeCLI] Timed out."
        except Exception as e:
            return f"[ClaudeCLI] Error: {e}"

    def drain_output(self) -> list[str]:
        """Drain all available output lines from the queue (non-blocking)."""
        lines = []
        while True:
            try:
                lines.append(self._output_queue.get_nowait())
            except queue.Empty:
                break
        return lines

    def cancel(self) -> None:
        if self._proc:
            self._proc.terminate()
            self._running = False

    @property
    def is_running(self) -> bool:
        return self._running
