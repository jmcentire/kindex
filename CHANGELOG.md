# Changelog

All notable changes to Kindex are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.15.0] - 2026-03-24

### Added
- YAML frontmatter on all skill SKILL.md files (kindex-capture, kindex-learn, kindex-prime) so they register properly when loaded as a Claude Code plugin
- `UserPromptSubmit` hook in plugin hooks.json (migrated from global settings)

### Changed
- Plugin version synced with package version (was 0.4.0, now matches 0.15.0)

## [0.14.1] - 2026-03-24

### Added
- `dream` MCP tool — knowledge consolidation available natively in Claude Code sessions
- CHANGELOG.md — retroactive changelog covering all releases from 0.4.0

## [0.14.0] - 2026-03-24

### Added
- **Dream cycle** (`kin dream`) — post-session knowledge consolidation: fuzzy dedup, suggestion auto-apply, domain edge strengthening
- `dream_deep.py` — LLM-powered cluster summarization via `claude -p` (structurally separated)
- `dream` MCP tool — consolidation available natively in Claude Code sessions
- Dream integrated into cron cycle (step 11, lightweight mode)
- Stop hook spawns detached dream on session exit (`start_new_session=True`)
- File locking (`fcntl.flock`) prevents concurrent dream cycles
- Pact contract-first artifacts (task.md, sops.md, constraints.yaml)
- 32 new tests (980 total)

## [0.13.0] - 2026-03-24

### Added
- `.kin/` directory migration — old `.kin` config files auto-upgrade to `.kin/config`
- Repo-scoped index writes (filter by code-mod/code-sym prefix)

### Fixed
- `.kin` directory vs config file collision in `config.py`

## [0.12.0] - 2026-03-23

### Added
- Multi-provider embedding support (local sentence-transformers, OpenAI, Gemini)
- Configurable via `embedding.provider` in kin.yaml

## [0.11.0] - 2026-03-22

### Added
- **Conversation modes** — reusable session-priming artifacts based on research (5.4x improvement over direct instruction)
- Five built-in modes: collaborate, code, create, research, chat
- `kin mode [activate|list|show|create|export|import|seed]`
- PII-free export for team sharing

## [0.10.0] - 2026-03-21

### Added
- **Code adapter** — ingest repository structure via ctags, cscope, tree-sitter
- Module nodes (artifact) with structural summaries
- Symbol nodes (concept) with method signatures
- Import/inheritance/call-graph edges
- Incremental re-ingest via file hashing

### Fixed
- `.kin/` directory collision with `.kin` config file

## [0.9.0] - 2026-03-18

### Added
- `--tags` support for `add`, `search`, and `list` across CLI and MCP
- Task lifecycle with graph-connected work items
- Slow graph archive with rotation (50MB or 365d)
- Graph health tools (`graph_heal`, `graph_merge`, `suggest`)
- Mechanical smoke tests (258 tests, 34 files via Pact adopt)
- Claude-web adapter for ingesting Claude.ai conversations

### Fixed
- Missing mcp dependency error now shows graceful message
- CI installs mcp dependency for smoke tests

## [0.7.0] - 2026-03-14

### Added
- **Reminders** — natural language scheduling, recurring rules, multi-channel notifications
- Actionable reminders with shell commands and `claude -p` execution
- Stop guard — blocks session exit when actionable reminders pending
- Adaptive scheduling (launchd/crontab interval adjusts to nearest reminder)

## [0.5.0] - 2026-03-10

### Added
- **Session tags** — named work context handles replacing resume files
- `kin tag [start|update|segment|pause|end|resume|list|show]`
- 16 MCP tools

## [0.4.2] - 2026-03-08

### Added
- Cache-optimized LLM retrieval — three-tier prompt architecture with Anthropic caching
- Layered config (global -> local merge, like git config)
- Adapter protocol for extensible ingestion with entry-point discovery

### Fixed
- FTS5 search failing on natural language questions
- Terminal block formatting (white-space: pre)

## [0.4.1] - 2026-03-07

### Fixed
- Schema migration for existing databases
- Server.json description within 100-char registry limit

## [0.4.0] - 2026-03-06

### Added
- Initial public release
- SQLite + FTS5 knowledge graph
- Hybrid search (FTS5 + graph BFS + RRF merge)
- Five context tiers (full, abridged, summarized, executive, index)
- MCP server for Claude Code integration
- CLI with core commands (search, add, context, show, list, ask)
- Node types: concept, document, session, person, project, decision, question, artifact, skill
- Operational types: constraint, directive, checkpoint, watch
- Weight decay and audience scoping
