# Kindex Human Guide

This guide is for humans installing and operating Kindex. The AI-facing
operating contract lives in [mcp-agent-guide.md](mcp-agent-guide.md).

## Public Docs

- Canonical public website: <https://kindex.tools/>
- GitHub Pages docs build from this repo: <https://jmcentire.github.io/kindex/>
- Source and releases: <https://github.com/jmcentire/kindex>
- PyPI package: <https://pypi.org/project/kindex/>

The canonical `kindex.tools` site is served by the companion `Kindex-Tools`
Fly static app. This repo's Pages workflow publishes the `docs/` directory,
including `docs/.well-known/`, to the GitHub Pages docs URL so the repo-local
documentation and MCP server-card metadata stay in sync with the release.

## Install

Pick one installer:

```bash
pip install 'kindex[mcp]'
uv tool install 'kindex[mcp]'
uvx --from 'kindex[mcp]' kin-mcp --help
git clone https://github.com/jmcentire/kindex && cd kindex && make install
```

Then initialize the graph:

```bash
kin init
```

Use extras when you need more than the base CLI:

```bash
pip install 'kindex[mcp,llm,reminders]'
pip install 'kindex[all]'
```

## Connect Your Agent

Install both the MCP server and the instruction file for each client you use:

```bash
# Claude Code
claude mcp add --scope user --transport stdio kindex -- kin-mcp
kin setup-claude-md --install
kin setup-hooks

# Codex
kin setup-codex-mcp
kin setup-codex-hooks
kin setup-agents-md --install --global

# Gemini CLI
kin setup-gemini-mcp
kin setup-gemini-md --install

# Google Antigravity
kin setup-antigravity-mcp
kin setup-antigravity-hooks
kin setup-antigravity-md --install

# OpenCode
kin setup-opencode-mcp
kin setup-agents-md --install --global

# Cursor
kin setup-cursor-mcp
kin setup-cursor-rules --install
```

After setup, start a fresh agent session and confirm the `kindex` MCP tools are
visible. The instruction files are what make agents use Kindex proactively:
start/resume a tag, search before adding, capture durable decisions, and end
the tag with a summary.

## Use Reminders

Reminders are stored in Kindex and fired by a checker. Creating a reminder does
not itself wake a running agent.

```bash
# Install periodic checks once
kin setup-cron

# Create a normal reminder
kin remind create "Check deploy" --at "in 30 minutes" --priority high

# Run due reminders manually
kin remind check
```

Wake reminders can start headless Codex or OpenCode follow-up turns when the
checker runs:

```bash
kin remind create "Continue rollout check" --at "in 10 minutes" \
  --wake codex --session last --cwd "$PWD" \
  --instructions "Check the rollout and fix any new failures."

kin remind create "Continue OpenCode build" --at "in 10 minutes" \
  --wake opencode --session last --cwd "$PWD" --wake-agent build \
  --instructions "Continue the build triage."
```

Boundary: these wakeups run `codex exec` or `opencode run` from the
daemon/cron context. They do not interrupt an idle terminal UI unless that host
adds a same-thread wake API.

## Keep Project Context Portable

If a repo tracks `.kin/`, treat it as shipped project state:

- Commit `.kin/config` when project policy, domains, or inheritance matter.
- Regenerate `.kin/index.json` with `kin index`; do not hand-edit conflicts.
- Regenerate `.kin/code-map.json` with `kin export code-map`.
- Keep private runtime data in `~/.kindex` or ignored `.kin/local`.

Run these before committing code that changes project structure:

```bash
kin ingest code --directory . --limit 10000
kin index
kin export code-map --directory . --project-name kindex --output .kin/code-map.json
```

## Release Surface Checklist

Before calling a release done, verify each public surface:

```bash
python3 -m pytest
mcp-publisher validate server.json
git describe --tags --exact-match HEAD
gh release view vX.Y.Z --repo jmcentire/kindex
python3 -m pip index versions kindex
curl -fsSL https://kindex.tools/ | grep 'vX.Y.Z'
curl -fsSL https://kindex.tools/.well-known/mcp/server-card.json | grep 'X.Y.Z'
```

If `mcp-publisher publish server.json` returns an expired-token error, refresh
the local registry login before publishing:

```bash
mcp-publisher login github
mcp-publisher publish server.json
```
