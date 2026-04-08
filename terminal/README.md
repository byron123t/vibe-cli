# terminal/

Agent session management and the embedded PTY terminal.

## Files

| File | Purpose |
|---|---|
| `agent_session.py` | Abstract base class all agent sessions share |
| `claude_session.py` | Wraps `claude --print --output-format stream-json` |
| `codex_session.py` | Wraps the OpenAI Codex CLI |
| `cursor_session.py` | Wraps the Cursor CLI |
| `pty_widget.py` | Full VT100 terminal widget (pyte + ptyprocess) |
| `approval_server.py` | Async HTTP server that blocks on `PreToolUse` hook POSTs |

## AgentSession (`agent_session.py`)

Abstract base class. Subclasses must implement:
- `build_command() -> list[str]` — construct the subprocess argv
- `parse_output(line: str)` — handle a line of stdout (update log, detect session ID, etc.)

Common state on every session:
- `session_id` — 8-char UUID prefix, used as widget ID
- `permission_mode` — `"safe"` | `"accept_edits"` | `"bypass"`
- `exit_code` — set when the subprocess exits
- `_output_tail` — last 30 output lines, fed to suggestion engine

## Claude session (`claude_session.py`)

Runs:
```
claude --print --verbose --output-format stream-json [flags] -- "<prompt>"
```

Permission mode mapping:

| Mode | Flag added |
|---|---|
| `safe` | PreToolUse HTTP hook written to `.claude/settings.local.json` |
| `accept_edits` | `--allowedTools edit` |
| `bypass` | `--dangerously-skip-permissions` |

Parses NDJSON stream events to extract text, tool uses, and the Claude session ID (for `--resume`).

## PTY widget (`pty_widget.py`)

Embeds a real VT100 shell using `pyte` + `ptyprocess`.

Key design decisions:
- Extends `Static` (push model via `update()`) to avoid Textual layout feedback loops
- Shell is spawned in `on_resize`, not `on_mount`, so terminal dimensions are already known
- A background reader thread feeds raw PTY bytes into `pyte.ByteStream`
- A 30 fps `set_interval` timer calls `Static.update()` on the main thread with the rendered screen
- `ctrl+t` bubbles up to `VibeCLIApp` which closes the panel

Each project has its own `PTYWidget` instance; they are created lazily and persist for the session.

## Approval server (`approval_server.py`)

A lightweight `asyncio` HTTP server. In **Safe** permission mode, Claude's `PreToolUse` hook POSTs tool details here before executing any tool. The server blocks the response until the TUI user makes a decision.

Flow:
1. Claude hits `POST /pre-tool` with `{"tool": "Write", "path": "src/main.py", ...}`
2. Server creates a `Future` and surfaces a `PermissionPrompt` widget in the TUI
3. User presses `y` / `n` / `Enter` — the TUI resolves the `Future`
4. Server returns `{"decision": "approve"}` or `{"decision": "deny"}`
5. Claude proceeds or skips the tool call

Port is chosen randomly at startup to avoid conflicts when running multiple vibe-cli instances.
