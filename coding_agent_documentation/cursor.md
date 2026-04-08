# Slash commands

CommandDescription`/plan`Switch to Plan mode to design your approach before coding`/ask`Switch to Ask mode for read-only exploration`/model <model>`Set or list models`/auto-run [state]`Toggle auto-run (default) or set [on|off|status]`/sandbox`Configure sandbox mode and network access settings`/max-mode [on|off]`Toggle max mode on models that support it`/new-chat`Start a new chat session`/vim`Toggle Vim keys`/help [command]`Show help (/help [cmd])`/feedback <message>`Share feedback with the team`/resume <chat>`Resume a previous chat by folder name`/usage`View Cursor streaks and usage stats`/about`Show environment and CLI setup details`/max-mode [on|off]`Toggle max mode on models that support it`/copy-request-id`Copy last request ID to clipboard`/copy-conversation-id`Copy conversation ID to clipboard`/logout`Sign out from Cursor`/quit`Exit`/setup-terminal`Auto-configure terminal keybindings (see [Terminal Setup](/docs/cli/reference/terminal-setup))`/mcp list`Browse, enable, and configure MCP servers`/mcp enable <name>`Enable an MCP server`/mcp disable <name>`Disable an MCP server`/rules`Create new rules or edit existing rules`/commands`Create new commands or edit existing commands`/compress`Summarize conversation to free context space


# Parameters

## Global options

Global options can be used with any command:

OptionDescription`-v, --version`Output the version number`--api-key <key>`API key for authentication (can also use `CURSOR_API_KEY` env var)`-H, --header <header>`Add custom header to agent requests (format: `Name: Value`, can be used multiple times)`-p, --print`Print responses to console (for scripts or non-interactive use). Has access to all tools, including write and shell.`--output-format <format>`Output format (only works with `--print`): `text`, `json`, or `stream-json` (default: `text`)`--stream-partial-output`Stream partial output as individual text deltas (only works with `--print` and `stream-json` format)`-c, --cloud`Start in cloud mode`--resume [chatId]`Resume a chat session`--continue`Continue the previous session (alias for `--resume=-1`)`--model <model>`Model to use`--mode <mode>`Set agent mode: `plan` or `ask` (agent is the default when no mode is specified)`--plan`Start in plan mode (shorthand for `--mode=plan`)`--list-models`List all available models`-f, --force`Force allow commands unless explicitly denied`--yolo`Alias for `--force``--sandbox <mode>`Set sandbox mode: `enabled` or `disabled``--approve-mcps`Automatically approve all MCP servers`--trust`Trust the workspace without prompting (headless mode only)`--workspace <path>`Workspace directory to use`--worktree`Run in a new Git worktree under `~/.cursor/worktrees` (see [CLI worktrees](/docs/cli/using#cli-worktrees))`-h, --help`Display help for command
## Commands

CommandDescriptionUsage`agent`Start in agent mode (the default)`agent agent``login`Authenticate with Cursor`agent login``logout`Sign out and clear stored authentication`agent logout``status` | `whoami`Check authentication status`agent status``about`Display version, system, and account info`agent about``models`List all available models`agent models``mcp`Manage MCP servers`agent mcp``acp`Start ACP server mode (advanced, hidden command)`agent acp``update`Update Cursor Agent to the latest version`agent update``ls`List previous chat sessions`agent ls``resume`Resume the latest chat session`agent resume``create-chat`Create a new empty chat and return its ID`agent create-chat``generate-rule` | `rule`Generate a new Cursor rule interactively`agent generate-rule``install-shell-integration`Install shell integration to `~/.zshrc``agent install-shell-integration``uninstall-shell-integration`Remove shell integration from `~/.zshrc``agent uninstall-shell-integration``help [command]`Display help for command`agent help [command]`
`agent acp` is intended for custom ACP clients and advanced integrations. It is
hidden from default command help output.

When no command is specified, Cursor Agent starts in interactive agent mode by
default.

## MCP

Manage MCP servers configured for Cursor Agent.

SubcommandDescriptionUsage`login <identifier>`Authenticate with an MCP server configured in `.cursor/mcp.json``agent mcp login <identifier>``list`List configured MCP servers and their status`agent mcp list``list-tools <identifier>`List available tools and their argument names for a specific MCP`agent mcp list-tools <identifier>``enable <identifier>`Enable an MCP server`agent mcp enable <identifier>``disable <identifier>`Disable an MCP server`agent mcp disable <identifier>`
All MCP commands support `-h, --help` for command-specific help.

## Arguments

When starting in chat mode (default behavior), you can provide an initial prompt:

**Arguments:**

- `prompt` — Initial prompt for the agent

## Getting help

All commands support the global `-h, --help` option to display command-specific help.



# Authentication

Cursor CLI supports two authentication methods: browser-based login (recommended) and API keys.

## Browser authentication (recommended)

Use the browser flow for the easiest authentication experience:

```
# Log in using browser flow
agent login

# Check authentication status
agent status

# Log out and clear stored authentication
agent logout
```

The login command will open your default browser and prompt you to authenticate with your Cursor account. Once completed, your credentials are securely stored locally.

## API key authentication

For automation, scripts, or CI/CD environments, use API key authentication:

### Step 1: Generate an API key

Generate an API key from [Cursor Dashboard → Cloud Agents](https://cursor.com/dashboard/cloud-agents) under **User API Keys**.

### Step 2: Set the API key

You can provide the API key in two ways:

**Option 1: Environment variable (recommended)**

```
export CURSOR_API_KEY=your_api_key_here
agent "implement user authentication"
```

**Option 2: Command line flag**

```
agent --api-key your_api_key_here "implement user authentication"
```

## Authentication status

Check your current authentication status:

```
agent status
```

This command will display:

- Whether you're authenticated
- Your account information
- Current endpoint configuration

## Troubleshooting

- **"Not authenticated" errors:** Run `agent login` or ensure your API key is correctly set
- **SSL certificate errors:** Use the `--insecure` flag for development environments
- **Endpoint issues:** Use the `--endpoint` flag to specify a custom API endpoint


# Permissions

Configure what the agent is allowed to do using permission tokens in your CLI configuration. Permissions are set in `~/.cursor/cli-config.json` (global) or `<project>/.cursor/cli.json` (project-specific).

## Permission types

### Shell commands

**Format:** `Shell(commandBase)`

Controls access to shell commands. The `commandBase` is the first token in the command line. Supports glob patterns and an optional `command:args` syntax for finer control.

ExampleDescription`Shell(ls)`Allow running `ls` commands`Shell(git)`Allow any `git` subcommand`Shell(npm)`Allow npm package manager commands`Shell(curl:*)`Allow `curl` with any arguments`Shell(rm)`Deny destructive file removal (commonly in `deny`)
### File reads

**Format:** `Read(pathOrGlob)`

Controls read access to files and directories. Supports glob patterns.

ExampleDescription`Read(src/**/*.ts)`Allow reading TypeScript files in `src``Read(**/*.md)`Allow reading markdown files anywhere`Read(.env*)`Deny reading environment files`Read(/etc/passwd)`Deny reading system files
### File writes

**Format:** `Write(pathOrGlob)`

Controls write access to files and directories. Supports glob patterns. When using in print mode, `--force` is required to write files.

ExampleDescription`Write(src/**)`Allow writing to any file under `src``Write(package.json)`Allow modifying package.json`Write(**/*.key)`Deny writing private key files`Write(**/.env*)`Deny writing environment files
### Web fetch

**Format:** `WebFetch(domainOrPattern)`

Controls which domains the agent can fetch when using the web fetch tool (e.g., to retrieve documentation or web pages). Without an allowlist entry, each fetch prompts for approval. Add domains to `allow` to auto-approve fetches from trusted sources.

ExampleDescription`WebFetch(docs.github.com)`Allow fetches from `docs.github.com``WebFetch(*.example.com)`Allow fetches from any subdomain of `example.com``WebFetch(*)`Allow fetches from any domain (use with caution)
**Domain pattern matching:**

- `*` matches all domains
- `*.example.com` matches subdomains (e.g., `docs.example.com`, `api.example.com`)
- `example.com` matches that exact domain only

### MCP tools

**Format:** `Mcp(server:tool)`

Controls which MCP (Model Context Protocol) tools the agent can run. Use `server` (from `mcp.json`) and `tool` name, with `*` for wildcards.

ExampleDescription`Mcp(datadog:*)`Allow all tools from the Datadog MCP server`Mcp(*:search)`Allow any server's `search` tool`Mcp(*:*)`Allow all MCP tools (use with caution)
## Configuration

Add permissions to the `permissions` object in your CLI configuration file:

```
{
  "permissions": {
    "allow": [
      "Shell(ls)",
      "Shell(git)",
      "Read(src/**/*.ts)",
      "Write(package.json)",
      "WebFetch(docs.github.com)",
      "WebFetch(*.github.com)",
      "Mcp(datadog:*)"
    ],
    "deny": [
      "Shell(rm)",
      "Read(.env*)",
      "Write(**/*.key)",
      "WebFetch(malicious-site.com)"
    ]
  }
}
```

## Pattern matching

- Glob patterns use `**`, `*`, and `?` wildcards
- Relative paths are scoped to the current workspace
- Absolute paths can target files outside the project
- Deny rules take precedence over allow rules
- Use `command:args` (e.g., `curl:*`) to match both command and arguments with globs


# Configuration

Configure the Agent CLI using the `cli-config.json` file.

## File location

TypePlatformPathGlobalmacOS/Linux`~/.cursor/cli-config.json`GlobalWindows`$env:USERPROFILE\.cursor\cli-config.json`ProjectAll`<project>/.cursor/cli.json`
Only permissions can be configured at the project level. All other CLI
settings must be set globally.

Override with environment variables:

- **`CURSOR_CONFIG_DIR`**: custom directory path
- **`XDG_CONFIG_HOME`** (Linux/BSD): uses `$XDG_CONFIG_HOME/cursor/cli-config.json`

## Schema

### Required fields

FieldTypeDescription`version`numberConfig schema version (current: `1`)`editor.vimMode`booleanEnable Vim keybindings (default: `false`)`permissions.allow`string[]Permitted operations (see [Permissions](/docs/cli/reference/permissions))`permissions.deny`string[]Forbidden operations (see [Permissions](/docs/cli/reference/permissions))
### Optional fields

FieldTypeDescription`model`objectSelected model configuration`hasChangedDefaultModel`booleanCLI-managed model override flag`network.useHttp1ForAgent`booleanUse HTTP/1.1 instead of HTTP/2 for agent connections (default: `false`)`attribution.attributeCommitsToAgent`booleanAdd "Made with Cursor" trailer to Agent commits (default: `true`)`attribution.attributePRsToAgent`booleanAdd "Made with Cursor" footer to Agent PRs (default: `true`)
## Examples

### Minimal config

```
{
  "version": 1,
  "editor": { "vimMode": false },
  "permissions": { "allow": ["Shell(ls)"], "deny": [] }
}
```

### Enable Vim mode

```
{
  "version": 1,
  "editor": { "vimMode": true },
  "permissions": { "allow": ["Shell(ls)"], "deny": [] }
}
```

### Configure permissions

```
{
  "version": 1,
  "editor": { "vimMode": false },
  "permissions": {
    "allow": ["Shell(ls)", "Shell(echo)"],
    "deny": ["Shell(rm)"]
  }
}
```

See [Permissions](/docs/cli/reference/permissions) for available permission types and examples.

## Troubleshooting

**Config errors**: Move the file aside and restart:

```
mv ~/.cursor/cli-config.json ~/.cursor/cli-config.json.bad
```

**Changes don't persist**: Ensure valid JSON and write permissions. Some fields are CLI-managed and may be overwritten.

## Notes

- Pure JSON format (no comments)
- CLI performs self-repair for missing fields
- Corrupted files are backed up as `.bad` and recreated
- Permission entries are exact strings (see [Permissions](/docs/cli/reference/permissions) for details)

## Models

You can select a model for the CLI using the `/model` slash command.

```
/model auto
/model gpt-5.2
/model sonnet-4.5-thinking
```

See the [Slash commands](/docs/cli/reference/slash-commands) docs for other commands.

## Proxy configuration

If your network routes traffic through a proxy server, configure the CLI using environment variables and the config file.

### Environment variables

Set these environment variables before running the CLI:

```
export HTTP_PROXY=http://your-proxy:port
export HTTPS_PROXY=http://your-proxy:port
export NODE_USE_ENV_PROXY=1
```

If your proxy performs SSL inspection (man-in-the-middle), also trust your organization's CA certificate:

```
export NODE_EXTRA_CA_CERTS=/path/to/corporate-ca-cert.pem
```

### HTTP/1.1 fallback

Some enterprise proxies (like Zscaler) don't support HTTP/2 bidirectional streaming. Enable HTTP/1.1 mode in your config:

```
{
  "version": 1,
  "editor": { "vimMode": false },
  "permissions": { "allow": [], "deny": [] },
  "network": {
    "useHttp1ForAgent": true
  }
}
```

This switches agent connections to HTTP/1.1 with Server-Sent Events (SSE), which works with most corporate proxies.

See [Network Configuration](/docs/enterprise/network-configuration) for proxy testing commands and troubleshooting.
