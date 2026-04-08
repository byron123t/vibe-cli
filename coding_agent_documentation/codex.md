# Codex CLI features

Codex supports workflows beyond chat. Use this guide to learn what each one unlocks and when to use it.

## Running in interactive mode

Codex launches into a full-screen terminal UI that can read your repository, make edits, and run commands as you iterate together. Use it whenever you want a conversational workflow where you can review Codex's actions in real time.

```bash
codex
```

You can also specify an initial prompt on the command line.

```bash
codex "Explain this codebase to me"
```

Once the session is open, you can:

- Send prompts, code snippets, or screenshots (see [image inputs](#image-inputs)) directly into the composer.
- Watch Codex explain its plan before making a change, and approve or reject steps inline.
- Read syntax-highlighted markdown code blocks and diffs in the TUI, then use `/theme` to preview and save a preferred theme.
- Use `/clear` to wipe the terminal and start a fresh chat, or press <kbd>Ctrl</kbd>+<kbd>L</kbd> to clear the screen without starting a new conversation.
- Use `/copy` to copy the latest completed Codex output. If a turn is still running, Codex copies the most recent finished output instead of in-progress text.
- Navigate draft history in the composer with <kbd>Up</kbd>/<kbd>Down</kbd>; Codex restores prior draft text and image placeholders.
- Press <kbd>Ctrl</kbd>+<kbd>C</kbd> or use `/exit` to close the interactive session when you're done.

## Resuming conversations

Codex stores your transcripts locally so you can pick up where you left off instead of repeating context. Use the `resume` subcommand when you want to reopen an earlier thread with the same repository state and instructions.

- `codex resume` launches a picker of recent interactive sessions. Highlight a run to see its summary and press <kbd>Enter</kbd> to reopen it.
- `codex resume --all` shows sessions beyond the current working directory, so you can reopen any local run.
- `codex resume --last` skips the picker and jumps straight to your most recent session from the current working directory (add `--all` to ignore the current working directory filter).
- `codex resume <SESSION_ID>` targets a specific run. You can copy the ID from the picker, `/status`, or the files under `~/.codex/sessions/`.

Non-interactive automation runs can resume too:

```bash
codex exec resume --last "Fix the race conditions you found"
codex exec resume 7f9f9a2e-1b3c-4c7a-9b0e-.... "Implement the plan"
```

Each resumed run keeps the original transcript, plan history, and approvals, so Codex can use prior context while you supply new instructions. Override the working directory with `--cd` or add extra roots with `--add-dir` if you need to steer the environment before resuming.

## Models and reasoning

For most tasks in Codex, `gpt-5.4` is the recommended model. It brings the
industry-leading coding capabilities of `gpt-5.3-codex` to OpenAI's flagship
frontier model, combining frontier coding performance with stronger reasoning,
native computer use, and broader professional workflows. For extra fast tasks,
ChatGPT Pro subscribers have access to the GPT-5.3-Codex-Spark model in
research preview.

Switch models mid-session with the `/model` command, or specify one when launching the CLI.

```bash
codex --model gpt-5.4
```

[Learn more about the models available in Codex](https://developers.openai.com/codex/models).

## Feature flags

Codex includes a small set of feature flags. Use the `features` subcommand to inspect what's available and to persist changes in your configuration.

```bash
codex features list
codex features enable unified_exec
codex features disable shell_snapshot
```

`codex features enable <feature>` and `codex features disable <feature>` write to `~/.codex/config.toml`. If you launch Codex with `--profile`, Codex stores the change in that profile rather than the root configuration.

## Subagents

Use Codex subagent workflows to parallelize larger tasks. For setup, role configuration (`[agents]` in `config.toml`), and examples, see [Subagents](https://developers.openai.com/codex/subagents).

Codex only spawns subagents when you explicitly ask it to. Because each
subagent does its own model and tool work, subagent workflows consume more
tokens than comparable single-agent runs.

## Image inputs

Attach screenshots or design specs so Codex can read image details alongside your prompt. You can paste images into the interactive composer or provide files on the command line.

```bash
codex -i screenshot.png "Explain this error"
```

```bash
codex --image img1.png,img2.jpg "Summarize these diagrams"
```

Codex accepts common formats such as PNG and JPEG. Use comma-separated filenames for two or more images, and combine them with text instructions to add context.

## Syntax highlighting and themes

The TUI syntax-highlights fenced markdown code blocks and file diffs so code is easier to scan during reviews and debugging.

Use `/theme` to open the theme picker, preview themes live, and save your selection to `tui.theme` in `~/.codex/config.toml`. You can also add custom `.tmTheme` files under `$CODEX_HOME/themes` and select them in the picker.

## Running local code review

Type `/review` in the CLI to open Codex's review presets. The CLI launches a dedicated reviewer that reads the diff you select and reports prioritized, actionable findings without touching your working tree. By default it uses the current session model; set `review_model` in `config.toml` to override.

- **Review against a base branch** lets you pick a local branch; Codex finds the merge base against its upstream, diffs your work, and highlights the biggest risks before you open a pull request.
- **Review uncommitted changes** inspects everything that's staged, not staged, or not tracked so you can address issues before committing.
- **Review a commit** lists recent commits and has Codex read the exact change set for the SHA you choose.
- **Custom review instructions** accepts your own wording (for example, "Focus on accessibility regressions") and runs the same reviewer with that prompt.

Each run shows up as its own turn in the transcript, so you can rerun reviews as the code evolves and compare the feedback.

## Web search

Codex ships with a first-party web search tool. For local tasks in the Codex CLI, Codex enables web search by default and serves results from a web search cache. The cache is an OpenAI-maintained index of web results, so cached mode returns pre-indexed results instead of fetching live pages. This reduces exposure to prompt injection from arbitrary live content, but you should still treat web results as untrusted. If you are using `--yolo` or another [full access sandbox setting](https://developers.openai.com/codex/agent-approvals-security), web search defaults to live results. To fetch the most recent data, pass `--search` for a single run or set `web_search = "live"` in [Config basics](https://developers.openai.com/codex/config-basic). You can also set `web_search = "disabled"` to turn the tool off.

You'll see `web_search` items in the transcript or `codex exec --json` output whenever Codex looks something up.

## Running with an input prompt

When you just need a quick answer, run Codex with a single prompt and skip the interactive UI.

```bash
codex "explain this codebase"
```

Codex will read the working directory, craft a plan, and stream the response back to your terminal before exiting. Pair this with flags like `--path` to target a specific directory or `--model` to dial in the behavior up front.

## Shell completions

Speed up everyday usage by installing the generated completion scripts for your shell:

```bash
codex completion bash
codex completion zsh
codex completion fish
```

Run the completion script in your shell configuration file to set up completions for new sessions. For example, if you use `zsh`, you can add the following to the end of your `~/.zshrc` file:

```bash
# ~/.zshrc
eval "$(codex completion zsh)"
```

Start a new session, type `codex`, and press <kbd>Tab</kbd> to see the completions. If you see a `command not found: compdef` error, add `autoload -Uz compinit && compinit` to your `~/.zshrc` file before the `eval "$(codex completion zsh)"` line, then restart your shell.

## Approval modes

Approval modes define how much Codex can do without stopping for confirmation. Use `/permissions` inside an interactive session to switch modes as your comfort level changes.

- **Auto** (default) lets Codex read files, edit, and run commands within the working directory. It still asks before touching anything outside that scope or using the network.
- **Read-only** keeps Codex in a consultative mode. It can browse files but won't make changes or run commands until you approve a plan.
- **Full Access** grants Codex the ability to work across your machine, including network access, without asking. Use it sparingly and only when you trust the repository and task.

Codex always surfaces a transcript of its actions, so you can review or roll back changes with your usual git workflow.

## Scripting Codex

Automate workflows or wire Codex into your existing scripts with the `exec` subcommand. This runs Codex non-interactively, piping the final plan and results back to `stdout`.

```bash
codex exec "fix the CI failure"
```

Combine `exec` with shell scripting to build custom workflows, such as automatically updating changelogs, sorting issues, or enforcing editorial checks before a PR ships.

## Working with Codex cloud

The `codex cloud` command lets you triage and launch [Codex cloud tasks](https://developers.openai.com/codex/cloud) without leaving the terminal. Run it with no arguments to open an interactive picker, browse active or finished tasks, and apply the changes to your local project.

You can also start a task directly from the terminal:

```bash
codex cloud exec --env ENV_ID "Summarize open bugs"
```

Add `--attempts` (1–4) to request best-of-N runs when you want Codex cloud to generate more than one solution. For example, `codex cloud exec --env ENV_ID --attempts 3 "Summarize open bugs"`.

Environment IDs come from your Codex cloud configuration—use `codex cloud` and press <kbd>Ctrl</kbd>+<kbd>O</kbd> to choose an environment or the web dashboard to confirm the exact value. Authentication follows your existing CLI login, and the command exits non-zero if submission fails so you can wire it into scripts or CI.

## Slash commands

Slash commands give you quick access to specialized workflows like `/review`, `/fork`, or your own reusable prompts. Codex ships with a curated set of built-ins, and you can create custom ones for team-specific tasks or personal shortcuts.

See the [slash commands guide](https://developers.openai.com/codex/guides/slash-commands) to browse the catalog of built-ins, learn how to author custom commands, and understand where they live on disk.

## Prompt editor

When you're drafting a longer prompt, it can be easier to switch to a full editor and then send the result back to the composer.

In the prompt input, press <kbd>Ctrl</kbd>+<kbd>G</kbd> to open the editor defined by the `VISUAL` environment variable (or `EDITOR` if `VISUAL` isn't set).

## Model Context Protocol (MCP)

Connect Codex to more tools by configuring Model Context Protocol servers. Add STDIO or streaming HTTP servers in `~/.codex/config.toml`, or manage them with the `codex mcp` CLI commands—Codex launches them automatically when a session starts and exposes their tools next to the built-ins. You can even run Codex itself as an MCP server when you need it inside another agent.

See [Model Context Protocol](https://developers.openai.com/codex/mcp) for example configurations, supported auth flows, and a more detailed guide.

## Tips and shortcuts

- Type `@` in the composer to open a fuzzy file search over the workspace root; press <kbd>Tab</kbd> or <kbd>Enter</kbd> to drop the highlighted path into your message.
- Press <kbd>Enter</kbd> while Codex is running to inject new instructions into the current turn, or press <kbd>Tab</kbd> to queue a follow-up prompt for the next turn.
- Prefix a line with `!` to run a local shell command (for example, `!ls`). Codex treats the output like a user-provided command result and still applies your approval and sandbox settings.
- Tap <kbd>Esc</kbd> twice while the composer is empty to edit your previous user message. Continue pressing <kbd>Esc</kbd> to walk further back in the transcript, then hit <kbd>Enter</kbd> to fork from that point.
- Launch Codex from any directory using `codex --cd <path>` to set the working root without running `cd` first. The active path appears in the TUI header.
- Expose more writable roots with `--add-dir` (for example, `codex --cd apps/frontend --add-dir ../backend --add-dir ../shared`) when you need to coordinate changes across more than one project.
- Make sure your environment is already set up before launching Codex so it doesn't spend tokens probing what to activate. For example, source your Python virtual environment (or other language environments), start any required daemons, and export the environment variables you expect to use ahead of time.





# Command line options

export const globalFlagOptions = [
  {
    key: "PROMPT",
    type: "string",
    description:
      "Optional text instruction to start the session. Omit to launch the TUI without a pre-filled message.",
  },
  {
    key: "--image, -i",
    type: "path[,path...]",
    description:
      "Attach one or more image files to the initial prompt. Separate multiple paths with commas or repeat the flag.",
  },
  {
    key: "--model, -m",
    type: "string",
    description:
      "Override the model set in configuration (for example `gpt-5.4`).",
  },
  {
    key: "--oss",
    type: "boolean",
    defaultValue: "false",
    description:
      'Use the local open source model provider (equivalent to `-c model_provider="oss"`). Validates that Ollama is running.',
  },
  {
    key: "--profile, -p",
    type: "string",
    description:
      "Configuration profile name to load from `~/.codex/config.toml`.",
  },
  {
    key: "--sandbox, -s",
    type: "read-only | workspace-write | danger-full-access",
    description:
      "Select the sandbox policy for model-generated shell commands.",
  },
  {
    key: "--ask-for-approval, -a",
    type: "untrusted | on-request | never",
    description:
      "Control when Codex pauses for human approval before running a command. `on-failure` is deprecated; prefer `on-request` for interactive runs or `never` for non-interactive runs.",
  },
  {
    key: "--full-auto",
    type: "boolean",
    defaultValue: "false",
    description:
      "Shortcut for low-friction local work: sets `--ask-for-approval on-request` and `--sandbox workspace-write`.",
  },
  {
    key: "--dangerously-bypass-approvals-and-sandbox, --yolo",
    type: "boolean",
    defaultValue: "false",
    description:
      "Run every command without approvals or sandboxing. Only use inside an externally hardened environment.",
  },
  {
    key: "--cd, -C",
    type: "path",
    description:
      "Set the working directory for the agent before it starts processing your request.",
  },
  {
    key: "--search",
    type: "boolean",
    defaultValue: "false",
    description:
      'Enable live web search (sets `web_search = "live"` instead of the default `"cached"`).',
  },
  {
    key: "--add-dir",
    type: "path",
    description:
      "Grant additional directories write access alongside the main workspace. Repeat for multiple paths.",
  },
  {
    key: "--no-alt-screen",
    type: "boolean",
    defaultValue: "false",
    description:
      "Disable alternate screen mode for the TUI (overrides `tui.alternate_screen` for this run).",
  },
  {
    key: "--enable",
    type: "feature",
    description:
      "Force-enable a feature flag (translates to `-c features.<name>=true`). Repeatable.",
  },
  {
    key: "--disable",
    type: "feature",
    description:
      "Force-disable a feature flag (translates to `-c features.<name>=false`). Repeatable.",
  },
  {
    key: "--config, -c",
    type: "key=value",
    description:
      "Override configuration values. Values parse as JSON if possible; otherwise the literal string is used.",
  },
];

export const commandOverview = [
  {
    key: "codex",
    href: "/codex/cli/reference#codex-interactive",
    type: "stable",
    description:
      "Launch the terminal UI. Accepts the global flags above plus an optional prompt or image attachments.",
  },
  {
    key: "codex app-server",
    href: "/codex/cli/reference#codex-app-server",
    type: "experimental",
    description:
      "Launch the Codex app server for local development or debugging.",
  },
  {
    key: "codex app",
    href: "/codex/cli/reference#codex-app",
    type: "stable",
    description:
      "Launch the Codex desktop app on macOS, optionally opening a specific workspace path.",
  },
  {
    key: "codex debug app-server send-message-v2",
    href: "/codex/cli/reference#codex-debug-app-server-send-message-v2",
    type: "experimental",
    description:
      "Debug app-server by sending a single V2 message through the built-in test client.",
  },
  {
    key: "codex apply",
    href: "/codex/cli/reference#codex-apply",
    type: "stable",
    description:
      "Apply the latest diff generated by a Codex Cloud task to your local working tree. Alias: `codex a`.",
  },
  {
    key: "codex cloud",
    href: "/codex/cli/reference#codex-cloud",
    type: "experimental",
    description:
      "Browse or execute Codex Cloud tasks from the terminal without opening the TUI. Alias: `codex cloud-tasks`.",
  },
  {
    key: "codex completion",
    href: "/codex/cli/reference#codex-completion",
    type: "stable",
    description:
      "Generate shell completion scripts for Bash, Zsh, Fish, or PowerShell.",
  },
  {
    key: "codex features",
    href: "/codex/cli/reference#codex-features",
    type: "stable",
    description:
      "List feature flags and persistently enable or disable them in `config.toml`.",
  },
  {
    key: "codex exec",
    href: "/codex/cli/reference#codex-exec",
    type: "stable",
    description:
      "Run Codex non-interactively. Alias: `codex e`. Stream results to stdout or JSONL and optionally resume previous sessions.",
  },
  {
    key: "codex execpolicy",
    href: "/codex/cli/reference#codex-execpolicy",
    type: "experimental",
    description:
      "Evaluate execpolicy rule files and see whether a command would be allowed, prompted, or blocked.",
  },
  {
    key: "codex login",
    href: "/codex/cli/reference#codex-login",
    type: "stable",
    description:
      "Authenticate Codex using ChatGPT OAuth, device auth, or an API key piped over stdin.",
  },
  {
    key: "codex logout",
    href: "/codex/cli/reference#codex-logout",
    type: "stable",
    description: "Remove stored authentication credentials.",
  },
  {
    key: "codex mcp",
    href: "/codex/cli/reference#codex-mcp",
    type: "experimental",
    description:
      "Manage Model Context Protocol servers (list, add, remove, authenticate).",
  },
  {
    key: "codex mcp-server",
    href: "/codex/cli/reference#codex-mcp-server",
    type: "experimental",
    description:
      "Run Codex itself as an MCP server over stdio. Useful when another agent consumes Codex.",
  },
  {
    key: "codex resume",
    href: "/codex/cli/reference#codex-resume",
    type: "stable",
    description:
      "Continue a previous interactive session by ID or resume the most recent conversation.",
  },
  {
    key: "codex fork",
    href: "/codex/cli/reference#codex-fork",
    type: "stable",
    description:
      "Fork a previous interactive session into a new thread, preserving the original transcript.",
  },
  {
    key: "codex sandbox",
    href: "/codex/cli/reference#codex-sandbox",
    type: "experimental",
    description:
      "Run arbitrary commands inside Codex-provided macOS seatbelt or Linux sandboxes (Landlock by default, optional bubblewrap pipeline).",
  },
];

export const execOptions = [
  {
    key: "PROMPT",
    type: "string | - (read stdin)",
    description:
      "Initial instruction for the task. Use `-` to pipe the prompt from stdin.",
  },
  {
    key: "--image, -i",
    type: "path[,path...]",
    description:
      "Attach images to the first message. Repeatable; supports comma-separated lists.",
  },
  {
    key: "--model, -m",
    type: "string",
    description: "Override the configured model for this run.",
  },
  {
    key: "--oss",
    type: "boolean",
    defaultValue: "false",
    description:
      "Use the local open source provider (requires a running Ollama instance).",
  },
  {
    key: "--sandbox, -s",
    type: "read-only | workspace-write | danger-full-access",
    description:
      "Sandbox policy for model-generated commands. Defaults to configuration.",
  },
  {
    key: "--profile, -p",
    type: "string",
    description: "Select a configuration profile defined in config.toml.",
  },
  {
    key: "--full-auto",
    type: "boolean",
    defaultValue: "false",
    description:
      "Apply the low-friction automation preset (`workspace-write` sandbox and `on-request` approvals).",
  },
  {
    key: "--dangerously-bypass-approvals-and-sandbox, --yolo",
    type: "boolean",
    defaultValue: "false",
    description:
      "Bypass approval prompts and sandboxing. Dangerous—only use inside an isolated runner.",
  },
  {
    key: "--cd, -C",
    type: "path",
    description: "Set the workspace root before executing the task.",
  },
  {
    key: "--skip-git-repo-check",
    type: "boolean",
    defaultValue: "false",
    description:
      "Allow running outside a Git repository (useful for one-off directories).",
  },
  {
    key: "--ephemeral",
    type: "boolean",
    defaultValue: "false",
    description: "Run without persisting session rollout files to disk.",
  },
  {
    key: "--output-schema",
    type: "path",
    description:
      "JSON Schema file describing the expected final response shape. Codex validates tool output against it.",
  },
  {
    key: "--color",
    type: "always | never | auto",
    defaultValue: "auto",
    description: "Control ANSI color in stdout.",
  },
  {
    key: "--json, --experimental-json",
    type: "boolean",
    defaultValue: "false",
    description:
      "Print newline-delimited JSON events instead of formatted text.",
  },
  {
    key: "--output-last-message, -o",
    type: "path",
    description:
      "Write the assistant’s final message to a file. Useful for downstream scripting.",
  },
  {
    key: "Resume subcommand",
    type: "codex exec resume [SESSION_ID]",
    description:
      "Resume an exec session by ID or add `--last` to continue the most recent session from the current working directory. Add `--all` to consider sessions from any directory. Accepts an optional follow-up prompt.",
  },
  {
    key: "-c, --config",
    type: "key=value",
    description:
      "Inline configuration override for the non-interactive run (repeatable).",
  },
];

export const appServerOptions = [
  {
    key: "--listen",
    type: "stdio:// | ws://IP:PORT",
    defaultValue: "stdio://",
    description:
      "Transport listener URL. `ws://` is experimental and intended for development/testing.",
  },
];

export const appOptions = [
  {
    key: "PATH",
    type: "path",
    defaultValue: ".",
    description:
      "Workspace path to open in Codex Desktop (`codex app` is available on macOS only).",
  },
  {
    key: "--download-url",
    type: "url",
    description:
      "Advanced override for the Codex desktop DMG download URL used during install.",
  },
];

export const debugAppServerSendMessageV2Options = [
  {
    key: "USER_MESSAGE",
    type: "string",
    description:
      "Message text sent to app-server through the built-in V2 test-client flow.",
  },
];

export const resumeOptions = [
  {
    key: "SESSION_ID",
    type: "uuid",
    description:
      "Resume the specified session. Omit and use `--last` to continue the most recent session.",
  },
  {
    key: "--last",
    type: "boolean",
    defaultValue: "false",
    description:
      "Skip the picker and resume the most recent conversation from the current working directory.",
  },
  {
    key: "--all",
    type: "boolean",
    defaultValue: "false",
    description:
      "Include sessions outside the current working directory when selecting the most recent session.",
  },
];

export const featuresOptions = [
  {
    key: "List subcommand",
    type: "codex features list",
    description:
      "Show known feature flags, their maturity stage, and their effective state.",
  },
  {
    key: "Enable subcommand",
    type: "codex features enable <feature>",
    description:
      "Persistently enable a feature flag in `config.toml`. Respects the active `--profile` when provided.",
  },
  {
    key: "Disable subcommand",
    type: "codex features disable <feature>",
    description:
      "Persistently disable a feature flag in `config.toml`. Respects the active `--profile` when provided.",
  },
];

export const execResumeOptions = [
  {
    key: "SESSION_ID",
    type: "uuid",
    description:
      "Resume the specified session. Omit and use `--last` to continue the most recent session.",
  },
  {
    key: "--last",
    type: "boolean",
    defaultValue: "false",
    description:
      "Resume the most recent conversation from the current working directory.",
  },
  {
    key: "--all",
    type: "boolean",
    defaultValue: "false",
    description:
      "Include sessions outside the current working directory when selecting the most recent session.",
  },
  {
    key: "--image, -i",
    type: "path[,path...]",
    description:
      "Attach one or more images to the follow-up prompt. Separate multiple paths with commas or repeat the flag.",
  },
  {
    key: "PROMPT",
    type: "string | - (read stdin)",
    description:
      "Optional follow-up instruction sent immediately after resuming.",
  },
];

export const forkOptions = [
  {
    key: "SESSION_ID",
    type: "uuid",
    description:
      "Fork the specified session. Omit and use `--last` to fork the most recent session.",
  },
  {
    key: "--last",
    type: "boolean",
    defaultValue: "false",
    description:
      "Skip the picker and fork the most recent conversation automatically.",
  },
  {
    key: "--all",
    type: "boolean",
    defaultValue: "false",
    description:
      "Show sessions beyond the current working directory in the picker.",
  },
];

export const execpolicyOptions = [
  {
    key: "--rules, -r",
    type: "path (repeatable)",
    description:
      "Path to an execpolicy rule file to evaluate. Provide multiple flags to combine rules across files.",
  },
  {
    key: "--pretty",
    type: "boolean",
    defaultValue: "false",
    description: "Pretty-print the JSON result.",
  },
  {
    key: "COMMAND...",
    type: "var-args",
    description: "Command to be checked against the specified policies.",
  },
];

export const loginOptions = [
  {
    key: "--with-api-key",
    type: "boolean",
    description:
      "Read an API key from stdin (for example `printenv OPENAI_API_KEY | codex login --with-api-key`).",
  },
  {
    key: "--device-auth",
    type: "boolean",
    description:
      "Use OAuth device code flow instead of launching a browser window.",
  },
  {
    key: "status subcommand",
    type: "codex login status",
    description:
      "Print the active authentication mode and exit with 0 when logged in.",
  },
];

export const applyOptions = [
  {
    key: "TASK_ID",
    type: "string",
    description:
      "Identifier of the Codex Cloud task whose diff should be applied.",
  },
];

export const sandboxMacOptions = [
  {
    key: "--full-auto",
    type: "boolean",
    defaultValue: "false",
    description:
      "Grant write access to the current workspace and `/tmp` without approvals.",
  },
  {
    key: "--config, -c",
    type: "key=value",
    description:
      "Pass configuration overrides into the sandboxed run (repeatable).",
  },
  {
    key: "COMMAND...",
    type: "var-args",
    description:
      "Shell command to execute under macOS Seatbelt. Everything after `--` is forwarded.",
  },
];

export const sandboxLinuxOptions = [
  {
    key: "--full-auto",
    type: "boolean",
    defaultValue: "false",
    description:
      "Grant write access to the current workspace and `/tmp` inside the Landlock sandbox.",
  },
  {
    key: "--config, -c",
    type: "key=value",
    description:
      "Configuration overrides applied before launching the sandbox (repeatable).",
  },
  {
    key: "COMMAND...",
    type: "var-args",
    description:
      "Command to execute under Landlock + seccomp. Provide the executable after `--`.",
  },
];

export const completionOptions = [
  {
    key: "SHELL",
    type: "bash | zsh | fish | power-shell | elvish",
    defaultValue: "bash",
    description: "Shell to generate completions for. Output prints to stdout.",
  },
];

export const cloudExecOptions = [
  {
    key: "QUERY",
    type: "string",
    description:
      "Task prompt. If omitted, Codex prompts interactively for details.",
  },
  {
    key: "--env",
    type: "ENV_ID",
    description:
      "Target Codex Cloud environment identifier (required). Use `codex cloud` to list options.",
  },
  {
    key: "--attempts",
    type: "1-4",
    defaultValue: "1",
    description:
      "Number of assistant attempts (best-of-N) Codex Cloud should run.",
  },
];

export const cloudListOptions = [
  {
    key: "--env",
    type: "ENV_ID",
    description: "Filter tasks by environment identifier.",
  },
  {
    key: "--limit",
    type: "1-20",
    defaultValue: "20",
    description: "Maximum number of tasks to return.",
  },
  {
    key: "--cursor",
    type: "string",
    description: "Pagination cursor returned by a previous request.",
  },
  {
    key: "--json",
    type: "boolean",
    defaultValue: "false",
    description: "Emit machine-readable JSON instead of plain text.",
  },
];

export const mcpCommands = [
  {
    key: "list",
    type: "--json",
    description:
      "List configured MCP servers. Add `--json` for machine-readable output.",
  },
  {
    key: "get <name>",
    type: "--json",
    description:
      "Show a specific server configuration. `--json` prints the raw config entry.",
  },
  {
    key: "add <name>",
    type: "-- <command...> | --url <value>",
    description:
      "Register a server using a stdio launcher command or a streamable HTTP URL. Supports `--env KEY=VALUE` for stdio transports.",
  },
  {
    key: "remove <name>",
    description: "Delete a stored MCP server definition.",
  },
  {
    key: "login <name>",
    type: "--scopes scope1,scope2",
    description:
      "Start an OAuth login for a streamable HTTP server (servers that support OAuth only).",
  },
  {
    key: "logout <name>",
    description:
      "Remove stored OAuth credentials for a streamable HTTP server.",
  },
];

export const mcpAddOptions = [
  {
    key: "COMMAND...",
    type: "stdio transport",
    description:
      "Executable plus arguments to launch the MCP server. Provide after `--`.",
  },
  {
    key: "--env KEY=VALUE",
    type: "repeatable",
    description:
      "Environment variable assignments applied when launching a stdio server.",
  },
  {
    key: "--url",
    type: "https://…",
    description:
      "Register a streamable HTTP server instead of stdio. Mutually exclusive with `COMMAND...`.",
  },
  {
    key: "--bearer-token-env-var",
    type: "ENV_VAR",
    description:
      "Environment variable whose value is sent as a bearer token when connecting to a streamable HTTP server.",
  },
];

## How to read this reference

This page catalogs every documented Codex CLI command and flag. Use the interactive tables to search by key or description. Each section indicates whether the option is stable or experimental and calls out risky combinations.

The CLI inherits most defaults from <code>~/.codex/config.toml</code>. Any
  <code>-c key=value</code> overrides you pass at the command line take
  precedence for that invocation. See [Config
  basics](https://developers.openai.com/codex/config-basic#configuration-precedence) for more information.

## Global flags

<ConfigTable client:load options={globalFlagOptions} />

These options apply to the base `codex` command and propagate to each subcommand unless a section below specifies otherwise.
When you run a subcommand, place global flags after it (for example, `codex exec --oss ...`) so Codex applies them as intended.

## Command overview

The Maturity column uses feature maturity labels such as Experimental, Beta,
  and Stable. See [Feature Maturity](https://developers.openai.com/codex/feature-maturity) for how to
  interpret these labels.

<ConfigTable
  client:load
  options={commandOverview}
  secondColumnTitle="Maturity"
  secondColumnVariant="maturity"
/>

## Command details

### `codex` (interactive)

Running `codex` with no subcommand launches the interactive terminal UI (TUI). The agent accepts the global flags above plus image attachments. Web search defaults to cached mode; use `--search` to switch to live browsing and `--full-auto` to let Codex run most commands without prompts.

### `codex app-server`

Launch the Codex app server locally. This is primarily for development and debugging and may change without notice.

<ConfigTable client:load options={appServerOptions} />

`codex app-server --listen stdio://` keeps the default JSONL-over-stdio behavior. `--listen ws://IP:PORT` enables WebSocket transport (experimental). If you generate schemas for client bindings, add `--experimental` to include gated fields and methods.

### `codex app`

Launch Codex Desktop from the terminal on macOS and optionally open a specific workspace path.

<ConfigTable client:load options={appOptions} />

`codex app` installs/opens the desktop app on macOS, then opens the provided workspace path. This subcommand is macOS-only.

### `codex debug app-server send-message-v2`

Send one message through app-server's V2 thread/turn flow using the built-in app-server test client.

<ConfigTable client:load options={debugAppServerSendMessageV2Options} />

This debug flow initializes with `experimentalApi: true`, starts a thread, sends a turn, and streams server notifications. Use it to reproduce and inspect app-server protocol behavior locally.

### `codex apply`

Apply the most recent diff from a Codex cloud task to your local repository. You must authenticate and have access to the task.

<ConfigTable client:load options={applyOptions} />

Codex prints the patched files and exits non-zero if `git apply` fails (for example, due to conflicts).

### `codex cloud`

Interact with Codex cloud tasks from the terminal. The default command opens an interactive picker; `codex cloud exec` submits a task directly, and `codex cloud list` returns recent tasks for scripting or quick inspection.

<ConfigTable client:load options={cloudExecOptions} />

Authentication follows the same credentials as the main CLI. Codex exits non-zero if the task submission fails.

#### `codex cloud list`

List recent cloud tasks with optional filtering and pagination.

<ConfigTable client:load options={cloudListOptions} />

Plain-text output prints a task URL followed by status details. Use `--json` for automation. The JSON payload contains a `tasks` array plus an optional `cursor` value. Each task includes `id`, `url`, `title`, `status`, `updated_at`, `environment_id`, `environment_label`, `summary`, `is_review`, and `attempt_total`.

### `codex completion`

Generate shell completion scripts and redirect the output to the appropriate location, for example `codex completion zsh > "${fpath[1]}/_codex"`.

<ConfigTable client:load options={completionOptions} />

### `codex features`

Manage feature flags stored in `~/.codex/config.toml`. The `enable` and `disable` commands persist changes so they apply to future sessions. When you launch with `--profile`, Codex writes to that profile instead of the root configuration.

<ConfigTable client:load options={featuresOptions} />

### `codex exec`

Use `codex exec` (or the short form `codex e`) for scripted or CI-style runs that should finish without human interaction.

<ConfigTable client:load options={execOptions} />

Codex writes formatted output by default. Add `--json` to receive newline-delimited JSON events (one per state change). The optional `resume` subcommand lets you continue non-interactive tasks. Use `--last` to pick the most recent session from the current working directory, or add `--all` to search across all sessions:

<ConfigTable client:load options={execResumeOptions} />

### `codex execpolicy`

Check `execpolicy` rule files before you save them. `codex execpolicy check` accepts one or more `--rules` flags (for example, files under `~/.codex/rules`) and emits JSON showing the strictest decision and any matching rules. Add `--pretty` to format the output. The `execpolicy` command is currently in preview.

<ConfigTable client:load options={execpolicyOptions} />

### `codex login`

Authenticate the CLI with a ChatGPT account or API key. With no flags, Codex opens a browser for the ChatGPT OAuth flow.

<ConfigTable client:load options={loginOptions} />

`codex login status` exits with `0` when credentials are present, which is helpful in automation scripts.

### `codex logout`

Remove saved credentials for both API key and ChatGPT authentication. This command has no flags.

### `codex mcp`

Manage Model Context Protocol server entries stored in `~/.codex/config.toml`.

<ConfigTable client:load options={mcpCommands} />

The `add` subcommand supports both stdio and streamable HTTP transports:

<ConfigTable client:load options={mcpAddOptions} />

OAuth actions (`login`, `logout`) only work with streamable HTTP servers (and only when the server supports OAuth).

### `codex mcp-server`

Run Codex as an MCP server over stdio so that other tools can connect. This command inherits global configuration overrides and exits when the downstream client closes the connection.

### `codex resume`

Continue an interactive session by ID or resume the most recent conversation. `codex resume` scopes `--last` to the current working directory unless you pass `--all`. It accepts the same global flags as `codex`, including model and sandbox overrides.

<ConfigTable client:load options={resumeOptions} />

### `codex fork`

Fork a previous interactive session into a new thread. By default, `codex fork` opens the session picker; add `--last` to fork your most recent session instead.

<ConfigTable client:load options={forkOptions} />

### `codex sandbox`

Use the sandbox helper to run a command under the same policies Codex uses internally.

#### macOS seatbelt

<ConfigTable client:load options={sandboxMacOptions} />

#### Linux Landlock

<ConfigTable client:load options={sandboxLinuxOptions} />

## Flag combinations and safety tips

- Set `--full-auto` for unattended local work, but avoid combining it with `--dangerously-bypass-approvals-and-sandbox` unless you are inside a dedicated sandbox VM.
- When you need to grant Codex write access to more directories, prefer `--add-dir` rather than forcing `--sandbox danger-full-access`.
- Pair `--json` with `--output-last-message` in CI to capture machine-readable progress and a final natural-language summary.

## Related resources

- [Codex CLI overview](https://developers.openai.com/codex/cli): installation, upgrades, and quick tips.
- [Config basics](https://developers.openai.com/codex/config-basic): persist defaults like the model and provider.
- [Advanced Config](https://developers.openai.com/codex/config-advanced): profiles, providers, sandbox tuning, and integrations.
- [AGENTS.md](https://developers.openai.com/codex/guides/agents-md): conceptual overview of Codex agent capabilities and best practices.



# Slash commands in Codex CLI

Slash commands give you fast, keyboard-first control over Codex. Type `/` in
the composer to open the slash popup, choose a command, and Codex will perform
actions such as switching models, adjusting permissions, or summarizing long
conversations without leaving the terminal.

This guide shows you how to:

- Find the right built-in slash command for a task
- Steer an active session with commands like `/model`, `/fast`,
  `/personality`, `/permissions`, `/agent`, and `/status`

## Built-in slash commands

Codex ships with the following commands. Open the slash popup and start typing
the command name to filter the list.

| Command                                                                         | Purpose                                                         | When to use it                                                                                            |
| ------------------------------------------------------------------------------- | --------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------- |
| [`/permissions`](#update-permissions-with-permissions)                          | Set what Codex can do without asking first.                     | Relax or tighten approval requirements mid-session, such as switching between Auto and Read Only.         |
| [`/sandbox-add-read-dir`](#grant-sandbox-read-access-with-sandbox-add-read-dir) | Grant sandbox read access to an extra directory (Windows only). | Unblock commands that need to read an absolute directory path outside the current readable roots.         |
| [`/agent`](#switch-agent-threads-with-agent)                                    | Switch the active agent thread.                                 | Inspect or continue work in a spawned subagent thread.                                                    |
| [`/apps`](#browse-apps-with-apps)                                               | Browse apps (connectors) and insert them into your prompt.      | Attach an app as `$app-slug` before asking Codex to use it.                                               |
| [`/clear`](#clear-the-terminal-and-start-a-new-chat-with-clear)                 | Clear the terminal and start a fresh chat.                      | Reset the visible UI and conversation together when you want a clean slate.                               |
| [`/compact`](#keep-transcripts-lean-with-compact)                               | Summarize the visible conversation to free tokens.              | Use after long runs so Codex retains key points without blowing the context window.                       |
| [`/copy`](#copy-the-latest-response-with-copy)                                  | Copy the latest completed Codex output.                         | Grab the latest finished response or plan text without manually selecting it.                             |
| [`/diff`](#review-changes-with-diff)                                            | Show the Git diff, including files Git isn't tracking yet.      | Review Codex's edits before you commit or run tests.                                                      |
| [`/exit`](#exit-the-cli-with-quit-or-exit)                                      | Exit the CLI (same as `/quit`).                                 | Alternative spelling; both commands exit the session.                                                     |
| [`/experimental`](#toggle-experimental-features-with-experimental)              | Toggle experimental features.                                   | Enable optional features such as subagents from the CLI.                                                  |
| [`/feedback`](#send-feedback-with-feedback)                                     | Send logs to the Codex maintainers.                             | Report issues or share diagnostics with support.                                                          |
| [`/init`](#generate-agentsmd-with-init)                                         | Generate an `AGENTS.md` scaffold in the current directory.      | Capture persistent instructions for the repository or subdirectory you're working in.                     |
| [`/logout`](#sign-out-with-logout)                                              | Sign out of Codex.                                              | Clear local credentials when using a shared machine.                                                      |
| [`/mcp`](#list-mcp-tools-with-mcp)                                              | List configured Model Context Protocol (MCP) tools.             | Check which external tools Codex can call during the session.                                             |
| [`/mention`](#highlight-files-with-mention)                                     | Attach a file to the conversation.                              | Point Codex at specific files or folders you want it to inspect next.                                     |
| [`/model`](#set-the-active-model-with-model)                                    | Choose the active model (and reasoning effort, when available). | Switch between general-purpose models (`gpt-4.1-mini`) and deeper reasoning models before running a task. |
| [`/fast`](#toggle-fast-mode-with-fast)                                          | Toggle Fast mode for GPT-5.4.                                   | Turn Fast mode on or off, or check whether the current thread is using it.                                |
| [`/plan`](#switch-to-plan-mode-with-plan)                                       | Switch to plan mode and optionally send a prompt.               | Ask Codex to propose an execution plan before implementation work starts.                                 |
| [`/personality`](#set-a-communication-style-with-personality)                   | Choose a communication style for responses.                     | Make Codex more concise, more explanatory, or more collaborative without changing your instructions.      |
| [`/ps`](#check-background-terminals-with-ps)                                    | Show experimental background terminals and their recent output. | Check long-running commands without leaving the main transcript.                                          |
| [`/fork`](#fork-the-current-conversation-with-fork)                             | Fork the current conversation into a new thread.                | Branch the active session to explore a new approach without losing the current transcript.                |
| [`/resume`](#resume-a-saved-conversation-with-resume)                           | Resume a saved conversation from your session list.             | Continue work from a previous CLI session without starting over.                                          |
| [`/new`](#start-a-new-conversation-with-new)                                    | Start a new conversation inside the same CLI session.           | Reset the chat context without leaving the CLI when you want a fresh prompt in the same repo.             |
| [`/quit`](#exit-the-cli-with-quit-or-exit)                                      | Exit the CLI.                                                   | Leave the session immediately.                                                                            |
| [`/review`](#ask-for-a-working-tree-review-with-review)                         | Ask Codex to review your working tree.                          | Run after Codex completes work or when you want a second set of eyes on local changes.                    |
| [`/status`](#inspect-the-session-with-status)                                   | Display session configuration and token usage.                  | Confirm the active model, approval policy, writable roots, and remaining context capacity.                |
| [`/debug-config`](#inspect-config-layers-with-debug-config)                     | Print config layer and requirements diagnostics.                | Debug precedence and policy requirements, including experimental network constraints.                     |
| [`/statusline`](#configure-footer-items-with-statusline)                        | Configure TUI status-line fields interactively.                 | Pick and reorder footer items (model/context/limits/git/tokens/session) and persist in config.toml.       |

`/quit` and `/exit` both exit the CLI. Use them only after you have saved or
committed any important work.

The `/approvals` command still works as an alias, but it no longer appears in the slash popup list.

## Control your session with slash commands

The following workflows keep your session on track without restarting Codex.

### Set the active model with `/model`

1. Start Codex and open the composer.
2. Type `/model` and press Enter.
3. Choose a model such as `gpt-4.1-mini` or `gpt-4.1` from the popup.

Expected: Codex confirms the new model in the transcript. Run `/status` to verify the change.

### Toggle Fast mode with `/fast`

1. Type `/fast on`, `/fast off`, or `/fast status`.
2. If you want the setting to persist, confirm the update when Codex offers to save it.

Expected: Codex reports whether Fast mode is on or off for the current thread. In the TUI footer, you can also show a Fast mode status-line item with `/statusline`.

### Set a communication style with `/personality`

Use `/personality` to change how Codex communicates without rewriting your prompt.

1. In an active conversation, type `/personality` and press Enter.
2. Choose a style from the popup.

Expected: Codex confirms the new style in the transcript and uses it for later
responses in the thread.

Codex supports `friendly`, `pragmatic`, and `none` personalities. Use `none`
to disable personality instructions.

If the active model doesn't support personality-specific instructions, Codex hides this command.

### Switch to plan mode with `/plan`

1. Type `/plan` and press Enter to switch the active conversation into plan
   mode.
2. Optional: provide inline prompt text (for example, `/plan Propose a
migration plan for this service`).
3. You can paste content or attach images while using inline `/plan` arguments.

Expected: Codex enters plan mode and uses your optional inline prompt as the first planning request.

While a task is already running, `/plan` is temporarily unavailable.

### Toggle experimental features with `/experimental`

1. Type `/experimental` and press Enter.
2. Toggle the features you want (for example, Apps or Smart Approvals), then restart Codex if the prompt asks you to.

Expected: Codex saves your feature choices to config and applies them on restart.

### Clear the terminal and start a new chat with `/clear`

1. Type `/clear` and press Enter.

Expected: Codex clears the terminal, resets the visible transcript, and starts
a fresh chat in the same CLI session.

Unlike <kbd>Ctrl</kbd>+<kbd>L</kbd>, `/clear` starts a new conversation.

<kbd>Ctrl</kbd>+<kbd>L</kbd> only clears the terminal view and keeps the current
chat. Codex disables both actions while a task is in progress.

### Update permissions with `/permissions`

1. Type `/permissions` and press Enter.
2. Select the approval preset that matches your comfort level, for example
   `Auto` for hands-off runs or `Read Only` to review edits.

Expected: Codex announces the updated policy. Future actions respect the
updated approval mode until you change it again.

### Copy the latest response with `/copy`

1. Type `/copy` and press Enter.

Expected: Codex copies the latest completed Codex output to your clipboard.

If a turn is still running, `/copy` uses the latest completed output instead of
the in-progress response. The command is unavailable before the first completed
Codex output and immediately after a rollback.

### Grant sandbox read access with `/sandbox-add-read-dir`

This command is available only when running the CLI natively on Windows.

1. Type `/sandbox-add-read-dir C:\absolute\directory\path` and press Enter.
2. Confirm the path is an existing absolute directory.

Expected: Codex refreshes the Windows sandbox policy and grants read access to
that directory for later commands that run in the sandbox.

### Inspect the session with `/status`

1. In any conversation, type `/status`.
2. Review the output for the active model, approval policy, writable roots, and current token usage.

Expected: You see a summary like what `codex status` prints in the shell,
confirming Codex is operating where you expect.

### Inspect config layers with `/debug-config`

1. Type `/debug-config`.
2. Review the output for config layer order (lowest precedence first), on/off
   state, and policy sources.

Expected: Codex prints layer diagnostics plus policy details such as
`allowed_approval_policies`, `allowed_sandbox_modes`, `mcp_servers`, `rules`,
`enforce_residency`, and `experimental_network` when configured.

Use this output to debug why an effective setting differs from `config.toml`.

### Configure footer items with `/statusline`

1. Type `/statusline`.
2. Use the picker to toggle and reorder items, then confirm.

Expected: The footer status line updates immediately and persists to
`tui.status_line` in `config.toml`.

Available status-line items include model, model+reasoning, context stats, rate
limits, git branch, token counters, session id, current directory/project root,
and Codex version.

### Check background terminals with `/ps`

1. Type `/ps`.
2. Review the list of background terminals and their status.

Expected: Codex shows each background terminal's command plus up to three
recent, non-empty output lines so you can gauge progress at a glance.

Background terminals appear when `unified_exec` is in use; otherwise, the list may be empty.

### Keep transcripts lean with `/compact`

1. After a long exchange, type `/compact`.
2. Confirm when Codex offers to summarize the conversation so far.

Expected: Codex replaces earlier turns with a concise summary, freeing context
while keeping critical details.

### Review changes with `/diff`

1. Type `/diff` to inspect the Git diff.
2. Scroll through the output inside the CLI to review edits and added files.

Expected: Codex shows changes you've staged, changes you haven't staged yet,
and files Git hasn't started tracking, so you can decide what to keep.

### Highlight files with `/mention`

1. Type `/mention` followed by a path, for example `/mention src/lib/api.ts`.
2. Select the matching result from the popup.

Expected: Codex adds the file to the conversation, ensuring follow-up turns reference it directly.

### Start a new conversation with `/new`

1. Type `/new` and press Enter.

Expected: Codex starts a fresh conversation in the same CLI session, so you
can switch tasks without leaving your terminal.

Unlike `/clear`, `/new` does not clear the current terminal view first.

### Resume a saved conversation with `/resume`

1. Type `/resume` and press Enter.
2. Choose the session you want from the saved-session picker.

Expected: Codex reloads the selected conversation's transcript so you can pick
up where you left off, keeping the original history intact.

### Fork the current conversation with `/fork`

1. Type `/fork` and press Enter.

Expected: Codex clones the current conversation into a new thread with a fresh
ID, leaving the original transcript untouched so you can explore an alternative
approach in parallel.

If you need to fork a saved session instead of the current one, run
`codex fork` in your terminal to open the session picker.

### Generate `AGENTS.md` with `/init`

1. Run `/init` in the directory where you want Codex to look for persistent instructions.
2. Review the generated `AGENTS.md`, then edit it to match your repository conventions.

Expected: Codex creates an `AGENTS.md` scaffold you can refine and commit for
future sessions.

### Ask for a working tree review with `/review`

1. Type `/review`.
2. Follow up with `/diff` if you want to inspect the exact file changes.

Expected: Codex summarizes issues it finds in your working tree, focusing on
behavior changes and missing tests. It uses the current session model unless
you set `review_model` in `config.toml`.

### List MCP tools with `/mcp`

1. Type `/mcp`.
2. Review the list to confirm which MCP servers and tools are available.

Expected: You see the configured Model Context Protocol (MCP) tools Codex can call in this session.

### Browse apps with `/apps`

1. Type `/apps`.
2. Pick an app from the list.

Expected: Codex inserts the app mention into the composer as `$app-slug`, so
you can immediately ask Codex to use it.

### Switch agent threads with `/agent`

1. Type `/agent` and press Enter.
2. Select the thread you want from the picker.

Expected: Codex switches the active thread so you can inspect or continue that
agent's work.

### Send feedback with `/feedback`

1. Type `/feedback` and press Enter.
2. Follow the prompts to include logs or diagnostics.

Expected: Codex collects the requested diagnostics and submits them to the
maintainers.

### Sign out with `/logout`

1. Type `/logout` and press Enter.

Expected: Codex clears local credentials for the current user session.

### Exit the CLI with `/quit` or `/exit`

1. Type `/quit` (or `/exit`) and press Enter.

Expected: Codex exits immediately. Save or commit any important work first.
