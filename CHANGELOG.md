# Changelog

All notable changes to Kindex are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [0.26.0] - 2026-06-27

### Added
- **Structured merge driver for `.kin` artifacts.** `.kin/index.json` and `.kin/code-map.json` are generated, id-keyed JSON snapshots — git's line-based merge conflicts on them needlessly. The new `kin merge-kin` git merge driver does a structured 3-way **union** instead: for `index.json`, union nodes by id (newer `updated_at` wins, base detects deletions) and recompute the derived header; for `code-map.json`, union nodes/edges/layer members and recompute the tour. This is lossless across machines (regenerating `index.json` from one machine's local DB would drop the other branch's nodes), and the result is byte-identical to what `kin index` would emit, so a later regeneration produces no spurious diff. Install per repo with `kin setup-merge`, which registers the driver in `.git/config` and points `.kin/index.json` / `.kin/code-map.json` at it via `.gitattributes` (repos without the driver registered fall back to git's default merge).

### Changed
- `.kin/index.json` no longer carries a volatile `source_updated_at` timestamp. It changed on every regeneration — churning git history and conflicting on every concurrent merge — while the commit time already records snapshot freshness and each node keeps its own `updated_at`.

## [0.25.6] - 2026-06-27

### Fixed
- Client scoping now also covers the pull-based context surfaces. `format_context_block`'s full/abridged tiers drop operational nodes — constraints, watches, directives — scoped to a different client when a client is known, so the MCP `context`/`ask` tools no longer surface (for example) an Antigravity-scoped constraint to a Claude session. The MCP server resolves its client from the `KIN_CLIENT` environment variable (a per-client MCP config can set it); unset means no scoping — the unchanged default. Human-facing `kin context` / `kin status` continue to show every node.
- `prime`'s 24h "Recent activity" section no longer echoes the titles of nodes scoped to a different client; the aggregate activity counts remain complete.

## [0.25.5] - 2026-06-27

### Fixed
- **Attention and context injections are now scoped to the running agent client.** A graph node tagged for a specific client — e.g. an `antigravity` directive documenting Antigravity's nested `toolCall`/`toolCall.args` PreToolUse hook protocol — previously surfaced as a tool-boundary advisory and as SessionStart context in *every* client, so Claude and Codex received instructions about a hook schema they do not use. The running client is now threaded from the hook through both the synchronous and asynchronous (queue → drain, including the status-retry hop) attention pipeline into candidate selection, and through `prime` / `agent-prime-hook` SessionStart context, so any node scoped to a different client is dropped.
- Client scope is declared with an explicit `client:<name>` / `agent:<name>` tag (authoritative for any known client) or a bare tag for a coined client name (`antigravity`, `opencode`). Names that double as topical subjects — `claude`, `codex`, `gemini`, `cursor`, and the 2-char `ag` alias — are **not** inferred from a bare tag, so a node tagged `gemini` about the Gemini API is never hidden from a Claude session. Nodes that name no client are unaffected and surface everywhere. A `plain`/unlabeled hook caller scopes as Claude (the default install).

### Performance
- Node embedding is deferred off the `add`/`edit`/`supersede` hot path, so those operations return without blocking on vector generation (#9).

## [0.25.4] - 2026-06-23

### Fixed
- `learn` (MCP) no longer creates unlinked, title-only concept nodes that inflated the orphan count. The keyword-extraction fallback emits content-less concepts whose connections never resolve to edges; these accumulated as orphans on repeated ingestion. `learn` now rejects low-information concepts (no content and no domains) and grounds every surviving concept to a freshly created source node via `context_of` edges, so extracted concepts can never orphan.
- Flaky test `test_capture_session_end_with_existing_nodes` is now deterministic — it mocks extraction to exercise the dedup path instead of depending on live LLM output.

## [0.25.3] - 2026-06-14

### Fixed
- Attention hooks now return within a bounded internal deadline instead of consuming Codex's 5-second hook timeout while waiting on LLM arbitration.
- Slow attention reviews are queued asynchronously and injected later only when the result remains relevant to the conversation.
- Hook setup now migrates attention commands to `--deadline-ms 3500`, leaving room inside the host hook timeout for process and SQLite overhead.

## [0.25.2] - 2026-06-13

### Fixed
- Antigravity quiet-mode prompt checks now preserve the Antigravity hook protocol instead of emitting a Claude `hookSpecificOutput` envelope that agy rejects.
- macOS system reminder delivery now honors `reminders.channels.system.enabled=false`, so disabling system notifications actually suppresses desktop popups.
- Codex no longer receives unsupported `suppressOutput` fields from Kindex quiet-mode hook output; Codex still receives the required SessionStart prime context.

## [0.25.0] - 2026-06-12

### Added
- **Google Antigravity support** — `kin setup-antigravity-mcp` writes Kindex MCP config to both Antigravity global MCP config locations (`~/.gemini/config/mcp_config.json` and `~/.gemini/antigravity-cli/mcp_config.json`), while `kin setup-antigravity-hooks` installs PreInvocation context priming, PreToolUse attention and config-write permission gating, and Stop-time reinforcement enqueue.
- **Agent adapter layer** — hook output and tool payload translation now lives behind a client adapter boundary, with Antigravity `injectSteps`, PreToolUse `allow`/`force_ask`, and nested `toolCall` payload parsing alongside existing Claude/Codex envelopes.
- **Per-agent tuning** — `agents.clients.<client>` and `agents.instances.<client>:<instance>` overlays can tune Kindex behavior by client family or individual conversation/instance. `kin agent-config show|set` writes only approved behavior keys (`attention.*`, `sim.*`, `collab.*`, `hooks.prime_tokens`) so agents can propose tuning through the host permission flow without silently mutating arbitrary config.

### Changed
- Agent setup docs now cover Claude Code, Codex, Gemini CLI, Google Antigravity, OpenCode, and Cursor consistently across README, `/docs`, and `kindex.tools` surfaces.

## [0.23.0] - 2026-06-09

### Added
- **Grounded Sim** — the supervisory check-in now reviews WITH relevant graph context instead of blind. `build_sim_grounding` injects top related concepts/decisions (hybrid search) plus active constraints/watches into the supervisor prompt, deduped and char-capped by `sim.grounding_chars` (default `1500`; `0` disables). Sim can now flag a constraint being violated, a known watch, or a decision being contradicted that the conversation window alone wouldn't reveal. (Outcome of a Sim-vs-multi-lens review experiment: grounding the single persona beat building a panel.)

### Fixed
- Hermetic test fixture now covers the default embedding provider (Voyage): provider keys are derived from `vectors.PROVIDER_DEFAULTS`, so `VOYAGE_API_KEY` (and future providers) can't leak in from the environment and trigger live embedding calls during tests.

## [0.22.0] - 2026-06-08

### Added
- **Codex SessionStart parity** — `kin setup-codex-hooks` now installs a SessionStart hook so Codex sessions begin with the same auto-primed context and "use kindex" directive as Claude Code. `kin prime` gained `--adapter {plain,claude,codex}`; `--adapter codex` emits the `hookSpecificOutput.additionalContext` envelope Codex ingests.
- **`reminders.remind_kindex_usage`** (default `true`) — toggle the injected "use kindex" session directive; set `false` per-project in `.kin/config [reminders]` to suppress the nudge.
- **Project-graph (`.kin/`) guidance** in the session directive — agents are told to discover the `.kin/` directory for the files they touch (not just the cwd root) and to stage/commit `.kin/` changes alongside the related code.
- **Stigmergic pheromone ranking and session-end reinforcement** — injection trails (deposit / reinforce / decay) feed an auto-ramping ranking signal, and an opt-in session-end grader reinforces the injections the agent actually used (`attention.pheromone_*`, `attention.reinforce_*`).
- **Sim supervisory check-in** (opt-in) — an async supervisor that periodically reviews the conversation window and surfaces guidance through the attention channel (`kin sim`, `[sim]` config).

### Fixed
- Test suite is now hermetic: provider API keys no longer leak from the ambient environment into tests, fixing a flaky extraction-dedup test and preventing accidental live-API calls (and spend) during `pytest`.

## [0.21.3] - 2026-05-30

### Changed
- Generated `.kin/index.json` now uses canonical stable ordering, sorted domains/JSON keys, and source-derived time metadata instead of wall-clock generation time.
- Code-map export now sorts nodes and edges canonically and derives `analyzedAt` from Git commit time or latest code-node time.
- Current-user provenance now prefers repo-local Git `user.name`, then global Git `user.name`, then OS username.

### Fixed
- Repeated `.kin` snapshot exports of unchanged source no longer churn Git diffs because of run-time timestamps or unstable ordering.

## [0.21.2] - 2026-05-28

### Fixed
- Lightweight dream no longer repeatedly scans and sorts the pending suggestion backlog while creating duplicate suggestions.
- Scheduled dream runs now cap new suggestion writes per run with `reminders.dream_max_new_suggestions` (default `100`).

### Changed
- Schema v6 adds suggestion indexes for recent pending reads and pair-existence checks.
- Dream duplicate detection skips content similarity work when title similarity is too low to meet the configured threshold.

## [0.20.0] - 2026-05-19

### Added
- Short-lived agent coordination plane with `coord_start`, `coord_post`, `coord_read`, `coord_list`, and `coord_end` MCP tools plus matching `kin coord` CLI commands.
- Expiring task claims with `task_claim` and `task_release` MCP tools plus `kin task claim`, `kin task release`, and cleanup support.

### Changed
- MCP agent guidance now distinguishes operational coordination messages from durable knowledge capture.
- Task formatting shows active claim ownership when present.
- Default embedding provider is now Voyage so vector search uses the pure-HTTP first-class provider by default; local `sentence-transformers` remains available via `embedding.provider: local`.

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
