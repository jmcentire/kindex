# Run v8 — kindex config test seam + the defects batch0 shipped

## Founder ask (verbatim, 2026-08-11)

> 1 accept and bundle fixes into v8 wave; 2 you can run without headless and bill
> the standard account; 3 do those tasks.

and, on agent assignment:

> please continue with any outstanding work slated using actual agent instances,
> Ollama for coding and codex for testing and antigravity for orchestration

## Scope

Batch0 shipped kindex 0.30.0/0.30.1 and its own adversarial review then found
defects that survived the release. This run fixes them, and fixes the thing that
made one of them unverifiable. Kindex tasks a8ce069ae2da and cf7521b81b92; evidence
in kindex nodes 45d67353a6d8 (defects) and bfed298fcb46 (the descope ruling).

**V1 — Config test seam (do this first; it unblocks V2's oracle).** kindex config
resolution has no injectable seam: module-level caching plus HOME-time resolution
make in-process test isolation impossible. The consequence was concrete — an
acceptance test could not isolate the MCP module's store and READ THE DEVELOPER'S
LIVE GRAPH twice during judge runs, and had to be descoped from 0.30.x.

**V2 — R4.1 decay is still schedule-phase-dependent.** The one-day gate accepted in
batch0's S4 ruling reintroduced the cadence-dependence S4 existed to remove.
Reproduced: from one checkpoint, runs at 24h and 47h leave weight 0.9923; runs at
23h, 46h and 47h leave 0.9853 — same end instant, different weights.
**The existing R4.1 cadence test is VACUOUS** (it cold-starts both databases then
makes immediate calls, so every compared call is a no-op) and must be rewritten,
not merely extended.

**V3 — The fence note overstates the escape hatch.** CLI and MCP say "N
archived/superseded results fenced; use --include-archived to see them", but
`include_archived=True` still excludes superseded.

**V4 — degraded.jsonl cap-vs-append race.** The size-cap rewrite can drop an append
that lands on the old inode between read and `os.replace()`. The batch0 R2.6 spec
clause was self-contradictory ("must not lose a concurrent append silently — best
effort is acceptable") and needs deciding, then satisfying.

**V5 — Backfill is bounded, not exhaustive.** `hybrid_search` draws `3*top_k`
candidates before filtering, so enough stronger expired/fenced matches can still
under-fill. Either widen it or state the bound in the specification.

**V6 — Re-enable the descoped test.** Once V1 lands, rewrite
`test_r2_5_mcp_store_open_failure_returns_typed_error` with real isolation and
remove its skip.

## Authority and mode

- Local AI-verdict mode per ~/.claude/commands/validate.md. The founder's verbatim
  ask above authorizes the work; release is a separate decision at endgame.
- **Cross-family lanes, first time**: Coder = ollama (glm-5.2 via opencode),
  Tester = codex, Orchestrator = gemini/antigravity. This is the doctrine's
  Stronger independence tier — different model families across Coder and Tester,
  no shared context, no channel — and the derived tier must be recorded from the
  dispatch receipts, not asserted.
- Batch0's lessons are binding on this run: verify oracle QUALITY before trusting
  results (does the fixture reach the path, does the assertion discriminate, does it
  fail at base for the reason the requirement names); a mutation must redden the
  test carrying that requirement; the doneness skeptic runs BEFORE release;
  collect lane feedback BEFORE the endgame; a Validator ruling is a design change
  and gets adversarial review.

## Out of scope

No schema changes. No new features. The batch0 declared gap on MCP prompts/resources
stays declared unless V1 makes it trivially testable, in which case it is a
candidate, not an obligation.
