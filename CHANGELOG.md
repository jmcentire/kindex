# Changelog

All notable changes to Kindex are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.19.0] - 2026-05-15

### Added
- Project-scoped config resolution via explicit `--project-path`, `KIN_PROJECT`, git worktree root, then cwd.
- `work_policy` config model and `kin policy [show|check]` for opt-in project policy enforcement.
- Git hook install now adds a pre-commit policy check and pre-push policy check before surfacing constraints.
- `.kin/.gitignore` pattern for tracking project context while ignoring local/private runtime state.

### Changed
- `.kin/config` is now treated as a git-shipped project contract rather than local-only cache.
- MCP agent guidance now tells agents to read tracked `.kin/config`, check policy when shell access exists, and enforce Linear only when the repo opts in.
- MCP server metadata updated to current package version and tracked `.kin` behavior.

## [0.18.0] - 2026-05-03

### Added
- Codex support: `kin setup-codex-mcp` registers `kin-mcp` in `~/.codex/config.toml`.
- Codex-facing `AGENTS.md` directives via `kin setup-agents-md`, including proactive search, capture, tasks, and session lifecycle guidance.
- `codex-sessions` adapter for ingesting saved Codex JSONL sessions from `~/.codex/sessions`.
- Agent-facing MCP usage guide at `docs/mcp-agent-guide.md`.

### Changed
- README, static docs, privacy copy, and MCP server description now present Kindex as an MCP memory layer for Claude Code, Codex, and other MCP-capable agents.

## [0.17.0] - 2026-04-11

### Added
- **Voyage AI embedding provider** (`vectors.py::_embed_voyage`) — Anthropic's officially recommended embeddings provider. Pure-HTTP via `urllib.request`, no native dependencies. Default model `voyage-3.5` (1024-dim), supports `voyage-3-large`, `voyage-3.5-lite`, `voyage-finance-2`, `voyage-law-2`, `voyage-code-3`. Configure via `embedding.provider: voyage` in `kin.yaml` and set `VOYAGE_API_KEY` in the environment. Generous free tier (200M tokens) makes it effectively free for typical use.

### Changed
- **`vectors` extra no longer pulls `sentence-transformers`.** The code already handled the import gracefully (try/except ImportError with fallback to FTS5), but the pyproject declaration was unconditionally installing it and transitively pulling `torch` and `scikit-learn`. On macOS, those two wheels ship incompatible `libomp.dylib` install names and crash Python with an OpenMP duplicate-registration abort when both load together. Users who want local embeddings now opt in explicitly: `pip install sentence-transformers`. API-based providers (voyage, openai, gemini) are the first-class path and require no native deps.
- `all` extra similarly no longer pulls `sentence-transformers`.

### Fixed
- `__version__` in `src/kindex/__init__.py` was stuck at 0.16.1 despite 0.16.2 shipping; now synced to 0.17.0.
- README version badge was stuck at 0.16.1; now 0.17.0.

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
