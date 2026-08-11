# Product Specification — run batch0 (kindex ship-now reliability batch)

Source: TASK.md (verbatim, digest 2ae1e527772d…, in run.json). Research ground: kindex
nodes 4967010c901e (defect inventory), 9bd012d3c92a (adversarial review), b8fb6698bca1
(decided collection), c24b1c963151 (Stop-hook watch). Ratified in local AI-verdict mode
per ~/.claude/commands/validate.md; no human signature — stated here and in the verdict.

Version target: kindex 0.30.0. Hard invariant for the whole run: **no database schema
change** (SCHEMA_VERSION stays 7); every fix is behavior-level and additive.

## S1 — End-of-session capture works (Stop hook)

Defect: the installed Stop hook runs `kin compact-hook --text "Session ended"`; the
`--text` argument preempts the stdin hook envelope, so the transcript is never read and
extraction runs on the 13-character literal (may spend an LLM call).

Required observable behavior:
- R1.1 When stdin carries a parseable JSON object with `hook_event_name` and
  `transcript_path` (the Claude Code hook envelope), `kin compact-hook` MUST use the
  envelope — reading the transcript at `transcript_path` for extraction — even when
  `--text` is also supplied. `--text` remains the effective input only when stdin is not
  a parseable envelope.
- R1.2 `kin setup` MUST install the Stop hook entry **without** `--text`, and re-running
  setup on a machine with the old broken entry MUST replace it (re-run-is-the-migration,
  the issue-#15 pattern). The replacement must rebuild the full hook command entry —
  the existing-entry matcher replaces whole entries, so sibling commands in the same
  entry (stop-guard, dream, reinforce enqueue) must be preserved exactly.
- R1.3 With an envelope but a missing/unreadable transcript file: no extraction, no LLM
  call, no minted nodes, exit 0.
- R1.4 No node with empty content is ever minted by this path (issue-#14 invariant holds).
- R1.5 Transcript tolerance under concurrency: a transcript containing malformed or
  truncated JSONL lines (mid-write when the Stop hook fires) MUST be handled by skipping
  the bad lines and extracting from the parseable remainder — never a crash, never zero
  output solely because the final line was partial. A transcript deleted between envelope
  parse and read falls under R1.3.

## S2 — Memory failure degrades the turn, never crashes it (hooks + MCP)

Defect: cli `main()` catches only `ProfileMismatchError`; any other failure (corrupt DB,
locked file, schema mismatch) tracebacks with nonzero exit on every hook event; nothing
records that priming/capture failed. In `prime_context`, the core `hybrid_search` and
operational-summary calls are unshielded, so one bad node zeroes the entire prime.

Required observable behavior:
- R2.1 Hook-surface subcommands (the prime, compact-hook, prompt-check/stop-guard,
  attention-hook, cron family — exactly the set invoked from installed hook entries and
  schedulers, enumerated in the architecture) MUST catch all exceptions: emit a one-line
  degraded output appropriate to the hook (prime → a single `# kindex degraded: <ErrClass>
  — session starting without memory context` line; guard-type hooks → empty fail-open
  output; compact-hook → nothing) and **exit 0**. Non-hook commands keep full tracebacks.
- R2.2 Every degraded event MUST be appended as one JSON line to `degraded.jsonl` in the
  **base** (pre-profile) data directory — a pure file append that works when SQLite is
  what broke — carrying at least {ts, cmd, profile, profile_source, error_class,
  msg (truncated ≤200 chars)}. The file is size-capped: over 1 MB, rewrite keeping the
  last 200 lines.
- R2.3 `kin status` and `kin doctor` MUST surface the degraded count for the last 7 days
  with the most recent event (cmd + error class); doctor treats count > 0 as a warning.
  Absent file = zero events, no warning.
- R2.4 The two unshielded core calls inside `prime_context` MUST be individually
  shielded so partial priming yields partial context, never an empty prime.
- R2.5 On the MCP surface, a Store open/init failure MUST yield the tool-result string
  `Error: memory unavailable (<ErrClass>)` (and a degraded.jsonl event) instead of an
  unhandled exception.
- R2.6 Degraded-ledger appends are concurrency-safe: each event is written as one
  single-write line append (O_APPEND semantics), so two hooks failing simultaneously
  both land without interleaving. The size-cap rewrite must not lose a concurrent
  append silently — best effort is acceptable, torn lines are not.

## S3 — Retired knowledge stays retired (search fence + honest top_k)

Defect: `fts_search` excludes only `superseded`, so archived nodes (360 junk in the live
graph) remain first-class FTS/vector candidates; context formatters pull recent decisions
with no status filter; `hybrid_search` drops expired/superseded entries after slicing
top_k, silently returning short result sets.

Required observable behavior:
- R3.1 Default search (FTS, hybrid, MCP search, CLI search, hook priming) MUST NOT
  return nodes with status `archived` or `superseded`. An explicit escape hatch
  (`include_archived` parameter / `--include-archived` flag) restores today's behavior
  for callers that legitimately need archived content; the dedicated archive-search
  path is unaffected.
- R3.2 Context formatters' operational pulls (recent decisions and equivalents) MUST
  filter to active status.
- R3.3 `hybrid_search` MUST backfill after drop-filtering: keep drawing from the merged
  candidate list until `top_k` results or exhaustion. Result counts stop lying.
- R3.4 Ranking order for surviving candidates is unchanged (no re-scoring in this run).
- R3.5 The fence never lies by omission: when a default CLI/MCP search returns fewer
  than top_k results AND fenced (archived/superseded) candidates would otherwise have
  ranked, the output appends one line — `(N archived/superseded results fenced; use
  --include-archived / include_archived=True to see them)` — so a caller who expected
  retired content knows the escape hatch exists. No note when top_k is satisfied by
  live results.

## S4 — Decay means what it says (cadence-independent weight decay)

Defect: `apply_weight_decay` has no last-decay stamp; each cron run multiplies the FULL
elapsed-age factor into the already-decayed weight, so decay compounds per run and the
effective half-life depends on cron cadence (a 5-minute launchd cadence decays an
untouched node ~50× faster than the stated 90-day half-life).

Required observable behavior:
- R4.1 Decay MUST be cadence-independent: for a node last accessed at time A with
  half-life H, its weight after any number of decay runs at time T equals
  w₀ · 0.5^((T − max(A, S))/H) within floating-point tolerance, where S is the time
  decay accounting started. Running decay twice in quick succession is a no-op the
  second time.
- R4.2 Implementation constraint (no schema change): a single meta-table key records the
  last decay run; each run decays every node only over the interval
  (max(last_accessed, previous_run), now], and every edge only over
  (max(created_at, previous_run), now]. First run after upgrade stamps the key and
  applies no decay (safe cold start — never retro-punishes).
- R4.3 The existing floor (0.01) and skip-negligible-delta behaviors are preserved.
  Edge decay stays keyed to created_at this run (the updated_at basis belongs to the
  v8 wave).

## S5 — Release 0.30.0

Performed by the Validator at endgame per the project CLAUDE.md Release Checklist (bump
pyproject.toml + __init__ + README badge + server.json, commit, push, tag, gh release,
watch all three workflow jobs, verify PyPI + `pip install kindex==0.30.0`). Not lane work.

## Cross-cutting invariants

- I1 No schema change; SCHEMA_VERSION remains 7; no new columns, tables, or indexes.
- I2 No new LLM calls on any path; S1 removes a wasted one.
- I3 No behavior change outside the surfaces named above; the full existing test suite
  passes unmodified except where a test asserts the defective behavior itself — any such
  test is reported by the Coder as a specification defect, never silently edited.
- I4 All new code degrades: failures in the new paths must never crash a hook or turn.
- I5 Public CLI/MCP signatures: only additive parameter changes (new optional params).
