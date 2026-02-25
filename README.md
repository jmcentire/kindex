# Kindex

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![v0.3.0](https://img.shields.io/badge/version-0.3.0-purple.svg)](https://github.com/jmcentire/kindex/releases)
[![Tests](https://img.shields.io/badge/tests-348%20passing-brightgreen.svg)](#)

**Knowledge index that learns from your conversations.**

Kindex is a persistent knowledge graph for AI-assisted workflows. It indexes your conversations, projects, and intellectual work so that Claude Code (or any AI coding assistant) never starts a session blind.

The CLI is `kin`.

## Install

```bash
pip install -e .
# or:
make install
```

With LLM-powered extraction:
```bash
pip install -e ".[llm]"
```

With everything (LLM + vectors):
```bash
pip install -e ".[all]"
make dev
```

## Quick Start

```bash
# Initialize a knowledge store
kin init

# Add knowledge
kin add "Stigmergy is coordination through environmental traces"

# Search with hybrid FTS5 + graph traversal
kin search stigmergy

# Get context for Claude Code injection
kin context --topic stigmergy --level full

# Ask questions (with question classification)
kin ask "How does weight decay work?"

# Track operational rules
kin add "Never break the API contract" --type constraint --trigger pre-deploy --action block

# Check status before deploy
kin status --trigger pre-deploy

# Ingest from your projects, sessions, and external sources
kin ingest all

# Install Claude Code hooks (one-time setup)
kin setup-hooks
kin setup-cron
```

## What It Does

Kindex maintains a graph of **nodes** (concepts, decisions, questions, skills, projects, sessions) connected by **weighted edges**. It provides:

- **Hybrid search** — FTS5 full-text + graph traversal, merged via Reciprocal Rank Fusion
- **Five-tier context** — full / abridged / summarized / executive / index, auto-selected by token budget
- **Operational nodes** — constraints, directives, checkpoints, watches that surface at the right time
- **Audience tenancy** — private / team / org / public scoping with PII stripping on export
- **Weight decay** — nodes and edges naturally fade unless accessed, keeping the graph fresh
- **Session learning** — ingests Claude Code sessions and project metadata automatically
- **Question classification** — procedural, decision, factual, and exploratory queries handled differently
- **Expertise detection** — auto-infers person skills from activity patterns
- **Bridge discovery** — finds cross-domain connections and suggests missing edges
- **.kin inheritance** — composable context chains across repos and teams

## Architecture

```
SQLite + FTS5          ← primary store and full-text search
  nodes: id, title, content, type, weight, audience, domains, extra
  edges: from_id, to_id, type, weight, provenance
  fts5:  content synced via triggers

Retrieval pipeline:
  FTS5 BM25 ──┐
  Graph BFS ──┼── RRF merge ── tier formatter ── context block
  (vectors) ──┘                   │
                          full │ abridged │ summarized │ executive │ index

Feedback loop:
  SessionStart ──> kin prime ──> context injected
  PreCompact  ──> kin compact-hook ──> knowledge captured
  Cron (30min) ──> kin cron ──> decay, ingest, health checks
```

### Node Types

**Knowledge**: concept, document, session, person, project, decision, question, artifact, skill

**Operational**: constraint (invariants), directive (soft rules), checkpoint (pre-flight checks), watch (attention flags)

### Context Tiers

| Tier | Budget | Use Case |
|------|--------|----------|
| full | ~4000 tokens | Session start, deep work |
| abridged | ~1500 tokens | Mid-session reference |
| summarized | ~750 tokens | Quick orientation |
| executive | ~200 tokens | Post-compaction re-injection |
| index | ~100 tokens | Existence check only |

Auto-selected via `--tokens` or set explicitly with `--level`.

## CLI Reference (42 commands)

### Core
| Command | Description |
|---------|-------------|
| `kin search <query>` | Hybrid FTS5 + graph search with RRF merging |
| `kin context` | Formatted context block for AI injection (--level, --tokens) |
| `kin add <text>` | Quick capture with auto-extraction and linking |
| `kin show <id>` | Full node details with edges, provenance, and state |
| `kin list` | List nodes (--type, --status, --mine, --limit) |
| `kin recent` | Recently active nodes |

### Knowledge Management
| Command | Description |
|---------|-------------|
| `kin learn` | Extract knowledge from sessions and inbox |
| `kin link <a> <b>` | Create weighted edge between nodes |
| `kin ask <question>` | Query the graph (question classification + LLM) |
| `kin alias <id> [add\|remove\|list]` | Manage AKA/synonyms for a node |
| `kin register <id> <path>` | Associate a file path with a node |
| `kin orphans` | Nodes with no connections |
| `kin trail <id>` | Temporal history and provenance chain |
| `kin decay` | Apply weight decay to stale nodes/edges |

### Graph Analytics
| Command | Description |
|---------|-------------|
| `kin graph [mode]` | Dashboard: stats, centrality, communities, bridges, trailheads |
| `kin suggest` | Review bridge opportunity suggestions (--accept, --reject) |
| `kin skills [person]` | Show skill profile and expertise for a person |
| `kin embed` | Index all nodes for vector similarity search |

### Operational
| Command | Description |
|---------|-------------|
| `kin status` | Graph health + operational summary (--trigger, --owner, --mine) |
| `kin set-audience <id> <scope>` | Set privacy scope (private/team/org/public) |
| `kin set-state <id> <key> <value>` | Set mutable state on directives/watches |
| `kin export` | Audience-aware graph export with PII stripping |
| `kin import <file>` | Import nodes/edges from JSON/JSONL (--mode merge/replace) |
| `kin sync-links` | Update node content with connection references |

### Ingestion & External Sources
| Command | Description |
|---------|-------------|
| `kin ingest <source>` | Ingest from: projects, sessions, files, commits, github, linear, all |
| `kin cron` | One-shot maintenance cycle (for crontab/launchd) |
| `kin watch` | Watch for new sessions and ingest them (--interval) |
| `kin analytics` | Archive session analytics and activity heatmap |
| `kin index` | Write .kin/index.json for git tracking |

### Claude Code Integration
| Command | Description |
|---------|-------------|
| `kin prime` | Generate context for SessionStart hook |
| `kin compact-hook` | Pre-compact knowledge capture |
| `kin setup-hooks` | Install Kindex hooks into Claude Code settings |
| `kin setup-cron` | Install periodic maintenance (launchd/crontab) |

### Infrastructure
| Command | Description |
|---------|-------------|
| `kin init` | Initialize data directory |
| `kin config [show\|get\|set]` | View or edit configuration |
| `kin migrate` | Import markdown topics into SQLite |
| `kin doctor` | Health check with graph enforcement (--fix) |
| `kin budget` | LLM spend tracking |
| `kin whoami` | Show current user identity |
| `kin changelog` | Show what changed (--since, --days, --actor) |
| `kin log` | Show recent activity log |
| `kin git-hook [install\|uninstall]` | Manage git hooks in a repository |

## External Integrations

### GitHub
Ingest issues, PRs, and commits via the `gh` CLI:
```bash
kin ingest github --repo owner/repo --since 2026-01-01
```

### Linear
Ingest issues via GraphQL API (requires `LINEAR_API_KEY`):
```bash
kin ingest linear --team ENG
```

### Git Hooks
Post-commit records commits, pre-push surfaces constraints:
```bash
kin git-hook install --repo-path /path/to/repo
```

### File Watching
SHA-256 change detection for registered files:
```bash
kin register my-node ./src/main.py
kin ingest files
```

## .kin Files

Place a `.kin` file in any repo root to declare its context:

```yaml
name: my-project
audience: team
domains: [engineering, python]
inherits:
  - ../platform/.kin
shared_with:
  - team: engineering
```

Inheritance chains resolve depth-first. Local values override ancestors. Lists are concatenated with dedup. Parent directories are auto-walked when no explicit `inherits` is set.

### Synonym Rings

Place `.syn` files in `<data_dir>/synonyms/` to define equivalent terms:

```yaml
ring: database-terms
synonyms:
  - database
  - db
  - datastore
  - persistence layer
```

## Configuration

Config is loaded from (first found):
1. `.kin` in current directory
2. `kin.yaml` in current directory
3. `~/.config/kindex/kin.yaml`

Or use `kin config` to read/write:

```bash
kin config show                    # show all
kin config get llm.enabled         # read a value
kin config set budget.daily 1.00   # write a value
```

Sample config (`kin.sample.yaml`):

```yaml
data_dir: ~/.kindex

llm:
  enabled: false
  model: claude-haiku-4-5-20251001
  api_key_env: ANTHROPIC_API_KEY

budget:
  daily: 0.50
  weekly: 2.00
  monthly: 5.00

project_dirs:
  - ~/Code
  - ~/Personal

defaults:
  hops: 2
  min_weight: 0.1
  mode: bfs
```

## Claude Code Integration

One-time setup:
```bash
kin setup-hooks    # installs SessionStart + PreCompact hooks
kin setup-cron     # installs 30-min maintenance cycle
```

Or add manually to `~/.claude/settings.json`:

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "kin context --level executive",
        "timeout": 5000
      }]
    }],
    "PreCompact": [{
      "matcher": "",
      "hooks": [{
        "type": "command",
        "command": "kin compact-hook --emit-context",
        "timeout": 10000
      }]
    }]
  }
}
```

## Development

```bash
make dev          # install with dev + LLM dependencies
make test         # run 348 tests
make test-verbose # run tests with full output
make lint         # check Python syntax
make check        # lint + test combined
make clean        # remove build artifacts
```

## License

MIT
