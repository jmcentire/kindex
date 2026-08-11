# Falsifiability spot-checks — run batch0 (Validator-executed)

Method: editable install of the lane/coder tree in a scratch venv
(judge/falsify + falsify-venv; import path asserted to resolve into the scratch).
Each control mutated in place, matching red_now tests run, source restored via git.

| Control | Mutation | Result | Verdict |
|---|---|---|---|
| S3 archived-search fence | status_fence forced to pre-fix form (superseded-only) | test_r3_1_fts_default_excludes_archived_and_superseded RED (was green unmutated) | control has executable evidence |
| S4 decay fold | decay.last_run read forced to None (perpetual cold start — decay never applies) | test_r4_1_closed_form_half_life_and_second_run_noop RED (was green unmutated) | control has executable evidence |

Notes:
- test_r3_2 fails in both mutated and unmutated runs — that is the open
  implementation gap returned to the coder (bare FAIL R3.2), not a mutation effect.
- The R4.1 cadence-equality twin passes vacuously under the never-decay mutation
  (both DBs decay equally, i.e. not at all); the closed-form absolute test is the
  one carrying detection for this mutation class. The pair jointly covers.
- MCP-variant tests skip in the falsify venv (mcp 2.0.0 incompatibility — same
  packaging finding as the judge venvs; pin decision recorded for the release).
