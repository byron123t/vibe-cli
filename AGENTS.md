# vibe-cli — Keyboard-First Multi-Project AI Coding Terminal

## What It Is

vibe-cli is a modal, keyboard-first terminal UI for managing multiple coding projects with concurrent AI coding agents. Think VS Code + Obsidian, running entirely in the terminal. Built for ADHD brains with many projects open at once.

**Core loop**: switch project → type a prompt → watch the agent work → auto-commit → repeat.

---

## Architecture

```
vibe-cli/
├── main.py                      # Entry point (--add flag for initial projects)
├── config.json                  # Model, vault root, git, UI settings
├── requirements.txt
│
├── ui/
│   └── app.py                   # Entire TUI — all widgets and the App class
│
├── core/
│   └── project_manager.py       # Project list, persistence, active file tracking
│
├── terminal/
│   ├── agent_session.py         # AgentSession base class
│   ├── claude_session.py        # `claude --print --output-format stream-json` wrapper
│   ├── codex_session.py         # OpenAI Codex CLI wrapper
│   ├── cursor_session.py        # Cursor CLI wrapper
│   ├── pty_widget.py            # Real PTY terminal widget (pyte + ptyprocess)
│   └── approval_server.py       # Async HTTP server for PreToolUse hook approvals
│
├── claude/
│   ├── cli_bridge.py            # Sync/async Claude CLI subprocess helpers
│   ├── sdk_client.py            # Anthropic SDK client (suggestions, profile updates)
│   ├── suggestion_engine.py     # Ranked prompt suggestions via personalization graph
│   └── profile_analyzer.py      # LLM-based user profile updater + prompt predictor
│
├── memory/
│   ├── vault.py                 # Read/write/search .md notes
│   ├── note.py                  # Note dataclass (frontmatter + body)
│   ├── moc.py                   # Maps of Content auto-maintenance
│   ├── linker.py                # [[wikilink]] parser + link graph
│   ├── linter.py                # Broken link + orphan detection
│   ├── run_log.py               # Save agent run outputs as timestamped notes
│   ├── user_profile.py          # Read/write user psychometric profile
│   └── project_registry.py      # Known projects registry
│
├── graph/
│   ├── knowledge_graph.py       # networkx DiGraph from vault wikilinks
│   └── personalization_graph.py # Weighted action-transition graph for predictions
│
├── personalization/
│   └── predictor.py             # Ranks actions by frequency × recency × project affinity
│
├── tests/
│   ├── conftest.py
│   ├── test_project_manager.py
│   ├── test_suggestion_engine.py
│   ├── test_claude_session.py
│   └── test_app_widgets.py
│
└── unused/                      # Dead code (gesture/voice/pygame) — safe to delete
```

---

## UI Widgets (ui/app.py)

| Widget | ID | Description |
|---|---|---|
| `ProjectTabBar` | `#tab-bar` | Top row of project tabs |
| `StatusBar` | `#status-bar` | Agent type + permission mode indicator |
| `FileBrowserPanel` | `#file-browser` | DirectoryTree for current project (toggle: `f`) |
| `EditorPanel` | `#editor-panel` | Read-only file viewer; `i` enters edit mode |
| `AgentPanel` | `#agent-panel` | Per-project stacks of `AgentWidget`s |
| `AgentWidget` | `#agent-{id}` | Single agent session: streamed log + status + memory |
| `AgentMemoryWidget` | `#agent-mem-{id}` | Vault notes related to the agent's prompt |
| `PermissionPrompt` | (inline) | Keyboard-navigable tool approval prompt |
| `TerminalPanel` | `#terminal-panel` | Per-project real PTY terminal (toggle: `t`) |
| `GraphPane` | `#graph-pane` | Memory/knowledge graph as navigable Tree (toggle: `m`) |
| `PromptBar` | `#prompt-bar` | Suggestion row + prompt input |
| `DirectoryPickerScreen` | (modal) | Filesystem navigator for opening projects |

---

## Interaction Model

vibe-cli is **modal** — like vim. Default is **command mode** where single keypresses trigger actions. Typing only happens in prompt or edit mode.

### Modes

| Mode | How to enter | How to exit |
|---|---|---|
| **Command** | Default / Backspace / `,` | — |
| **Prompt** | `n` or `Enter` | `Escape`, `Backspace`, or submit with `Enter` |
| **Edit** | `i` (editor must be visible) | `Escape` (auto-saves) |
| **Terminal** | `t` | `Escape`, `Backspace`, or `ctrl+t` |

### Command Mode Keys

| Key | Action |
|---|---|
| `]` / `[` | Next / prev project |
| `1`–`9` | Jump to project by number |
| `n` or `Enter` | Open prompt (new agent) |
| `x` | Cancel last running agent |
| `d` | Dismiss last agent widget |
| `j` / `k` | Scroll agent list down / up |
| `f` | Toggle file browser |
| `e` | Toggle editor panel |
| `i` | Enter file edit mode |
| `m` | Toggle memory / knowledge graph |
| `t` | Toggle embedded terminal |
| `r` | Run last shell command from agent output |
| `s` | Save file (edit mode only) |
| `a` / `A` | Cycle agent type (Claude → Codex → Cursor) |
| `P` | Cycle permission mode (Safe → Accept Edits → Bypass) |
| `o` | Open project (directory picker modal) |
| `Backspace` / `,` | Back to command mode |
| `q` | Quit |

---

## Key Features

### Per-Project Isolation

Switching projects with `]`/`[` does **not** cancel running agents. Each project has its own:
- `ScrollableContainer` of `AgentWidget`s inside `AgentPanel`
- `PTYWidget` instance inside `TerminalPanel`
- Editor file state

Containers are created lazily on first switch and shown/hidden by `switch_project()`.

### Agent Types

Cycle with `A`. Supported:
- **Claude** — `claude --print --verbose --output-format stream-json`
- **Codex** — OpenAI Codex CLI
- **Cursor** — Cursor CLI

### Permission System (Safe Mode)

In **Safe** mode, vibe-cli writes a `PreToolUse` HTTP hook to `.claude/settings.local.json` before each agent run. Claude pauses before every tool call and POSTs tool details to `ApprovalServer` (port chosen at startup). The server blocks until the TUI user responds.

`PermissionPrompt` appears inline on the running `AgentWidget`:
```
 Claude wants to use: Write
 src/main.py

 ❯ Approve      Approve+Remember      Deny
 ◄/► select  Enter=confirm  y=approve  n=deny
```

- **◄/►** or **h/l** moves selection
- **Enter** confirms; **y** approves instantly; **n** denies instantly
- **Approve+Remember** writes the tool to `settings.local.json` `permissions.allow`

Hook config written by `_write_pretooluse_hook()`:
```json
{
  "hooks": {
    "PreToolUse": [{
      "hooks": [{"type": "http", "url": "http://127.0.0.1:<port>/pre-tool", "timeout": 300}]
    }]
  }
}
```

### Real PTY Terminal (`terminal/pty_widget.py`)

`PTYWidget` embeds a full VT100 terminal using `pyte` + `ptyprocess`:
- Extends `Static` (push model via `update()`) — avoids Textual layout feedback loops
- Shell is spawned on first `on_resize` (not `on_mount`) so dimensions are known
- Background reader thread feeds PTY output to `pyte.ByteStream`; a 30 fps `set_interval` poller calls `Static.update()` on the main thread
- `ctrl+t` bubbles to the App's binding to close the panel

### Post-Run Hook (`_post_run_hook` in `VibeCLIApp`)

Runs in a background thread after every successful agent completion:
1. Record prompt in personalization graph
2. Log run to vault as a timestamped `.md` note
3. Update MOC index
4. Run vault linter; write issues to `vault/meta/lint_report.md`
5. Update user psychometric profile via LLM (Claude SDK)
6. Generate personalized next-prompt predictions blended with graph-based suggestions
7. Push updated suggestions to `PromptBar`

### NetworkX Compatibility

`personalization_graph.py` uses version-agnostic wrappers for `node_link_data`/`node_link_graph` that handle the API change between NetworkX 3.0 (`link=`) and 3.2+ (`edges=`).

---

## Configuration (`config.json`)

```json
{
  "claude": {
    "model": "claude-sonnet-4-6",
    "permission_mode": "accept_edits"
  },
  "vault": {
    "root": "vault"
  },
  "git": {
    "auto_commit": true,
    "commit_message_prefix": "[vibe-cli] "
  },
  "ui": {
    "max_agents_per_project": 8,
    "suggestions_count": 4
  }
}
```

---

## Running

```bash
pip install -r requirements.txt
python main.py --add ~/code/myproject
```

### Tests

```bash
pip install pytest pytest-asyncio
python -m pytest tests/ -v
```

---

## Textual Framework Reference

The TUI is built with the [Textual](https://textual.textualize.io/) framework. When working on any frontend/UI code in this project (`ui/app.py`, widgets, screens, layouts, styling, events, reactivity), **refer to the Textual documentation links in `textual_documentation/documentation_links.txt`** before making changes.

Key topics and their URLs (from `textual_documentation/documentation_links.txt`):

| Topic | URL |
|---|---|
| App | https://textual.textualize.io/guide/app/ |
| Widgets | https://textual.textualize.io/guide/widgets/ |
| Events | https://textual.textualize.io/guide/events/ |
| Layout | https://textual.textualize.io/guide/layout/ |
| Styles | https://textual.textualize.io/guide/styles/ |
| Queries | https://textual.textualize.io/guide/queries/ |
| Actions | https://textual.textualize.io/guide/actions/ |
| Reactivity | https://textual.textualize.io/guide/reactivity/ |
| Design | https://textual.textualize.io/guide/design/ |
| Content | https://textual.textualize.io/guide/content/ |
| Animation | https://textual.textualize.io/guide/animation/ |
| Screens | https://textual.textualize.io/guide/screens/ |
| Workers | https://textual.textualize.io/guide/workers/ |
| Command Palette | https://textual.textualize.io/guide/command_palette/ |
| Testing | https://textual.textualize.io/guide/testing/ |
| DevTools | https://textual.textualize.io/guide/devtools/ |

---

## CLI Reference Documentation

Full documentation for the supported coding agent CLIs is in `coding_agent_documentation/`:

| File(s) | Agent |
|---|---|
| `cursor.md` | Cursor `agent` CLI |
| `codex.md` | OpenAI Codex CLI |
| `claude_cli.md`, `claude_cmds.md`, `claude_env.md`, `claude_hook.md`, `claude_inter.md`, `claude_mcp.md`, `claude_plug.md`, `claude_tool.md`, `claude_ckpt.md` | Claude Code CLI |

---

## Dead Code

`unused/` contains the original gesture/voice/camera implementation. Not imported anywhere — safe to delete.
