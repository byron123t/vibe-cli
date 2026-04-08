# vibe-cli Architecture

## Overview

vibe-cli is a keyboard-first, modal TUI for managing multiple AI coding agents concurrently. It is built on [Textual](https://textual.textualize.io/) (async Python TUI framework) and wraps the Claude CLI, OpenAI Codex CLI, and Cursor CLI as interchangeable agent backends.

**~5,600 lines of Python** across 6 subsystems.

---

## Directory Layout

```
vibe-cli/
├── main.py                       # Entry point — config loading, CLI args, app startup
├── config.json                   # Runtime configuration (model, vault root, git, UI)
│
├── ui/
│   └── app.py                    # Entire TUI: all widgets + VibeCLIApp controller (2,329 lines)
│
├── core/
│   ├── project_manager.py        # Multi-project CRUD + active-project state
│   └── session_store.py          # Serialize/restore full UI state across restarts
│
├── terminal/
│   ├── agent_session.py          # Abstract base class for all agent types
│   ├── claude_session.py         # `claude --print --output-format stream-json` wrapper
│   ├── codex_session.py          # OpenAI Codex CLI wrapper
│   ├── cursor_session.py         # Cursor CLI wrapper
│   ├── pty_widget.py             # Real PTY terminal (pyte + ptyprocess, 449 lines)
│   ├── approval_server.py        # Async HTTP server for PreToolUse hook approvals
│   └── cli_bridge.py             # Subprocess wrapper with queue-based async streaming
│
├── memory/
│   ├── vault.py                  # On-disk Obsidian-style markdown vault
│   ├── note.py                   # Markdown note with YAML frontmatter + wikilinks
│   ├── moc.py                    # Maps of Content auto-maintenance
│   ├── run_log.py                # Timestamped agent run output logger
│   ├── user_profile.py           # User psychometric profile (read/write)
│   ├── project_registry.py       # Known-projects registry
│   ├── linker.py                 # Bidirectional [[wikilink]] indexer
│   └── linter.py                 # Broken-link + orphan detection
│
├── graph/
│   ├── knowledge_graph.py        # NetworkX DiGraph from vault wikilinks
│   └── personalization_graph.py  # Weighted action-transition graph (recency decay)
│
├── claude/
│   ├── sdk_client.py             # Anthropic SDK client for non-CLI LLM calls
│   ├── suggestion_engine.py      # Ranked prompt suggestion engine
│   ├── profile_analyzer.py       # LLM-driven user profiling + next-prompt prediction
│   └── cli_bridge.py             # Claude CLI subprocess helper
│
└── personalization/
    └── predictor.py              # Wraps PersonalizationGraph for menu/action ranking
```

---

## Subsystems

### 1. UI Layer (`ui/app.py`)

All widgets live in a single file. The `VibeCLIApp` class owns the application lifecycle, project switching, agent spawning, and keybindings.

| Widget | Description |
|--------|-------------|
| `VibeCLIApp` | Root app — coordinates all subsystems, owns async work loops |
| `ProjectTabBar` | Top tab row; `1`–`9` to switch, `+` to add |
| `AgentPanel` | Per-project scrollable stack of `AgentWidget`s |
| `AgentWidget` | One agent session: streaming `RichLog` → `SelectableLog` on complete |
| `AgentMemoryWidget` | Vault notes matching the agent's prompt keywords |
| `PermissionPrompt` | Inline approve/deny widget for PreToolUse hook |
| `TerminalPanel` | Embedded real PTY shell (wraps `PTYWidget`) |
| `FileBrowserPanel` | `DirectoryTree` for the current project |
| `EditorPanel` | Read-only/edit `TextArea` for files; `i` to edit, `s` to save |
| `GraphPane` | NetworkX graph visualization as a navigable `Tree` |
| `PromptBar` | Input field + ranked suggestion row |
| `StatusBar` | Agent type, permission mode, active project |
| `DirectoryPickerScreen` | Modal filesystem navigator for opening projects |

**Key interactions** (Textual Message types):

```
PromptSubmitted     → _on_prompt()         → session created
AgentPanel.AgentComplete → _agent_done()   → post-run hook
PermissionPrompt.Decision → _handle_permission() → ApprovalServer.respond()
FileBrowserPanel.FileSelected → editor update + project.active_file
ProjectTabBar.TabPressed → switch_project()
CommandDetected     → _last_command stored → `r` key reruns it
```

---

### 2. Agent Execution (`terminal/`)

All agents share a common interface (`AgentSession` ABC):

```python
class AgentSession(ABC):
    async def run(on_line, on_permission_request) -> int
    def cancel()
    def approve_permission(decision)
```

Three concrete implementations:

| Session | Command | Output Format | Multi-turn |
|---------|---------|---------------|------------|
| `ClaudeSession` | `claude --print --output-format stream-json` | NDJSON events | `--resume <session_id>` |
| `CodexSession` | `codex -q --approval-mode <mode>` | Plain text | No |
| `CursorSession` | `cursor-agent --output-format stream-json` | NDJSON events | No |

Permission mode maps to CLI flags (Claude only):

| Mode | CLI flags |
|------|-----------|
| `safe` | *(none)* + PreToolUse HTTP hook |
| `accept_edits` | `--permission-mode acceptEdits` |
| `bypass` | `--dangerously-skip-permissions` |

**PTY Widget** (`pty_widget.py`): spawns a real shell via `ptyprocess`, feeds output to a `pyte.ByteStream` screen buffer, and polls at 30 fps with `set_interval` to push rendered output to `Static.update()`.

**ApprovalServer** (`approval_server.py`): `asyncio.start_server` on a random port. Claude Code POSTs tool details to `/pre-tool`; the server blocks until `PermissionPrompt` calls `respond()`.

---

### 3. Memory / Vault (`memory/`)

An Obsidian-compatible markdown vault with auto-maintained structure:

```
vault/
├── user/
│   ├── profile.md               # User psychometric profile
│   ├── projects.json            # ProjectManager persistence
│   ├── session.json             # SessionStore persistence
│   └── personalization_graph.json
├── _MOCs/
│   ├── MOC - Index.md           # Master index (auto-updated)
│   ├── MOC - Projects.md
│   └── MOC - <topic>.md
└── projects/
    └── <project_name>/
        └── run_logs/
            └── YYYY-MM-DDThh-mm-ss_action.md
```

| Module | Responsibility |
|--------|----------------|
| `vault.py` / `note.py` | CRUD for `.md` files; YAML frontmatter + `[[wikilink]]` parsing |
| `moc.py` | Creates and updates Maps of Content (index notes) by tag |
| `run_log.py` | Saves agent outputs as timestamped notes in `projects/<name>/run_logs/` |
| `linker.py` | Builds bidirectional wikilink index (outgoing + incoming per note) |
| `linter.py` | Detects broken links, orphans, stale MOCs, empty notes |
| `user_profile.py` | Reads/writes `vault/user/profile.md` (psychometric + coding style data) |

---

### 4. Graph Layer (`graph/`)

| Module | Graph | Nodes | Edges | Use |
|--------|-------|-------|-------|-----|
| `knowledge_graph.py` | `KnowledgeGraph` | Note titles | Wikilinks | PageRank centrality; shortest path queries; `GraphPane` display |
| `personalization_graph.py` | `PersonalizationGraph` | Action IDs | Transitions | Frequency-weighted; per-project affinity; 7-day recency decay; JSON persistence |

Both use NetworkX DiGraph with version-agnostic wrappers for `node_link_data`/`node_link_graph` (handles 3.0 vs 3.2+ API change).

---

### 5. Claude / LLM Integration (`claude/`)

Separate from agent execution — these modules handle non-session LLM calls:

| Module | Class | Purpose |
|--------|-------|---------|
| `sdk_client.py` | `ClaudeSDKClient` | Anthropic SDK wrapper: `complete()`, `suggest_followup_prompts()`, `suggest_menu_options()` |
| `suggestion_engine.py` | `PromptSuggestionEngine` | Ranks suggestions: graph predictions → recent prompts → file-extension hints → built-in fallbacks |
| `profile_analyzer.py` | `ProfileAnalyzer` | `update_profile()` after each run (LLM analyzes prompting style, tech stack, cognitive patterns); `predict_prompts()` for personalized suggestions |
| `cli_bridge.py` | `ClaudeCLIBridge` | Subprocess helper for sync/async `claude` CLI calls |

---

### 6. Personalization (`personalization/`)

`predictor.py` wraps `PersonalizationGraph` with a simple interface:

```python
predictor.score_action(action_id, project_path) -> float
predictor.rank_actions(candidates, project_path) -> list[str]
predictor.update_menu_weights(selected_action, from_state, project_path)
```

Used for dynamic menu reordering and prompt ranking blending.

---

## Request Lifecycle

End-to-end flow from prompt submission to post-run hooks:

```
User presses Enter (Prompt mode)
        │
        ▼
PromptBar emits PromptSubmitted
        │
        ▼
VibeCLIApp._on_prompt()
  ├─ PersonalizationGraph.record_use()
  ├─ AgentPanel.add_agent()            → new AgentWidget created
  └─ _run_session() [@work async]
       ├─ _make_session()              → ClaudeSession / CodexSession / CursorSession
       ├─ _write_pretooluse_hook()     → if perm_mode == "safe"
       │    └─ writes .claude/settings.local.json in project root
       │
       └─ session.run(on_line=..., on_permission_request=...)
            │
            ├─ [each line] → AgentWidget._full_lines.append()
            │               → RichLog.write() (live streaming)
            │               → CommandDetected if line starts with $
            │
            ├─ [permission request] ─────────────────────────────────┐
            │                       ApprovalServer receives POST      │
            │                       PermissionPrompt shown in TUI     │
            │                       User presses y/n/Enter            │
            │                       ApprovalServer.respond() unblocks ◄┘
            │
            └─ [exit] → _mark_complete(exit_code)
                         ├─ RichLog → SelectableLog switch
                         └─ AgentPanel.AgentComplete posted
                              │
                              ▼
                         _agent_done()
                           ├─ _auto_git_commit()    [thread]
                           └─ _post_run_hook()      [thread]
                                ├─ ProfileAnalyzer.update_profile()
                                ├─ RunLogger.log()
                                ├─ MOCManager.update_*()
                                ├─ VaultLinter.run() → lint_report.md
                                └─ PromptSuggestionEngine → PromptBar.update_suggestions()
```

---

## Async Architecture

- **Textual workers**: `@work(exclusive=False)` on `_run_session`, `_run_reply`, `_auto_git_commit`, `_post_run_hook` — all run as async tasks without blocking the UI
- **Agent I/O**: `AgentSession.run()` is a coroutine; output lines delivered via async callback
- **PTY refresh**: 30 fps `set_interval` polls a dirty flag set by a background reader thread; `Static.update()` is called on the main thread only
- **HTTP approval gate**: `asyncio.start_server`; the PreToolUse handler `await`s a `asyncio.Event` that `PermissionPrompt.Decision` sets
- **Cross-widget messaging**: Textual `Message` subclasses posted up the DOM; handlers on ancestor widgets

---

## Configuration

**`config.json`** (runtime settings):
```json
{
  "claude": {
    "model": "claude-sonnet-4-6",
    "permission_mode": "accept_edits"
  },
  "vault": { "root": "vault" },
  "git": { "auto_commit": true, "commit_message_prefix": "[vibe-cli] " },
  "ui": { "max_agents_per_project": 8, "suggestions_count": 4 }
}
```

**`.claude/settings.local.json`** (auto-written per project in safe mode):
```json
{
  "hooks": {
    "PreToolUse": [{
      "hooks": [{ "type": "http", "url": "http://127.0.0.1:<port>/pre-tool", "timeout": 300 }]
    }]
  }
}
```

---

## External Dependencies

| Library | Role |
|---------|------|
| `textual` | Async TUI framework — all widgets, layout, bindings, messaging |
| `anthropic` | SDK for profile analysis and prompt suggestion LLM calls |
| `networkx` | Knowledge graph + personalization graph construction |
| `pyte` | VT100 screen emulation for PTY widget |
| `ptyprocess` | Spawns a real PTY shell subprocess |
| `pyyaml` | YAML frontmatter parsing in vault notes |
| `rich` | Terminal formatting (RichLog, TextArea, Tree, markup) |
