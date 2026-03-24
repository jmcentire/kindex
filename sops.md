# Operating Procedures

## Tech Stack
- Language: Python 3.12+
- Testing: pytest
- CLI: argparse (existing pattern in cli.py)
- Storage: SQLite + FTS5 (existing Store class in store.py)
- Config: kin.yaml via existing Config model

## Standards
- Type annotations on all public functions
- Follow existing patterns in daemon.py and graph.py — they are the stylistic reference
- snake_case functions, PascalCase classes
- `from __future__ import annotations` at top of every module
- TYPE_CHECKING guard for Store and Config imports
- All store operations go through the Store API — no direct SQL in new modules
- Prefer composition over inheritance

## Verification
- All functions must have at least one test
- Tests must be runnable without external services
- No task is done until its contract tests pass
- Mock `subprocess.run` for claude -p in deep mode tests
- Use existing test fixtures for in-memory Store

## Error Handling
- File lock contention: exit silently with return code 0
- LLM timeout in deep mode: log warning, skip, continue
- Corrupted node data: skip node, log warning, continue
- New modules must never crash the cron cycle or Stop hook

## Preferences
- Prefer stdlib over third-party libraries (difflib.SequenceMatcher for fuzzy matching)
- Keep files under 300 lines
- No new pip dependencies
- Reuse existing Store methods (orphans, fts_search, add_edge, update_node, etc.)
