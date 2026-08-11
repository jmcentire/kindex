# Postmortem — run `batch0`

Generated 2026-08-11T18:36:10+00:00 from primary sources under `.harness/runs/batch0`. Numbers without a source say UNDERIVED.

## Run
- base SHA: `8c5cc925648c64758f3824dac75e270f56783c64` (run.json)
- task digest: `2ae1e527772d626831cd69b08cb9174f565fbfc50dca06a021a58f4749487ff7` (run.json; verbatim task in TASK.md)
- declared budget: none declared (run.json)
- endgame verdict: GREEN (endgame/verdict.json)

## Derived counts (source: events.jsonl / dispatches.jsonl / injections.jsonl / wakes)
- events: 78 total — alignment_audit=21, contamination=8, dispatcher_start=1, orchestrator_correction=2, orchestrator_escalation=1, orchestrator_note=4, orchestrator_wake_disposition=12, stall_confirmed=23, triumvirate_bypass_suspected=2, validator_note=4
- lane dispatches: 2 (coder, tester)
- injections: 106 total; validator→lanes 22, coordination (dispatcher/orchestrator→validator) 84
- orchestrator wakes: 54 (projection receipts in wakes/receipts.jsonl)
- receipts in chain: 27 (.harness/receipts/chain.jsonl)

## Spend per lane (source: Claude Code session logs; absence = UNDERIVED)
- coder: tokens=UNDERIVED (no session log found)
- tester: tokens=UNDERIVED (no session log found)
- validator (repo cwd): tokens=3050604 — /Users/jmcentire/.claude/projects/-Users-jmcentire-Code-kindex (58 session files)

## Coordination vs build
Coordination = dispatcher/orchestrator traffic + wakes; build = validator→lane dispatch and tending. This split arbitrates the persistent-lanes-at-small-scale question across iterations (coder postmortem: coordination exceeded build once).
- coordination signals: 138
- build signals: 24

## Per-agent feedback (collected by the Validator before teardown — never invented here)
### validator
UNCOLLECTED — have the validator answer: what the harness got in its way, what it supplied for itself that the harness should own, what it would change in the next iteration.

### coder
UNCOLLECTED — have the coder answer: what the harness got in its way, what it supplied for itself that the harness should own, what it would change in the next iteration.

### tester
UNCOLLECTED — have the tester answer: what the harness got in its way, what it supplied for itself that the harness should own, what it would change in the next iteration.

### orchestrator
UNCOLLECTED — have the orchestrator answer: what the harness got in its way, what it supplied for itself that the harness should own, what it would change in the next iteration.

## Corrections pass
Diff this document against the raw pane logs and receipts before treating any conclusion as settled; superseded conclusions are marked FALSE-AS-WRITTEN and kept, never rewritten. Proposed process changes escalate through the Diff-Intent Gate and trigger requalification — they do not silently edit the next run.

---

## Validator addenda (collected before teardown, 2026-08-11)

### Per-agent feedback: UNCOLLECTABLE, not skipped
The coder and tester lanes both hit the account's monthly spend limit while
answering the feedback questions ("You've hit your monthly spend limit"), and the
orchestrator's headless path had died at the same cap earlier in the run. No
feedback was invented to fill the gap. What survives is behavioral evidence in
the pane logs and the incident record, not self-report. **Collect lane feedback
BEFORE the endgame next time** — the current ordering asks for it exactly when
the budget is most likely exhausted.

### Post-release verification (closes the tester's residual ask)
Against the released tag `v0.30.1`, installed through the public `[all]` path
into a bare venv: mcp resolves to 1.29.0 (pin holds), and the acceptance suite
reports **43 passed, 1 skipped** — the MCP tests report PASSED, not SKIPPED, and
the only skip is the documented R2.5 descope. Receipted.

### The number that matters
Coordination:build = 138:24. The dominant contributors were the two detector
false-positive classes (23 stall_confirmed, 21 alignment_audit, 8 contamination,
2 triumvirate_bypass — most traced to bookkeeping, not lane behavior) plus
channel-delivery defects. The bypass detector in particular fires on
pre-ignition dirt because it has no ignition-time tree snapshot to compare
against — `dispatcher.py` is the fix site. Measure the next run against this
ratio: if fixing the eight recorded upstream defects does not move it materially,
the seat model at small scale is what needs revisiting, not the implementation.
