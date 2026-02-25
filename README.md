# Kindex

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![MIT License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![v0.4.2](https://img.shields.io/badge/version-0.4.2-purple.svg)](https://github.com/jmcentire/kindex/releases)
[![PyPI](https://img.shields.io/pypi/v/kindex.svg)](https://pypi.org/project/kindex/)
[![Tests](https://img.shields.io/badge/tests-429%20passing-brightgreen.svg)](#)
[![Claude Code Plugin](https://img.shields.io/badge/Claude%20Code-Plugin-orange.svg)](#install-as-claude-code-plugin)

**The memory layer Claude Code doesn't have.**

Kindex does one thing. It knows what you know.

It's a persistent knowledge graph for AI-assisted workflows. It indexes your conversations, projects, and intellectual work so that Claude Code never starts a session blind. Available as a **free Claude Code plugin** (MCP server) or standalone CLI.

> **Memory plugins capture what happened. Kindex captures what it means and how it connects.** Most memory tools are session archives with search. Kindex is a weighted knowledge graph that grows intelligence over time — understanding relationships, surfacing constraints, and managing exactly how much context to inject based on your available token budget.

## Install as Claude Code Plugin

Two commands. Zero configuration.

```bash
pip install kindex[mcp]
claude mcp add --scope user --transport stdio kindex -- kin-mcp
kin init
```

Claude Code now has 12 native tools: `search`, `add`, `context`, `show`, `ask`, `learn`, `link`, `list_nodes`, `status`, `suggest`, `graph_stats`, `changelog`.

Or add `.mcp.json` to any repo for project-scope access:
```json
{ "mcpServers": { "kindex": { "command": "kin-mcp" } } }
```

## Install as CLI

```bash
pip install kindex
kin init
```

With LLM-powered extraction:
```bash
pip install kindex[llm]
```

With everything (LLM + vectors + MCP):
```bash
pip install kindex[all]
```

## Why Kindex

### Context-aware by design
Five context tiers auto-select based on available tokens. When other plugins dump everything into context, Kindex gives you 200 tokens of executive summary or 4000 tokens of deep context — whatever fits. **Your plugin doesn't eat the context window.**

| Tier | Budget | Use Case |
|------|--------|----------|
| full | ~4000 tokens | Session start, deep work |
| abridged | ~1500 tokens | Mid-session reference |
| summarized | ~750 tokens | Quick orientation |
| executive | ~200 tokens | Post-compaction re-injection |
| index | ~100 tokens | Existence check only |

### Knowledge graph, not log file
Nodes have types, weights, domains, and audiences. Edges carry provenance and decay over time. The graph understands what matters — not just what was said.

### Operational guardrails
Constraints block deploys. Directives encode preferences. Watches flag attention items. Checkpoints run pre-flight. No other memory plugin has this.

### Team and org ready
`.kin` inheritance chains let a service repo inherit from a platform context, which inherits from an org voice. Private/team/org/public scoping with PII stripping on export. Enterprise-ready from day one.

## In Practice

A 162-file fantasy novel vault — characters, locations, magic systems, plot outlines — ingested in one pass. Cross-referenced by content mentions. Searched in milliseconds.

```
$ kin status
Nodes:     192
Edges:     11,802
Orphans:   3

$ time kin search "the Baker"
# Kindex: 10 results for "the Baker"

## [document] The Baker - Hessa's Profile and Message Broker System (w=0.70)
  → Thieves Guild, Five Marks, Thieves Guild Operations

## [person] Mia and The Baker (Hessa) -- Relationship (w=0.70)
  → Sebastian and Mia, Mia -- Motivations and Goals

0.142 total

$ kin graph stats
Nodes:      192
Edges:      11,802
Density:    0.3218
Components: 5
Avg degree: 122.94
```

192 nodes. 11,802 edges. 5 context tiers. Hybrid FTS5 + graph traversal in 142ms.

## Quick Start

```bash
# Add knowledge
kin add "Stigmergy is coordination through environmental traces"

# Search with hybrid FTS5 + graph traversal
kin search stigmergy

# Ask questions (with automatic classification)
kin ask "How does weight decay work?"

# Get context for AI injection
kin context --topic stigmergy --level full

# Track operational rules
kin add "Never break the API contract" --type constraint --trigger pre-deploy --action block

# Check status before deploy
kin status --trigger pre-deploy

# Ingest from all sources
kin ingest all
```

## .kin Voice & Inheritance

Companies publish `.kin` files that encode their communication style, engineering standards, and values. Teams inherit from orgs. Repos inherit from teams. The knowledge graph carries the voice forward.

```
~/.kindex/voices/acme.kin         # Org voice (downloadable, public)
    ^
    |  inherits
~/Code/platform/.kin              # Platform team context
    ^
    |  inherits
~/Code/payments-service/.kin      # Service-specific context
```

```yaml
# payments-service/.kin
name: payments-service
audience: team
domains: [payments, python]
inherits:
  - ../platform/.kin
```

The payments service gets Acme's voice principles, the platform's engineering standards, AND its own domain context. Local values override ancestors. Lists merge with dedup. Parent directories auto-walk when no explicit `inherits` is set.

See [examples/kin-voices/](examples/kin-voices/) for ready-to-use voice templates.

## Architecture

```
SQLite + FTS5          <- primary store and full-text search
  nodes: id, title, content, type, weight, audience, domains, extra
  edges: from_id, to_id, type, weight, provenance
  fts5:  content synced via triggers

Retrieval pipeline:
  FTS5 BM25 --+
  Graph BFS --+-- RRF merge -- tier formatter -- context block
  (vectors) --+                   |
                          full | abridged | summarized | executive | index

Two integration paths:
  MCP plugin --> Claude calls tools natively (search, add, learn, ...)
  CLI hooks  --> SessionStart / PreCompact / Stop lifecycle events
```

### Node Types

**Knowledge**: concept, document, session, person, project, decision, question, artifact, skill

**Operational**: constraint (invariants), directive (soft rules), checkpoint (pre-flight), watch (attention flags)

## CLI Reference (42 commands)

### Core
| Command | Description |
|---------|-------------|
| `kin search <query>` | Hybrid FTS5 + graph search with RRF merging |
| `kin context` | Formatted context block for AI injection (--level, --tokens) |
| `kin add <text>` | Quick capture with auto-extraction and linking |
| `kin show <id>` | Full node details with edges, provenance, and state |
| `kin list` | List nodes (--type, --status, --mine, --limit) |
| `kin ask <question>` | Question classification + LLM or context answer |

### Knowledge Management
| Command | Description |
|---------|-------------|
| `kin learn` | Extract knowledge from sessions and inbox |
| `kin link <a> <b>` | Create weighted edge between nodes |
| `kin alias <id> [add\|remove\|list]` | Manage AKA/synonyms for a node |
| `kin register <id> <path>` | Associate a file path with a node |
| `kin orphans` | Nodes with no connections |
| `kin trail <id>` | Temporal history and provenance chain |
| `kin decay` | Apply weight decay to stale nodes/edges |
| `kin recent` | Recently active nodes |

### Graph Analytics
| Command | Description |
|---------|-------------|
| `kin graph [mode]` | Dashboard: stats, centrality, communities, bridges, trailheads |
| `kin suggest` | Bridge opportunity suggestions (--accept, --reject) |
| `kin skills [person]` | Skill profile and expertise for a person |
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

### Infrastructure
| Command | Description |
|---------|-------------|
| `kin init` | Initialize data directory |
| `kin config [show\|get\|set]` | View or edit configuration |
| `kin setup-hooks` | Install lifecycle hooks into Claude Code |
| `kin setup-cron` | Install periodic maintenance (launchd/crontab) |
| `kin doctor` | Health check with graph enforcement (--fix) |
| `kin migrate` | Import markdown topics into SQLite |
| `kin budget` | LLM spend tracking |
| `kin whoami` | Show current user identity |
| `kin changelog` | What changed (--since, --days, --actor) |
| `kin log` | Recent activity log |
| `kin git-hook [install\|uninstall]` | Manage git hooks in a repository |
| `kin prime` | Generate context for SessionStart hook |
| `kin compact-hook` | Pre-compact knowledge capture |

## Configuration

Config is loaded from (first found):
1. `.kin` in current directory
2. `kin.yaml` in current directory
3. `~/.config/kindex/kin.yaml`

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

## Development

```bash
make dev          # install with dev + LLM dependencies
make test         # run 373 tests
make check        # lint + test combined
make clean        # remove build artifacts
```

## License

MIT

<!-- mcp-name: io.github.jmcentire/kindex -->
