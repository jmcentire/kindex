# Kindex — Knowledge Graph for AI-Assisted Workflows

Kindex is a persistent knowledge graph that indexes conversations, projects, and intellectual work so that Claude Code never starts a session blind. The CLI is `kin`.

## Running Tests

```bash
# All tests
pytest tests/ -v

# Specific module
pytest tests/test_cli.py -v
pytest tests/test_hooks.py -v
pytest tests/test_store.py -v

# With coverage
pytest tests/ --cov=kindex --cov-report=term-missing
```

## Key File Locations

| Path | Purpose |
|------|---------|
| `src/kindex/cli.py` | CLI entry point and all `kin` subcommands |
| `src/kindex/store.py` | SQLite + FTS5 storage layer |
| `src/kindex/config.py` | Configuration loading and `Config` model |
| `src/kindex/graph.py` | Graph analytics (centrality, communities, bridges) |
| `src/kindex/retrieve.py` | Hybrid search (FTS5 + graph BFS + RRF merge) |
| `src/kindex/extract.py` | Knowledge extraction from text and sessions |
| `src/kindex/ingest.py` | Project and session ingestion pipeline |
| `src/kindex/sessions.py` | Session tag lifecycle (start, update, segment, resume) |
| `src/kindex/hooks.py` | Claude Code hook handlers (prime, compact) |
| `src/kindex/setup.py` | System setup (Claude hooks, launchd, crontab) |
| `src/kindex/daemon.py` | Background daemon and cron cycle |
| `src/kindex/budget.py` | LLM spend tracking and budget enforcement |
| `src/kindex/vectors.py` | Vector embedding and similarity search |
| `src/kindex/vault.py` | Markdown vault import/export |
| `src/kindex/models.py` | Core data models |
| `src/kindex/schema.py` | Database schema and migrations |
| `src/kindex/llm.py` | LLM provider abstraction |
| `tests/` | All tests (pytest) |
| `~/.kindex/` | Default data directory (SQLite DB, logs) |
| `~/.config/kindex/kin.yaml` | User config |

## Session Directives

When working in this codebase, follow these practices:

- **Capture new knowledge**: When you learn something significant, use `kin add "<insight>" --tags <domain1>,<domain2>` to persist it with tags.
- **Create links**: When you discover a relationship between concepts, use `kin link <a> <b> <relationship> --why "<reason>"` to connect them.
- **Track decisions**: Use `kin add "<decision>" --type decision --tags <project>` for architectural or design decisions.
- **Record constraints**: Use `kin add "<rule>" --type constraint --trigger <event> --action <verify|warn|block>` for invariants.
- **Flag attention items**: Use `kin add "<item>" --type watch --owner <person> --expires <date>` for things that need monitoring.
- **Search before adding**: Use `kin search <term>` or `kin search <term> --tags <domain>` to check if knowledge already exists before duplicating.
- **Filter by tags**: Use `kin list --tags <tag1>,<tag2>` to find nodes by tag (AND logic). Tags supplement auto-categorization.
- **Check status**: Use `kin status` to see graph health and active operational nodes.
- **Session tags**: Use `kin tag start <name> --focus "what you're working on"` to create a named session handle. Update with `kin tag update --focus "..."` or `kin tag segment --focus "new topic" --summary "what happened"`. Resume in a new session with `kin tag resume <name>`. End with `kin tag end --summary "..."`. See `kin tag list` for all tags.

## Auto-Context Loading

At the start of each session, load relevant context:

```bash
kin prime --for hook
```

This auto-detects the current project from `$PWD` and outputs a context block with relevant knowledge, active constraints, and recent changes.

## Release Checklist

When asked to release, follow these steps exactly. Do NOT install twine or attempt manual PyPI upload — it's fully automated.

1. Run full test suite: `pytest tests/ -v`
2. Bump version in `pyproject.toml`, `src/kindex/__init__.py`, and README badge
3. Commit version bump and all changes
4. Push to main: `git push origin main`
5. Tag: `git tag vX.Y.Z && git push origin vX.Y.Z`
6. Create GitHub release: `gh release create vX.Y.Z --title "..." --notes "..."`
7. **Done.** GitHub Actions (`.github/workflows/publish.yml`) handles PyPI publish automatically via trusted publishing (OIDC) on tag push. The `sync-version` pre-commit hook auto-updates docs/index.html badges.

## Architecture Notes

- Storage: SQLite with FTS5 full-text search, triggers for index sync
- Search: Hybrid retrieval combining FTS5 BM25 scores, graph BFS traversal, and optional vector similarity, merged via Reciprocal Rank Fusion (RRF)
- Context tiers: full (~4000 tokens), abridged (~1500), summarized (~750), executive (~200), index (~100) -- auto-selected by budget
- Node types: concept, document, session, person, project, decision, question, artifact, skill, constraint, directive, checkpoint, watch
- Edge types: relates_to, depends_on, derived_from, contradicts, etc. with weights and provenance
- Audience: private / team / public scoping with export boundary enforcement
- Weight decay: Nodes and edges naturally fade unless accessed, keeping the graph fresh
