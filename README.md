# Kindex

**Knowledge index that learns from your conversations.**

Kindex is a persistent knowledge graph for AI-assisted workflows. It indexes your conversations, projects, and intellectual work so that Claude Code (or any AI coding assistant) never starts a session blind.

The CLI is `kin`.

## Install

```bash
make install
# or directly:
pip install -e .
```

With LLM-powered extraction:
```bash
pip install -e ".[llm]"
```

## Quick Start

```bash
# Initialize a knowledge store
kin init

# Add knowledge
kin add "Stigmergy is coordination through environmental traces"

# Search
kin search stigmergy

# Get context for Claude Code injection
kin context --topic stigmergy --level full

# Track operational rules
kin add "Never break the API contract" --type constraint --trigger pre-deploy --action block

# Check status before deploy
kin status --trigger pre-deploy

# Ingest from your projects and sessions
kin ingest all

# View/edit config
kin config show
kin config set llm.enabled true
```

## What It Does

Kindex maintains a graph of **nodes** (concepts, decisions, questions, skills, projects, sessions) connected by **weighted edges**. It provides:

- **Hybrid search** — FTS5 full-text + graph traversal, merged via Reciprocal Rank Fusion
- **Five-tier context** — full / abridged / summarized / executive / index, auto-selected by token budget
- **Operational nodes** — constraints, directives, checkpoints, watches that surface at the right time
- **Audience tenancy** — private / team / public scoping with export boundary enforcement
- **Weight decay** — nodes and edges naturally fade unless accessed, keeping the graph fresh
- **Session learning** — ingests Claude Code sessions and project metadata automatically
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
  (vectors) ──┘                   ↓
                          full | abridged | summarized | executive | index
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

## CLI Reference

### Core
| Command | Description |
|---------|-------------|
| `kin search <query>` | Hybrid FTS5 + graph search |
| `kin context` | Formatted context block for AI injection |
| `kin add <text>` | Quick capture with auto-extraction |
| `kin show <id>` | Full node with edges and provenance |
| `kin list` | List all nodes (--type, --status, --limit) |
| `kin recent` | Recently active nodes |

### Knowledge Management
| Command | Description |
|---------|-------------|
| `kin learn --from-inbox` | Process inbox items |
| `kin link <a> <b>` | Create edge between nodes |
| `kin orphans` | Nodes with no connections |
| `kin trail <id>` | Temporal history and provenance |
| `kin decay` | Apply weight decay |

### Operational
| Command | Description |
|---------|-------------|
| `kin add --type constraint` | Add invariant rule |
| `kin add --type watch` | Add attention flag |
| `kin status` | Graph health + operational summary |
| `kin status --trigger pre-deploy` | Pre-flight checklist |
| `kin set-audience <id> <scope>` | Set privacy scope |
| `kin export` | Audience-aware graph export |

### Infrastructure
| Command | Description |
|---------|-------------|
| `kin init` | Initialize data directory |
| `kin config show` | Show current configuration |
| `kin config get <key>` | Read a config value |
| `kin config set <key> <value>` | Write a config value |
| `kin migrate` | Import markdown topics |
| `kin doctor` | Health check |
| `kin budget` | LLM spend tracking |
| `kin ingest <source>` | Scan projects/sessions |
| `kin compact-hook` | Pre-compact capture |

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

Inheritance chains resolve depth-first. Local values override ancestors. Lists are concatenated with dedup.

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

Add to `~/.claude/settings.json`:

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

## License

MIT
