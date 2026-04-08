# claude/

Claude API integration: CLI subprocess helpers, Anthropic SDK client, prompt suggestion engine, and user profile analysis.

## Files

| File | Purpose |
|---|---|
| `cli_bridge.py` | Sync and async helpers for spawning `claude` CLI subprocesses |
| `sdk_client.py` | Anthropic SDK client used for non-coding LLM calls |
| `suggestion_engine.py` | Ranks and returns prompt suggestions for the prompt bar |
| `profile_analyzer.py` | LLM-based user profile updater and prompt predictor |

## CLI bridge (`cli_bridge.py`)

Thin wrappers around `subprocess` / `asyncio.create_subprocess_exec` for running the Claude Code CLI. Used by `ClaudeSession` in `terminal/claude_session.py`.

Not used for prompt suggestions or profile updates — those go through the Anthropic SDK directly to avoid spawning extra CLI processes.

## SDK client (`sdk_client.py`)

Wraps `anthropic.Anthropic` (or `AsyncAnthropic`) for calls that need raw LLM completions:
- User profile updates after each run
- Next-prompt prediction
- Vault note summarization (future)

Uses `claude-sonnet-4-6` by default (set in `config.json`).

## Suggestion engine (`suggestion_engine.py`)

Returns up to `suggestions_count` (default 4) ranked prompt strings for the `PromptBar`.

Priority order:
1. **Project-specific recent prompts** from the personalization graph (frequency × recency × project affinity)
2. **Built-in prompts** — a static list of broadly applicable tasks (write tests, fix bugs, add types, etc.)
3. **Context-aware prompts** based on the active file extension (`.py` → pytest suggestions, `.ts` → type-check suggestions, etc.)

### Key methods

```python
engine.record(project_name, prompt)      # called on every agent submit
engine.suggest(project_name, active_ext) # returns list[str]
```

`record()` updates both the in-memory recent list and the `PersonalizationGraph`.

## Profile analyzer (`profile_analyzer.py`)

After each successful agent run, `_post_run_hook` calls `ProfileAnalyzer.update_async()`. It:
1. Reads the current user profile from `memory/user_profile.py`
2. Sends the run log (prompt + output tail) to Claude via the SDK
3. Asks Claude to update the profile with new observations (preferred patterns, tools, languages, etc.)
4. Writes the updated profile back to vault

Also exposes `predict_next_prompts(project_name, context)` which returns LLM-generated suggestions blended with graph-based ones.
