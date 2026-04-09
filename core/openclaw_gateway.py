"""OpenClaw Gateway WebSocket client.

Implements the OpenClaw Gateway protocol v3.
Reference: https://github.com/openclaw/openclaw

Protocol overview
─────────────────
1. Server sends connect.challenge event immediately on connect
2. Client sends `connect` request (with optional auth token/password)
3. Server responds with `hello-ok` (protocol accepted) or closes
4. Client subscribes to `sessions.subscribe` and `sessions.messages.subscribe`
5. Server pushes events:
     chat              — streaming agent response deltas/finals
     chat.side_result  — secondary agent replies
     sessions.changed  — session state changes (new message, running, etc.)
     presence          — device/client connect/disconnect
     tick              — heartbeat
     shutdown          — gateway restart imminent

Session key formats
───────────────────
  agent:main:main                               — main session
  agent:main:direct:<peerId>                    — DM from specific user
  agent:main:<channel>:<peerKind>:<peerId>      — channel-scoped
  acp:<uuid>                                    — ACP bridge session

Wire frames (all JSON):
  { "type": "req",   "id": "...", "method": "...", "params": {...} }
  { "type": "res",   "id": "...", "ok": true,       "payload": {...} }
  { "type": "event", "event": "...", "payload": {...}, "seq": N }
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Callable, Awaitable

log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Data classes for incoming events
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class ChannelMessage:
    """A completed message exchange visible in the inbox."""
    session_key:  str
    channel:      str           # "telegram", "discord", "whatsapp", etc.
    peer_id:      str           # sender identifier
    peer_kind:    str           # "user", "group", etc.
    direction:    str           # "inbound" | "outbound"
    text:         str
    run_id:       str = ""
    ts:           float = 0.0
    model:        str = ""

    @property
    def display_channel(self) -> str:
        ch = self.channel.replace("_", " ").title()
        return ch or "Unknown"

    @property
    def display_peer(self) -> str:
        return self.peer_id[:20] if self.peer_id else "?"


@dataclass
class DeviceEvent:
    """An event from a paired iOS/Android/macOS node."""
    node_id:   str
    node_name: str
    event_type: str     # node.event type field
    payload:    dict = field(default_factory=dict)
    ts:         float = 0.0


@dataclass
class PresenceEntry:
    host:     str
    platform: str = ""
    roles:    list[str] = field(default_factory=list)
    ts:       float = 0.0


# ──────────────────────────────────────────────────────────────────────────────
# Session key parser
# ──────────────────────────────────────────────────────────────────────────────

def _parse_session_key(key: str) -> dict:
    """
    Parse an OpenClaw session key into its components.

    Examples:
      agent:main:main                       → {agent:"main", channel:"main", ...}
      agent:main:telegram:user:12345        → {agent:"main", channel:"telegram",
                                               peer_kind:"user", peer_id:"12345"}
      agent:main:direct:user:abc            → {agent:"main", channel:"direct", ...}
    """
    parts  = key.split(":")
    result = {"raw": key, "agent": "", "channel": "", "peer_kind": "", "peer_id": ""}
    if len(parts) >= 2 and parts[0] == "agent":
        result["agent"]   = parts[1] if len(parts) > 1 else ""
        result["channel"] = parts[2] if len(parts) > 2 else ""
        result["peer_kind"] = parts[3] if len(parts) > 3 else ""
        result["peer_id"]   = parts[4] if len(parts) > 4 else ""
    elif parts[0] == "acp":
        result["channel"] = "acp"
        result["peer_id"] = parts[1] if len(parts) > 1 else ""
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Gateway client
# ──────────────────────────────────────────────────────────────────────────────

class GatewayClient:
    """
    Async WebSocket client for the OpenClaw Gateway.

    Usage::

        client = GatewayClient(port=18789, token="...")
        client.on_message = my_message_handler   # Callable[[ChannelMessage], None]
        client.on_device_event = my_dev_handler  # Callable[[DeviceEvent], None]
        client.on_presence = my_pres_handler     # Callable[[list[PresenceEntry]], None]
        client.on_status = my_status_handler     # Callable[[str], None]  (status text)
        await client.run()   # blocks; call client.stop() to cancel
    """

    PROTOCOL_VERSION = 3

    def __init__(
        self,
        host:     str = "127.0.0.1",
        port:     int = 18789,
        token:    str = "",
        password: str = "",
    ) -> None:
        self._url      = f"ws://{host}:{port}"
        self._token    = token
        self._password = password
        self._stop     = asyncio.Event()
        self._ws       = None

        # Pending RPC responses: id → Future
        self._pending: dict[str, asyncio.Future] = {}

        # Streaming chat buffers: run_id → list[str]
        self._chat_buf: dict[str, list[str]] = {}

        # Callbacks (set by the owner)
        self.on_message:      Callable[[ChannelMessage], None] | None = None
        self.on_device_event: Callable[[DeviceEvent], None] | None    = None
        self.on_presence:     Callable[[list[PresenceEntry]], None] | None = None
        self.on_status:       Callable[[str], None] | None             = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Connect, authenticate, subscribe, and receive events until stopped."""
        try:
            import websockets
        except ImportError:
            self._emit_status("[!] websockets package not installed — pip install websockets")
            return

        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self._url,
                    ping_interval=20,
                    ping_timeout=10,
                    close_timeout=5,
                ) as ws:
                    self._ws = ws
                    self._emit_status("Gateway connected")
                    await self._handshake(ws)
                    await self._subscribe(ws)
                    await self._receive_loop(ws)
            except Exception as exc:
                self._emit_status(f"Gateway disconnected: {exc}")
                if self._stop.is_set():
                    break
                await asyncio.sleep(5)   # reconnect back-off
        self._ws = None

    # ── send helpers ─────────────────────────────────────────────────────────

    async def _send(self, ws, method: str, params: dict) -> dict:
        """Send a request and await its response."""
        req_id = str(uuid.uuid4())
        loop   = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending[req_id] = fut
        frame = _json.dumps({"type": "req", "id": req_id, "method": method, "params": params})
        await ws.send(frame)
        try:
            res = await asyncio.wait_for(fut, timeout=15)
        finally:
            self._pending.pop(req_id, None)
        return res

    async def send_message(self, session_key: str, text: str) -> None:
        """Send a message to a session (reply to an incoming channel message)."""
        if not self._ws:
            return
        try:
            await self._send(self._ws, "chat.send", {
                "sessionKey": session_key,
                "message": text,
            })
        except Exception as exc:
            self._emit_status(f"send_message error: {exc}")

    # ── handshake ────────────────────────────────────────────────────────────

    async def _handshake(self, ws) -> None:
        """Perform the connect.challenge → connect → hello-ok handshake."""
        # Step 1: wait for connect.challenge
        raw  = await asyncio.wait_for(ws.recv(), timeout=10)
        msg  = _json.loads(raw)
        if msg.get("type") != "event" or msg.get("event") != "connect.challenge":
            raise RuntimeError(f"Expected connect.challenge, got: {msg}")
        nonce = msg.get("payload", {}).get("nonce", "")

        # Step 2: send connect
        auth: dict = {}
        if self._token:
            auth["token"] = self._token
        elif self._password:
            auth["password"] = self._password
        # else: try unauthenticated (works on local loopback with no auth configured)

        params: dict = {
            "minProtocol": 1,
            "maxProtocol": self.PROTOCOL_VERSION,
            "client": {
                "id":          "vibe-cli",
                "displayName": "VibeCLI",
                "version":     "1.0.0",
                "platform":    "darwin",
                "deviceFamily": "mac",
                "mode":        "observer",
                "instanceId":  str(uuid.uuid4()),
            },
            "role":   "operator",
            "scopes": ["sessions", "config"],
        }
        if auth:
            params["auth"] = auth
        if nonce:
            params.setdefault("device", {})["nonce"] = nonce

        req_id = str(uuid.uuid4())
        await ws.send(_json.dumps({"type": "req", "id": req_id, "method": "connect", "params": params}))

        # Step 3: wait for hello-ok (or error res)
        raw = await asyncio.wait_for(ws.recv(), timeout=15)
        msg = _json.loads(raw)
        if msg.get("type") == "hello-ok":
            snap = msg.get("snapshot", {})
            self._emit_status(
                f"Gateway authenticated  ·  protocol {msg.get('protocol', '?')}"
                + (f"  ·  v{msg.get('server', {}).get('version', '')}" if msg.get("server") else "")
            )
            # Emit initial presence
            presence = [
                PresenceEntry(
                    host=p.get("host", "?"),
                    platform=p.get("platform", ""),
                    roles=p.get("roles", []),
                    ts=p.get("ts", 0),
                )
                for p in snap.get("presence", [])
            ]
            if presence and self.on_presence:
                self.on_presence(presence)
        elif msg.get("type") == "res" and not msg.get("ok"):
            err = msg.get("error", {})
            raise RuntimeError(f"Gateway auth failed: {err.get('message', err)}")
        else:
            raise RuntimeError(f"Unexpected handshake response: {msg}")

    # ── subscriptions ─────────────────────────────────────────────────────────

    async def _subscribe(self, ws) -> None:
        """Subscribe to session change events and message streams."""
        await self._send(ws, "sessions.subscribe", {})
        await self._send(ws, "sessions.messages.subscribe", {"sessionKey": "agent:main:main"})

    # ── receive loop ─────────────────────────────────────────────────────────

    async def _receive_loop(self, ws) -> None:
        """Main event loop — dispatch incoming frames until disconnect or stop."""
        async for raw in ws:
            if self._stop.is_set():
                break
            try:
                msg = _json.loads(raw)
            except _json.JSONDecodeError:
                continue

            ftype = msg.get("type")

            if ftype == "res":
                req_id = msg.get("id")
                fut    = self._pending.get(req_id)
                if fut and not fut.done():
                    fut.set_result(msg.get("payload", {}))

            elif ftype == "event":
                self._dispatch_event(msg.get("event", ""), msg.get("payload", {}))

    def _dispatch_event(self, event: str, payload: dict) -> None:
        if event == "tick":
            return  # heartbeat — ignore

        if event == "shutdown":
            reason = payload.get("reason", "")
            self._emit_status(f"Gateway shutting down: {reason}")
            return

        if event == "presence":
            entries = [
                PresenceEntry(
                    host=p.get("host", "?"),
                    platform=p.get("platform", ""),
                    roles=p.get("roles", []),
                    ts=p.get("ts", 0),
                )
                for p in payload.get("presence", [])
            ]
            if self.on_presence:
                self.on_presence(entries)
            return

        if event == "chat":
            self._handle_chat(payload)
            return

        if event == "chat.side_result":
            text = payload.get("text", "").strip()
            if text:
                sk     = payload.get("sessionKey", "")
                parsed = _parse_session_key(sk)
                msg    = ChannelMessage(
                    session_key=sk,
                    channel=parsed["channel"],
                    peer_id=parsed["peer_id"],
                    peer_kind=parsed["peer_kind"],
                    direction="outbound",
                    text=f"[btw] {text}",
                    run_id=payload.get("runId", ""),
                    ts=payload.get("ts", 0),
                )
                if self.on_message:
                    self.on_message(msg)
            return

        if event == "sessions.changed":
            # A new inbound message triggered a session run
            reason = payload.get("reason", "")
            if reason == "send":
                sk      = payload.get("sessionKey", "")
                channel = payload.get("channel", "")
                parsed  = _parse_session_key(sk)
                if not channel:
                    channel = parsed["channel"]
                # We'll get the actual text via `chat` events; emit a status line
                self._emit_status(
                    f"↓ Inbound on [{channel or sk}]  status={payload.get('status','?')}"
                )
            return

        if event == "connect.challenge":
            return  # handled in handshake only

        # node.event — from paired iOS/Android/macOS node
        if event in ("node.event", "node.invoke", "node.invoke.result"):
            dev = DeviceEvent(
                node_id    = payload.get("nodeId", payload.get("id", "?")),
                node_name  = payload.get("name", "node"),
                event_type = event,
                payload    = payload,
                ts         = payload.get("ts", 0),
            )
            if self.on_device_event:
                self.on_device_event(dev)
            return

    def _handle_chat(self, payload: dict) -> None:
        """Buffer chat deltas; emit completed ChannelMessage on final/aborted."""
        state  = payload.get("state", "")
        run_id = payload.get("runId", "")
        sk     = payload.get("sessionKey", "")

        if state == "delta":
            delta = payload.get("data", {}).get("delta", "")
            self._chat_buf.setdefault(run_id, []).append(delta)
            return

        if state in ("final", "aborted", "error"):
            parts  = self._chat_buf.pop(run_id, [])
            text   = payload.get("message", "") or "".join(parts)
            parsed = _parse_session_key(sk)

            if text.strip():
                msg = ChannelMessage(
                    session_key = sk,
                    channel     = parsed["channel"],
                    peer_id     = parsed["peer_id"],
                    peer_kind   = parsed["peer_kind"],
                    direction   = "outbound",
                    text        = text.strip(),
                    run_id      = run_id,
                    ts          = payload.get("usage", {}).get("ts", 0),
                    model       = payload.get("model", ""),
                )
                if self.on_message:
                    self.on_message(msg)
            return

    # ── internal ─────────────────────────────────────────────────────────────

    def _emit_status(self, text: str) -> None:
        if self.on_status:
            self.on_status(text)
        else:
            log.debug("[openclaw-gateway] %s", text)


# ──────────────────────────────────────────────────────────────────────────────
# Auth token discovery
# ──────────────────────────────────────────────────────────────────────────────

def _read_gateway_token() -> str:
    """
    Try to find the gateway auth token from known locations.
    Returns empty string if not found (unauthenticated local connection).
    """
    candidates = [
        os.path.expanduser("~/.openclaw/token"),
        os.path.expanduser("~/.openclaw/.token"),
        os.path.expanduser("~/.openclaw/auth.json"),
    ]
    for path in candidates:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                content = f.read().strip()
            if path.endswith(".json"):
                data = _json.loads(content)
                return str(data.get("token", data.get("deviceToken", "")))
            return content
        except Exception:
            continue
    # Fall back: read from openclaw.json gateway.auth.token
    cfg_path = os.path.expanduser("~/.openclaw/openclaw.json")
    try:
        with open(cfg_path, encoding="utf-8") as f:
            cfg = _json.load(f)
        return str(cfg.get("gateway", {}).get("auth", {}).get("token", ""))
    except Exception:
        return ""


def make_client(host: str = "127.0.0.1", port: int = 18789) -> GatewayClient:
    """Construct a GatewayClient with auto-discovered auth token."""
    token = _read_gateway_token()
    return GatewayClient(host=host, port=port, token=token)
