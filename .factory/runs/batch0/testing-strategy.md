# Testing & Monitoring Strategy — run batch0

Proposed by the Validator, ratified in local AI-verdict mode (no human signature —
flagged). The Tester works ONLY from this artifact + the product specification + the
architecture + the material in the tester projection (tests/, pyproject.toml, Makefile,
README.md, CLAUDE.md). Exact module surfaces under test are DECLARED here because the
tester has no source view; if a declared surface turns out not to exist as stated, that
is a specification defect to report upward, never a guess to paper over.

## Ground rules for the Tester

- T0.1 New tests go in new files: `tests/test_batch0_capture.py`,
  `tests/test_batch0_degrade.py`, `tests/test_batch0_search_fence.py`,
  `tests/test_batch0_decay.py`. Existing test files are read-only (fixtures and
  conventions may be imported/reused, never edited).
- T0.2 Every test carries exactly one of two markers:
  `@pytest.mark.red_now` — asserts the FIXED behavior; EXPECTED TO FAIL on the unfixed
  base SHA (that failure is the point: it proves the suite catches the defect);
  or no marker — a green-now guard asserting behavior that must be identical before and
  after the fix. Register the marker in the new files via `pytest.ini`-style
  `pytestmark`/conftest addition ONLY if tests/conftest.py does not already define one —
  and any conftest change is additive, in a new `tests/conftest_batch0.py`-style helper
  if possible, else reported.
- T0.3 Falsifiability at contract level (flagged run-wide per harness ratification item
  3): every test docstring names the reversion that turns it red, e.g. "red if the Stop
  hook again passes --text / if compact-hook again prefers --text over an envelope."
- T0.4 Determinism: no network, no LLM keys (tests must pass with no ANTHROPIC/OPENAI
  key in env), no sleeps beyond ≤0.1s, tmp dirs for all state. Time control for decay
  tests: write backdated timestamps directly into the fixture DB rows (SQL UPDATE on
  nodes/edges/meta) — fixtures are yours to construct.
- T0.5 Authoring baseline: you MAY `pip install kindex==0.29.0` into a scratch venv to
  sanity-check signatures while authoring. Expect red_now tests to fail there — that is
  correct. The authoritative red-now/green-now runs are executed by the Validator, not
  by you.
- T0.6 Done = committed on your `lane/tester` branch with a final line `__DONE__` and a
  one-paragraph summary of coverage per requirement id. Questions/failure reports/spec
  defects go up to the Validator; nothing else leaves the lane.

## Declared test-visible surfaces

- CLI via subprocess (pattern per existing tests): the `kin` entry point with env
  overrides for data dir / HOME as existing tests do. Hook envelope = JSON object on
  stdin with keys `hook_event_name`, `transcript_path`, `session_id`, `cwd`.
- `kindex.store.Store` — construction against a tmp DB path per existing test fixtures;
  `add_node(...)`, `fts_search(query, ...)` (gains `include_archived: bool = False`),
  `apply_weight_decay()`, meta accessors as used in existing tests, node status mutation
  via the existing set-state/edit surface used in tests.
- `kindex.retrieve.hybrid_search(store, query, top_k=...)` returning ranked results.
- `kindex.hooks.prime_context(...)` as exercised by existing hook tests.
- `kindex.setup` hook-entry construction: assert on the WRITTEN settings content (the
  Stop hook command line) using the same entry points existing setup tests use.
- Transcript fixture: a small `.jsonl` file in the modern Claude Code shape used by
  existing capture tests (nested `message` objects) — reuse existing fixture builders.

## Acceptance tests per requirement (oracle matrix)

| Req | Test (marker) | Contract |
|---|---|---|
| R1.1 | red_now | `kin compact-hook --text "Session ended"` with a valid envelope on stdin pointing at a transcript fixture → extraction consumed the transcript (observable: minted node content references transcript material, not "Session ended"; or the capture path's recorded source is the transcript path). Env kills LLM (no keys) so the keyword path runs. |
| R1.1 | green-now | Same invocation with stdin that is NOT a parseable envelope (empty / garbage) → behaves as today's --text path; exit 0. |
| R1.2 | red_now | Generated/updated Stop hook entry contains `compact-hook` WITHOUT `--text`, and sibling commands in the same entry survive the rewrite; running the setup routine twice is idempotent. |
| R1.3 | red_now | Envelope whose transcript_path does not exist → exit 0, zero nodes minted. |
| R1.4 | green-now | No content-empty node minted across the above (existing invariant). |
| R2.1 | red_now | `kin prime` with a corrupted/unopenable DB (fixture: truncate or chmod the DB file) → exit 0, single degraded line on stdout matching `# kindex degraded:`. |
| R2.1 | green-now | A non-hook command (e.g. `kin search`) with the same broken DB → nonzero exit, real traceback/error (hooks fail open; humans see errors). |
| R2.2 | red_now | The degraded event lands as one JSON line in degraded.jsonl in the base data dir with the required keys; a second failure appends. Size-cap: seed a >1MB file, trigger append, file shrinks to last 200 lines + the new event. |
| R2.3 | red_now | `kin status` / `kin doctor` with ≥1 recent degraded event show the count; with no file, no warning. |
| R2.4 | red_now | prime with a healthy DB but one poisoned section (fixture: a node engineered to throw in one section — e.g. malformed extra JSON if that is what the shield covers) → other sections still render (partial > empty). If constructing a poisoned fixture proves impossible from the declared surfaces, report it as a spec defect rather than faking it. |
| R2.5 | red_now | MCP store-getter failure path returns the literal `Error: memory unavailable (` prefix — test via the mcp_server module's store accessor with a broken DB path, per existing MCP test conventions. |
| R3.1 | red_now | Fixture: active node A + archived node B, both matching a query. Default `fts_search`/`hybrid_search`/MCP search exclude B; `include_archived=True` includes B. |
| R3.2 | red_now | An archived/superseded decision node does not appear in the context formatter's recent-decisions output. |
| R3.3 | red_now | Fixture: enough matching nodes that expired/superseded entries previously consumed top_k slots → default search returns exactly top_k live results (backfill), not fewer, when ≥ top_k live candidates exist. |
| R3.4 | green-now | Relative ranking order of surviving live candidates is unchanged vs a fence-free call on an all-active fixture. |
| R4.1 | red_now | Fixture: node with last_accessed backdated 90 days (SQL). One decay run → weight ≈ w₀·0.5 (tolerance 5%). Then a second immediate run → weight unchanged (delta < 0.001). |
| R4.1 | red_now | Cadence independence: same backdated fixture in two DBs; DB-X gets one decay run; DB-Y gets 5 decay runs in a loop (same wall-clock); final weights equal within tolerance. |
| R4.2 | red_now | Fresh DB, first-ever decay run: no weight changes; the meta checkpoint exists afterwards. |
| R4.3 | green-now | Floor 0.01 respected; negligible-delta skip behavior preserved (weights never drop below floor; a just-accessed node does not change). |
| R1.5 | red_now* | Transcript fixture whose final line is truncated mid-JSON and containing one garbage line → extraction succeeds on the parseable remainder, exit 0, no crash. (*Tentative marker: if this passes on the unfixed base — the #14 hardening may already cover it — the Validator reclassifies it as a green-now guard; that reclassification is recorded, per the recognition rule.) |
| R2.6 | red_now | Two processes append degraded events concurrently (e.g. multiprocessing, 50 events each) → the ledger contains exactly 100 well-formed JSON lines, zero torn/interleaved lines. |
| R3.5 | red_now | Fixture where fenced candidates would rank and live results < top_k → CLI/MCP search output contains the `archived/superseded results fenced` note; with top_k satisfied by live results → no note. |
| I3 | (Validator) | Full existing suite at final SHA — run by the Validator, not a lane. |

Edge cases the suite must include somewhere above: empty stdin vs whitespace stdin vs
non-JSON stdin for the envelope parser; degraded-ledger append when the base data dir
does not yet exist; search fence with a node whose status is the legacy default; decay
on a node with last_accessed in the future (clock skew — must not raise, must not boost).

Sim-pass provenance: the R1.5/R2.6/R3.5 rows and their spec lines were added after the
receipted framing attack (receipt R-20260811T023345Z-21918) — chaos-under-concurrency
was the named missing class. The attack's other two objections were already answered by
the spec text (cap eviction policy; the R4.1 closed-form invariant) and are recorded as
examined-and-rejected, not ignored.

## Monitoring / observability (this run)

The degraded-event surfacing in status/doctor IS the run's monitor deliverable (R2.3).
No new alerting. Recovery posture: git revert + patch release; PyPI yank for a broken
artifact.

## Evidence discipline (Validator-side, recorded for the run)

Red-now: every red_now test executed against base 8c5cc925648c must fail; ≥1 per defect
on the defect itself. Green-now: every unmarked test must pass at base. Both recorded as
run ids + exit codes in the run's receipts. Falsifiability spot-checks: Validator breaks
S3 fence and S4 fold in a scratch worktree and confirms the matching tests go red.
Critical-class determinism rules are not triggered (no Critical surfaces); Standard rule
applied: one rerun allowed, recorded separately, never overwriting.
