"""SSH filesystem mounting via sshfs.

Provides helpers for mounting and unmounting remote directories locally so
agent CLIs (claude, cursor, codex) can work on them as if they were local.

Requirements:
  macOS: brew install macfuse  (provides sshfs)
  Linux: apt install sshfs  /  pacman -S sshfs

The mount base directory is ~/.vibe-cli/mounts/.
Each mount gets a unique subdirectory: <user>_<host>_<port>_<slug>.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field


_MOUNT_BASE = os.path.expanduser("~/.vibe-cli/mounts")


def _slug(text: str) -> str:
    """Sanitize a string for use in a directory name."""
    return re.sub(r"[^a-zA-Z0-9._-]", "_", text)[:40]


def is_available() -> bool:
    """Return True if sshfs is on PATH."""
    return shutil.which("sshfs") is not None


@dataclass
class SSHInfo:
    host:        str
    user:        str = ""
    port:        int = 22
    remote_path: str = "~"

    @property
    def connection(self) -> str:
        """sshfs source string, e.g. user@host:/remote/path"""
        prefix = f"{self.user}@" if self.user else ""
        return f"{prefix}{self.host}:{self.remote_path}"

    @property
    def display(self) -> str:
        """Human-readable label for the tab bar."""
        prefix = f"{self.user}@" if self.user else ""
        port_str = f":{self.port}" if self.port != 22 else ""
        return f"{prefix}{self.host}{port_str}:{self.remote_path}"

    def mount_dir(self) -> str:
        """Return (but do not create) the local mountpoint path."""
        user_part = _slug(self.user) + "_" if self.user else ""
        port_part = f"_{self.port}" if self.port != 22 else ""
        name = f"{user_part}{_slug(self.host)}{port_part}_{_slug(self.remote_path)}"
        return os.path.join(_MOUNT_BASE, name)

    def to_dict(self) -> dict:
        return {
            "host":        self.host,
            "user":        self.user,
            "port":        self.port,
            "remote_path": self.remote_path,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SSHInfo":
        return cls(
            host        = d.get("host", ""),
            user        = d.get("user", ""),
            port        = int(d.get("port", 22)),
            remote_path = d.get("remote_path", "~"),
        )


def mount(info: SSHInfo, *, timeout: int = 15) -> str:
    """
    Mount info.remote_path on the local machine via sshfs.
    Returns the local mountpoint path on success.
    Raises RuntimeError on failure.
    """
    if not is_available():
        raise RuntimeError(
            "sshfs not found. Install it first:\n"
            "  macOS:  brew install macfuse\n"
            "  Linux:  sudo apt install sshfs"
        )

    mountpoint = info.mount_dir()
    os.makedirs(mountpoint, exist_ok=True)

    # If already mounted, return immediately
    if _is_mounted(mountpoint):
        return mountpoint

    cmd = [
        "sshfs",
        info.connection,
        mountpoint,
        "-o", "reconnect",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", f"ConnectTimeout={timeout}",
    ]
    if info.port != 22:
        cmd += ["-p", str(info.port)]

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
    if result.returncode != 0:
        # Clean up empty mountpoint
        try:
            os.rmdir(mountpoint)
        except OSError:
            pass
        raise RuntimeError(
            f"sshfs failed (exit {result.returncode}):\n"
            f"{(result.stderr or result.stdout).strip()}"
        )

    return mountpoint


def unmount(mountpoint: str) -> None:
    """Unmount a previously mounted sshfs directory (best-effort)."""
    if not _is_mounted(mountpoint):
        return
    # Try umount first (macOS/Linux), then fusermount (Linux only)
    for cmd in (["umount", mountpoint], ["fusermount", "-u", mountpoint]):
        try:
            result = subprocess.run(cmd, capture_output=True, timeout=10)
            if result.returncode == 0:
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            continue
    try:
        os.rmdir(mountpoint)
    except OSError:
        pass


def _is_mounted(mountpoint: str) -> bool:
    """Return True if the path is currently a mount point."""
    try:
        result = subprocess.run(
            ["mountpoint", "-q", mountpoint],
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fallback: compare device ids of the path and its parent
    try:
        return os.stat(mountpoint).st_dev != os.stat(os.path.dirname(mountpoint)).st_dev
    except OSError:
        return False
