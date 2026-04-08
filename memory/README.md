# memory/

Obsidian-style markdown vault: every agent run is saved as a timestamped note, notes are wikilinked, and a knowledge graph is built from the links.

## Files

| File | Purpose |
|---|---|
| `vault.py` | CRUD + search for `.md` notes on disk |
| `note.py` | `Note` dataclass — YAML frontmatter + body + parsed links |
| `moc.py` | Auto-maintained Maps of Content (index notes) |
| `linker.py` | `[[wikilink]]` parser and link graph |
| `linter.py` | Broken link and orphan note detection |
| `run_log.py` | Saves agent run outputs as timestamped vault notes |
| `user_profile.py` | Read/write the user's psychometric/preference profile |
| `project_registry.py` | Persists the list of known projects |

## Vault layout

```
vault/
├── _MOCs/           # Maps of Content (auto-generated index notes)
├── projects/        # Per-project run logs
├── user/            # User profile and preferences
└── meta/
    └── lint_report.md   # Written by the linter after each run
```

## Note format

Every note is a markdown file with YAML frontmatter:

```markdown
---
title: Fix auth bug in myapp
type: run_log
tags: [myapp, bugfix]
created: 2026-04-07T14:23:00
project: myapp
---

Prompt: Fix the auth token expiry bug

## Output

...agent output...

## Related
- [[User Profile]]
- [[myapp MOC]]
```

## MemoryVault (`vault.py`)

The central CRUD layer. Main methods:

| Method | Description |
|---|---|
| `get_note(rel_path)` | Load a note by relative path |
| `create_note(rel_path, title, body, ...)` | Create and persist a new note |
| `save_note(note)` | Write an already-loaded `Note` back to disk |
| `all_notes()` | Iterate over every note in the vault |
| `search(query)` | Full-text search across note bodies |

## Run log (`run_log.py`)

Called by `_post_run_hook` in the TUI after each successful agent run. Creates a note under `vault/projects/<project_name>/YYYY-MM-DD_HH-MM-SS.md` containing the prompt, agent output tail, and session metadata.

## Linter (`linter.py`)

Detects:
- **Broken links** — `[[target]]` with no matching note title
- **Orphan notes** — notes with no incoming or outgoing links
- **Stale MOCs** — index notes that reference deleted notes

Results are written to `vault/meta/lint_report.md` after every agent run.

## MOC maintenance (`moc.py`)

Maps of Content are auto-generated index notes, one per project and one per tag. `MOCManager.update()` is called after each run log write; it adds new `[[wikilinks]]` to the relevant MOC notes.
