"""ApprovalServer — async HTTP server for Claude Code PreToolUse hooks.

How it works
------------
Claude Code's PreToolUse hook POSTs tool details here *before* executing any
tool.  The server calls an app-supplied callback (to show a TUI approval
prompt), waits for the user's decision, then returns the appropriate JSON.

Claude Code hook response format:
  {
    "hookSpecificOutput": {
      "hookEventName": "PreToolUse",
      "permissionDecision": "allow"   # or "deny"
      "permissionDecisionReason": "…" # optional, only on deny
    }
  }

Hook config to write into .claude/settings.local.json for a project:
  {
    "hooks": {
      "PreToolUse": [
        {
          "hooks": [
            {"type": "http", "url": "http://127.0.0.1:<port>/pre-tool", "timeout": 300}
          ]
        }
      ]
    }
  }
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Callable, Optional


class ApprovalServer:
    """
    Minimal async HTTP server that handles Claude Code PreToolUse callbacks.

    The server binds to 127.0.0.1 on an OS-assigned port. Use `port` after
    calling `start()` to get the bound port for the hook URL.

    on_request(request_id, payload) is called on the Textual event loop when
    a PreToolUse POST arrives. The callback should surface a TUI prompt and
    eventually call server.respond(request_id, allow).
    """

    def __init__(self, on_request: Callable[[str, dict], None]) -> None:
        self._on_request = on_request
        self._port: int = 0
        self._server: Optional[asyncio.AbstractServer] = None
        self._pending:   dict[str, asyncio.Event] = {}
        self._decisions: dict[str, dict]          = {}

    @property
    def port(self) -> int:
        return self._port

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, "127.0.0.1", 0
        )
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    def respond(self, request_id: str, allow: bool, reason: str = "") -> None:
        """
        Call this from the main thread when the user makes a decision.
        Unblocks the waiting HTTP handler coroutine.
        """
        body: dict = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow" if allow else "deny",
            }
        }
        if not allow and reason:
            body["hookSpecificOutput"]["permissionDecisionReason"] = reason
        self._decisions[request_id] = body
        ev = self._pending.get(request_id)
        if ev:
            ev.set()

    # ------------------------------------------------------------------ internals

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            payload = await self._parse_post(reader)
        except Exception as exc:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        request_id = str(uuid.uuid4())
        event      = asyncio.Event()
        self._pending[request_id] = event

        try:
            self._on_request(request_id, payload)
        except Exception:
            # TUI unavailable — auto-deny so Claude doesn't hang forever
            self.respond(request_id, allow=False, reason="Approval UI unavailable")

        try:
            await asyncio.wait_for(event.wait(), timeout=300)
        except asyncio.TimeoutError:
            self.respond(request_id, allow=False, reason="Approval timed out (300 s)")

        decision = self._decisions.pop(request_id, {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": "No decision recorded",
            }
        })
        self._pending.pop(request_id, None)

        body_bytes = json.dumps(decision).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\n"
            b"Content-Type: application/json\r\n"
            + f"Content-Length: {len(body_bytes)}\r\n\r\n".encode()
            + body_bytes
        )
        await writer.drain()
        writer.close()

    @staticmethod
    async def _parse_post(reader: asyncio.StreamReader) -> dict:
        """Parse a minimal HTTP POST and return the JSON body as a dict."""
        # Request line (e.g. POST /pre-tool HTTP/1.1)
        await reader.readline()
        # Headers
        headers: dict[str, str] = {}
        while True:
            raw = await reader.readline()
            line = raw.decode(errors="replace").strip()
            if not line:
                break
            if ":" in line:
                k, _, v = line.partition(":")
                headers[k.strip().lower()] = v.strip()
        # Body
        length = int(headers.get("content-length", "0"))
        body   = await reader.readexactly(length)
        return json.loads(body)
