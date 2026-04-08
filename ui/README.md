# ui/

The entire TUI lives in a single file: `app.py`. It is a [Textual](https://github.com/Textualize/textual) application.

## Layout

```
┌─────────────────────────────────────────────────────┐
│  ProjectTabBar  (#tab-bar)                          │
│  StatusBar      (#status-bar)                       │
├────────────┬──────────────────────────┬─────────────┤
│ FileBrowser│   AgentPanel             │ GraphPane   │
│ (#file-    │   (#agent-panel)         │ (#graph-    │
│  browser)  │   └── AgentWidget×N      │  pane)      │
│            ├──────────────────────────┤             │
│            │   EditorPanel            │             │
│            │   (#editor-panel)        │             │
├────────────┴──────────────────────────┴─────────────┤
│  TerminalPanel  (#terminal-panel)                   │
├─────────────────────────────────────────────────────┤
│  PromptBar  (#prompt-bar)                           │
└─────────────────────────────────────────────────────┘
```

## Widgets

| Widget | CSS ID | Purpose |
|---|---|---|
| `ProjectTabBar` | `#tab-bar` | Row of project tabs at the top |
| `StatusBar` | `#status-bar` | Shows current agent type and permission mode |
| `FileBrowserPanel` | `#file-browser` | `DirectoryTree` for the active project; toggle with `f` |
| `EditorPanel` | `#editor-panel` | Read-only file viewer; `i` enters edit mode |
| `AgentPanel` | `#agent-panel` | Scrollable stack of `AgentWidget`s, one per launched agent |
| `AgentWidget` | `#agent-{id}` | Streamed agent log, status badge, and related memory notes |
| `AgentMemoryWidget` | `#agent-mem-{id}` | Vault notes related to that agent's prompt |
| `PermissionPrompt` | (inline) | Keyboard-navigable tool approval — appears inside an `AgentWidget` |
| `TerminalPanel` | `#terminal-panel` | Full PTY shell per project; toggle with `t` |
| `GraphPane` | `#graph-pane` | Memory/knowledge graph as a navigable `Tree`; toggle with `m` |
| `PromptBar` | `#prompt-bar` | Suggestion pills + text input |
| `DirectoryPickerScreen` | (modal) | Filesystem navigator for opening a new project |

## Per-project isolation

`switch_project()` in `VibeCLIApp` shows/hides containers rather than destroying and recreating them. Each project has its own:
- `ScrollableContainer` of `AgentWidget`s mounted inside `AgentPanel`
- `PTYWidget` inside `TerminalPanel`
- Editor file state

Containers are created lazily on first project switch so startup is fast.

## Modes

The app is modal. The active mode is tracked in `VibeCLIApp.mode`.

| Mode | Enter | Exit |
|---|---|---|
| `command` | Default / `Backspace` / `,` | — |
| `prompt` | `n` or `Enter` | `Escape`, `Backspace`, or submit with `Enter` |
| `edit` | `i` (editor visible) | `Escape` (auto-saves) |
| `terminal` | `t` | `Escape`, `Backspace`, or `ctrl+t` |

## Post-run hook

After every successful agent completion `_post_run_hook` runs in a background thread:
1. Record prompt in the personalization graph
2. Save run to vault as a timestamped `.md` note
3. Update MOC index
4. Run vault linter
5. Update user profile via LLM
6. Generate new prompt suggestions
7. Push suggestions to `PromptBar`
