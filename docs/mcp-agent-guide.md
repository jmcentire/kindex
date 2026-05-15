# Kindex MCP Agent Guide

This guide is for AI coding agents connected to the `kindex` MCP server.
Kindex is the user's durable memory layer: a persistent knowledge graph for
projects, decisions, tasks, constraints, reminders, and session context.

## Core Rule

Use kindex proactively. Do not wait for the user to mention it.

At the start of meaningful work, search or ask kindex for relevant context.
During work, capture durable discoveries as they happen. At the end of work,
summarize the session with a tag update.

## Session Lifecycle

1. Start: call `tag_start` with a short session name and current focus, or
   `tag_resume` if continuing known work.
2. Orient: call `search`, `context`, or `ask` before significant investigation
   or edits.
3. Work: capture discoveries, decisions, tasks, watches, and connections while
   they are fresh.
4. Segment: when changing topics, call `tag_update` with `action=segment`.
5. End: call `tag_update` with `action=end` and a concise summary.

## Tool Use Patterns

### Search Before Work

Use `search` when starting a task, entering an unfamiliar project, revisiting a
topic, or before adding new knowledge. This prevents duplicates and reveals
constraints or previous decisions.

### Capture Knowledge

Use `add` for durable knowledge, not routine transcript logging.

Recommended node types:

- `concept`: facts, architecture, important files, patterns, domain terms
- `decision`: choices and rationale
- `question`: open problems that should resurface
- `constraint`: hard invariants or safety rules
- `directive`: soft preferences and style rules
- `watch`: ongoing risks, flaky tests, unstable APIs, tech debt
- `checkpoint`: pre-flight checks before release or high-risk work

Always search before adding unless the knowledge is obviously new and immediate.

### Link Related Ideas

Use `link` when two nodes relate. Prefer explicit relationship types:

- `relates_to`
- `depends_on`
- `implements`
- `contradicts`
- `blocks`
- `context_of`
- `answers`
- `supersedes`

The graph becomes useful through links. Add them whenever a relationship matters
for future work.

### Learn From Large Text

Use `learn` after reading long files, logs, design docs, transcripts, or command
outputs. It extracts multiple concepts in one pass.

### Manage Tasks

Use `task_add` for actionable work items. Link tasks to relevant concepts when
possible so they surface contextually.

Use `task_list` when planning or resuming work. Use `task_done` immediately when
a task is completed.

### Add Watches

Use `watch_add` for issues that need future attention: flaky tests, unstable
dependencies, brittle APIs, unresolved performance concerns, or migration
deadlines.

Use `watch_resolve` once the risk is fixed or irrelevant.

### Use Reminders

Use `remind_create` for time-based follow-up. A reminder may include a shell
action, natural-language instructions, or both.

## What Not To Capture

Do not capture trivial file reads, routine git status output, obvious
boilerplate, private secrets, duplicate knowledge, or every step of a
transcript. Capture what should help a future agent or future user.

## Recommended Startup Behavior

1. `tag_start` or `tag_resume`
2. `search` the current project/topic
3. `task_list` for nearby or global open tasks
4. `remind_check` if reminders are enabled
5. Continue with the user's task

## Recommended Shutdown Behavior

1. Add any final decisions, tasks, watches, or questions
2. Link newly captured nodes where obvious
3. `tag_update` with `action=end` and a concise summary

## Client Setup

Install kindex with the `mcp` extra using whichever installer you prefer
(`pip install 'kindex[mcp]'`, `uv tool install 'kindex[mcp]'`, or
`git clone … && make install`). Then wire each agent:

### Claude Code

```bash
claude mcp add --scope user --transport stdio kindex -- kin-mcp
kin init
kin setup-claude-md --install
kin setup-hooks
```

Claude Code supports lifecycle hooks, so `kin setup-hooks` can prime context,
capture pre-compaction context, and run stop guards.

### Codex

```bash
kin setup-codex-mcp
kin setup-agents-md --install --global
kin ingest codex-sessions   # optional: backfill saved sessions
```

Or hand-edit `~/.codex/config.toml`:

```toml
[mcp_servers.kindex]
command = "kin-mcp"
```

### Gemini CLI

```bash
kin setup-gemini-mcp
kin setup-gemini-md --install
```

Or hand-edit `~/.gemini/settings.json`:

```json
{ "mcpServers": { "kindex": { "command": "kin-mcp", "args": [] } } }
```

### OpenCode

```bash
kin setup-opencode-mcp
kin setup-agents-md --install   # OpenCode reads AGENTS.md
```

Or hand-edit `~/.config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "kindex": { "type": "local", "command": ["kin-mcp"], "enabled": true }
  }
}
```

### Cursor

```bash
kin setup-cursor-mcp
kin setup-cursor-rules --install
```

The rules file lands at `~/.cursor/rules/kindex.mdc` with `alwaysApply: true`.
Or hand-edit `~/.cursor/mcp.json`:

```json
{ "mcpServers": { "kindex": { "type": "stdio", "command": "kin-mcp" } } }
```

## Human Setup Checklist

1. Install kindex (`pip install 'kindex[mcp]'` / `uv tool install 'kindex[mcp]'` / source).
2. Run `kin init` to initialize the knowledge graph.
3. Install the MCP server for your agent (see Client Setup above).
4. Install the agent instruction file (`CLAUDE.md`, `AGENTS.md`, `GEMINI.md`, or Cursor `.mdc`).
5. Run `kin setup-cron` for periodic maintenance.
6. Backfill saved sessions with `kin ingest codex-sessions` or `kin ingest sessions`.
