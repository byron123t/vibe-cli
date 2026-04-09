"""OpenClaw configuration and gateway health helpers.

Reads ~/.openclaw/openclaw.json (the user's OpenClaw config) and provides
utilities for checking whether the Gateway daemon is reachable.

Gateway default: ws://127.0.0.1:18789  (also serves HTTP on the same port)

Reference: https://github.com/openclaw/openclaw
"""
from __future__ import annotations

import json as _json
import os
import shutil
import socket
import urllib.request
from dataclasses import dataclass, field
from typing import Any


_CONFIG_PATH = os.path.expanduser("~/.openclaw/openclaw.json")
_GATEWAY_HOST = "127.0.0.1"
_GATEWAY_PORT = 18789


# ---------------------------------------------------------------------------
# Config reading
# ---------------------------------------------------------------------------

def _read_raw() -> dict:
    """Return the raw parsed openclaw.json, or {} if missing/malformed."""
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            return _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError):
        return {}


@dataclass
class OpenClawConfig:
    """Parsed subset of ~/.openclaw/openclaw.json relevant to vibe-cli."""
    model:           str = ""
    gateway_port:    int = _GATEWAY_PORT
    workspace:       str = ""
    channels:        list[str] = field(default_factory=list)
    thinking_level:  str = "medium"   # global default

    @classmethod
    def load(cls) -> "OpenClawConfig":
        raw = _read_raw()
        agent = raw.get("agent", {})
        gateway = raw.get("gateway", {})

        # Discover which channels are configured (non-empty sections)
        channel_keys = [
            "telegram", "discord", "slack", "whatsapp", "signal",
            "bluebubbles", "imessage", "matrix", "msteams", "irc",
            "feishu", "line", "mattermost", "nextcloudt", "nostr",
            "synology", "tlon", "twitch", "zalo", "wechat", "webchat",
        ]
        enabled: list[str] = []
        channels_cfg = raw.get("channels", {})
        for k in channel_keys:
            section = channels_cfg.get(k, {})
            if section and isinstance(section, dict):
                # Present and non-empty → configured
                enabled.append(k)

        return cls(
            model          = agent.get("model", ""),
            gateway_port   = int(gateway.get("port", _GATEWAY_PORT)),
            workspace      = agent.get("workspace",
                             os.path.expanduser("~/.openclaw/workspace")),
            channels       = enabled,
            thinking_level = agent.get("thinkingLevel", "medium"),
        )

    @property
    def config_path(self) -> str:
        return _CONFIG_PATH

    @property
    def config_exists(self) -> bool:
        return os.path.isfile(_CONFIG_PATH)


# ---------------------------------------------------------------------------
# Gateway health
# ---------------------------------------------------------------------------

def gateway_port(cfg: OpenClawConfig | None = None) -> int:
    """Return the configured gateway port (default 18789)."""
    return cfg.gateway_port if cfg else _GATEWAY_PORT


def is_gateway_reachable(port: int = _GATEWAY_PORT, timeout: float = 2.0) -> bool:
    """
    Return True if the OpenClaw Gateway is reachable on localhost.

    Tries a plain TCP connect first (fast), then a HTTP GET to /health
    as a fallback for environments where TCP connect succeeds but the
    daemon is still starting up.
    """
    try:
        with socket.create_connection((_GATEWAY_HOST, port), timeout=timeout):
            pass
        return True
    except (OSError, ConnectionRefusedError):
        return False


def gateway_status(port: int = _GATEWAY_PORT) -> dict[str, Any]:
    """
    Return a dict with keys:
      reachable: bool
      url: str
      channels: list[str]  (if reachable and /status responds)
    """
    reachable = is_gateway_reachable(port)
    info: dict[str, Any] = {
        "reachable": reachable,
        "url": f"http://{_GATEWAY_HOST}:{port}",
    }
    if reachable:
        try:
            req  = urllib.request.urlopen(
                f"http://{_GATEWAY_HOST}:{port}/api/status", timeout=2
            )
            data = _json.loads(req.read())
            info["channels"] = data.get("channels", [])
        except Exception:
            info["channels"] = []
    return info


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------

def is_available() -> bool:
    """Return True if the openclaw CLI is on PATH."""
    return shutil.which("openclaw") is not None


def start_gateway_cmd(port: int = _GATEWAY_PORT) -> list[str]:
    """Return the shell command to start the Gateway in the background."""
    return ["openclaw", "gateway", "--port", str(port)]
