# Testing & Monitoring Strategy — run v8

Proposed by the Validator, ratified in local AI-verdict mode. The Tester works ONLY
from this artifact, the product specification, the architecture, and its projection
(`tests/`, `pyproject.toml`, `Makefile`, `README.md`, `CLAUDE.md`). Exact surfaces are
DECLARED here because the Tester has no source view; a declared surface that does not
exist as stated is a specification defect to report, never a guess to paper over.

## Ground rules

- **T0.1** New tests in new files: `tests/test_v8_config_seam.py`,
  `tests/test_v8_decay_schedule.py`, `tests/test_v8_ledger_race.py`,
  `tests/test_v8_search_honesty.py`. Existing files are read-only except the one
  edit named in T0.7.
- **T0.2** Markers as in batch0: `@pytest.mark.red_now` asserts fixed behavior and is
  EXPECTED TO FAIL at base; unmarked tests are green-now guards. Register the marker
  in the existing root `conftest.py` (it already defines `red_now`).
- **T0.3 — ORACLE QUALITY IS A DELIVERABLE, NOT A COURTESY (invariant I6).** For each
  test you write, record in its docstring: (a) the reversion that turns it red, (b)
  **why the fixture is not degenerate** — that it reaches the code path, that the
  assertion discriminates, and that the test fails at base *for the requirement's
  reason and not an unrelated one*.
  This exists because batch0's headline requirement was "verified" by a test that
  cold-started two databases and then made immediate calls, so every compared call was
  a no-op: it failed at base and passed at head for reasons unrelated to the
  requirement, and nothing caught it until after release. **Two named traps you must
  actively avoid: a time-based fixture whose operations are all no-ops, and an
  ordering fixture with fewer than two surviving elements.**
- **T0.4** Determinism: no network, no LLM keys, no sleeps beyond 0.2s except where a
  race is under test. Control time by writing timestamps into fixture rows, not by
  waiting.
- **T0.5** You may `pip install kindex==0.30.1` into a workspace venv as an authoring
  baseline. red_now tests will fail there — correct.
- **T0.6** Done = committed on `lane/tester` with `__DONE__` and a coverage summary
  per requirement id, plus an explicit statement of which tests you consider *weakest*
  and why. That statement is required; "none" is not an acceptable answer.
- **T0.7** One permitted existing-file edit: remove the skip decorator from
  `test_r2_5_mcp_store_open_failure_returns_typed_error` in
  `tests/test_batch0_degrade.py` and rewrite its fixture to use the V1 seam (R6.1).
  Its skip reason names the descope; that reason is what you are retiring.

## Declared test-visible surfaces

- `kindex.config` — the V1 seam. **Its exact name and shape are the implementer's
  choice, so do not guess:** write the V1 tests against the seam as described in the
  specification (bind a root, resolve, release), and if the concrete API is not
  discoverable from the public documentation in your projection, raise
  `DEFECT->VALIDATOR` asking for the signature. Do not invent one.
- `kindex.store.Store` — construction against a tmp path, `add_node`, `fts_search`,
  `apply_weight_decay`, `set_meta`/`get_meta`, direct SQL on the connection for
  fixture construction.
- `kindex.retrieve.hybrid_search(store, query, top_k=...)`.
- `kindex.config.record_degraded` (or the equivalent named in the spec) for V4.
- CLI via subprocess and MCP via the module's tool functions, as existing tests do.

## Acceptance tests per requirement

| Req | Test (marker) | Contract |
|---|---|---|
| R1.1 | red_now | After import, bind config to a tmp root; resolution returns paths under it. |
| R1.2 | red_now | With a binding active, the MCP store accessor resolves under the binding — the surface that caused the descope. |
| R1.3 | green-now | With no binding, resolution order/precedence/defaults are unchanged. |
| R1.4 | red_now | Bind, release, resolve → back to default; a second test in the same process sees no leak. |
| R1.5 | **red_now, CRITICAL** | With a binding active, no path outside it is read or created. Prove it: point the binding at a tmp root, plant a canary file at the default location, exercise store open + a search + the MCP accessor, and assert the canary's mtime is untouched and no file appeared outside the binding. |
| R2.1 | red_now | **The batch0 vacuous test, done properly.** Backdate a checkpoint; drive two schedules that END AT THE SAME INSTANT but differ in phase (24h+47h vs 23h+46h+47h) by writing the checkpoint between runs rather than sleeping; assert both equal the closed form within tolerance. Fails at base with the exact 0.9923 vs 0.9853 split. |
| R2.1 | red_now | Sub-day cadence: many short intervals over a long span equal one long fold. |
| R2.2 | green-now | Immediate second run changes nothing. |
| R2.3 | green-now | First-ever run stamps and decays nothing. |
| R2.4 | red_now | Anti-starvation: a floor-adjacent weight under a frequent cadence still decays over a long span — no permanent freeze. |
| R2.5 | green-now | Floor holds; just-accessed node untouched; future `last_accessed` neither raises nor boosts. |
| R3.1 | red_now | The note's promise matches behavior: whatever the note names, passing the flag reveals exactly that. |
| R4.1 | red_now | Append concurrently with a cap rewrite (real processes); every event is present or a loss is counted. Zero torn lines. |
| R4.2 | red_now | Ledger write with an unwritable target never raises into the caller. |
| R5.1 | red_now | Either backfill reaches top_k past the old window, or the documented bound is present and the note states it. |
| R6.1 | red_now | The un-skipped MCP typed-error test passes using the seam, with a canary proving no real-graph access. |
| I1 | green-now | SCHEMA_VERSION is 7 and the table/column/index inventory is unchanged after a decay run. |

## Validator-side evidence discipline

Red-now at base `d4ecf5cf`, green-now at base, both receipted. **Oracle-quality review
before results are trusted:** for each red_now test the Validator confirms the fixture
reaches the path and the base failure message names the requirement's reason — a test
failing at base for an unrelated reason is treated as no evidence. Mutation
spot-checks must redden the specific test carrying the requirement. Lane feedback is
collected BEFORE the endgame. The doneness skeptic runs BEFORE any release decision.
