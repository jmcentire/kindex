# VERDICT — run batch0 (kindex reliability batch, released as 0.30.0)

**VERDICT: PASS_WITH_DECLARED_GAPS** — *rendered by AI validator, no human
signature. Independence of Coder/Tester lanes: **Moderate** (separate
invocations, separate contexts, no inter-lane channel, asymmetric projections;
**same model family**, directive-level rather than kernel-level isolation).
Framing unrefuted by a human.*

Final SHA `043c4223ccd7e0a8af2f66fc3445774aba061f33`, tag `v0.30.0`.
Base `8c5cc925648c` (includes PR #18, merged by the Validator pre-ignition).

## What changed, and what surfaces it touched

Four defects, each verified present at base and absent at the final tree:

| Req | Defect | Surface class |
|---|---|---|
| S1 | Stop hook passed `--text`, preempting the stdin envelope — end-of-session capture extracted from a 13-char literal and could spend an LLM call | Standard |
| S2 | Hook commands tracebacked on any store failure; no record anywhere | Standard |
| S3 | Search fenced only superseded, not archived; `top_k` under-filled silently | Standard |
| S4 | Weight decay compounded per cron run — cadence-dependent half-life | Standard |
| — | `mcp` extra unpinned; resolved to an incompatible 2.0.0 | Standard |

Declared side effects: installed Claude hook entries (S1 rewrite migrates them),
`degraded.jsonl` in the base data dir (new file), `decay.last_run` meta row (new
row, **not** schema — invariant I1 holds, `SCHEMA_VERSION` remains 7).

## What the oracle covers

- **Red-now**: 24 tests failed against the unfixed base, ≥1 per defect; all pass
  at the final tree. One tentative-marker test (R1.5 truncated transcript lines)
  passed at base — the issue-#14 hardening already covered it — and was
  reclassified to a green-now guard under the strategy's pre-agreed recognition
  rule, not silently rewritten.
- **Green-now**: 16/16 at base and at the final tree.
- **Existing suite**: 1702 passed, 1 skipped, from a fresh checkout.
- **Endgame**: `make ship` green, isolation proof green, five live probes green
  against a real wheel installed into a bare venv with a fresh `HOME`, changeset
  hygiene clean. Receipts `R-20260811T182710Z` through `R-20260811T182843Z`.
- **Falsifiability**: mutation spot-checks — removing the search fence turns
  `test_r3_1` red; breaking the decay fold turns the closed-form R4.1 test red.
  A control whose test never went red has no executable evidence; these did.

## What the oracle does NOT cover (declared, not hidden)

1. **R2.5's in-suite test is skipped.** It cannot isolate the MCP module's config
   resolution in-process (module caching plus `HOME`-time resolution), and as
   written it *reads the developer's live graph when one exists* — observed twice
   during judge runs. The requirement itself is verified by a receipted
   fresh-interpreter probe (`R-20260811T180533Z-24442`): a corrupt store yields
   `Error: memory unavailable (DatabaseError)`. Remediation: subprocess-isolated
   rewrite. Root cause worth carrying forward: **kindex config resolution has no
   injectable seam for tests** — a v8-wave design input.
2. **MCP prompts and resources are not wrapped** by the memory-unavailable guard.
   Found by the cross-family whole-tree pass, disclosed by the implementer
   beforehand, and out of R2.5's spec scope (which names tool results). Verified
   behavior: a typed `MemoryUnavailableError` → protocol error for that one
   request; the server survives and tools keep answering. Not a crash.
3. **Three MCP acceptance tests skip** when the `mcp` extra is absent or
   incompatible. They execute under the shipped `<2.0` pin.
4. **Lane independence is Moderate, not Stronger.** Both lanes are the same model
   family as the Validator. The doctrine names different families as the cheap
   available improvement and the option existed on this machine; it was not taken
   for the lanes. Compensated at review time (below), not at authoring time.
5. **Isolation is directive-level.** `dispatch_lane.sh` does not wire the kernel
   Seatbelt backend, so lane tool restrictions were audited post-hoc rather than
   enforced by the OS.
6. **Audit-of-the-auditor lapsed** for part of the run: the orchestrator's
   headless path died at an API spend cap, and the Validator's compensating relay
   was not stood up until the founder asked. The Validator self-audited its five
   charter modes at endgame; that is weaker evidence than an independent seat.

## Anomalies and surprises (read these first)

- **The writer-never-judges split earned its keep twice.** The tester's suite
  caught two real gaps (R2.4, R3.2) that the coder's own thorough
  self-verification had passed. Neither was a test bug.
- **A different model family punctured a reading three same-family agents
  shared.** Codex found `store.recent_nodes` has no status predicate — an R3.2
  surface the coder, the tester, *and the Validator* all missed by reading
  "operational pulls" as the two named formatters. The doctrine's "three readings
  of one specification are one reading" is not theoretical.
- **The judge rig's first fixed-tree run was void.** A stale `site-packages`
  `.pth` on this machine aliases `import kindex` to the live main repo for every
  system python, so the run tested the wrong code and returned base-identical
  numbers. The rig now installs the tree under judgment into a fresh venv and
  asserts `kindex.__file__` resolves inside it. Any future verification on this
  workstation must do the same or its evidence is void.
- **Pinning `mcp` unskipped three tests that had never once executed**, and the
  first thing they found was a real defect (SQLite surfaces corruption lazily, so
  it escaped a guard that only caught the eager case).
- **Two pane injections forged the `[VALIDATOR]` prefix** without appearing in
  the receipt ledger. Content was benign; origin unproven (the wake fan-out
  defect had three orchestrator seats live concurrently). Pane-prefix identity is
  spoofable — receipts are the only authority. Contained, no lane impact.

## Cross-family review (compensating control for #4)

Three reviewers of different lineage; every finding refuted or confirmed by the
Validator against the code before it could act. Codex: 10 findings → 5 confirmed
and fixed, 4 refuted, 1 already-ruled. Ollama glm-5.2: 0 net-new, independent
corroboration of the R4.1 algebra and R3.5 note logic. Gemini whole-tree: 1
finding, confirmed with severity refuted (gap #2 above). Full dispositions in
`review-dispositions.md`.

The mandated skeptic pass on the doneness claim was run cross-family because the
usual skeptic shares the exhausted spend account — recorded as a substitution.

## Governance

- **Control-plane prohibition held**: no change to the target, verifier, tool
  grant, or promotion policy inside the run being proven, except three tool-grant
  amendments made *before* the affected work and receipted with digests.
- **Roles not collapsed**: the Validator wrote no implementation and no tests.
  The judge rig, probes, and release mechanics are Validator work by design.
- **Rounds**: 3, capped in advance. Round 3 opened narrow (one catch clause) and
  closed; the descope decision was taken rather than a fourth round.
- **Six run incidents** recorded in `incidents.md`; **eight upstream factory
  defects** banked in kindex node `1df6c2085a7c`.

## Recommendation

Ship — it is shipped, and the release is CI-gated independently of this verdict.
The two declared gaps are both narrow, both disclosed before they were found, and
neither affects the four user-visible fixes. The item most worth the founder's
attention is not in this release: **kindex has no test seam for config
resolution**, which is what made an acceptance test read a live graph. That
belongs in the v8 wave alongside the review queue.

## Post-release addendum — 0.30.1

The cross-family doneness skeptic, run against the 0.30.0 release claim,
found a defect the release had already passed every gate with: `pyproject`'s
`all` extra still carried an unbounded `mcp[cli]>=1.26.0`, so
`pip install kindex[all]` continued to produce a non-starting MCP server.
Every piece of release evidence — the live probes, the isolation gate, the
judge's pinned variant — exercised the bare wheel or the `[mcp]` extra, and
none exercised `[all]`.

Disposition: 0.30.1 cut immediately (never re-tagging 0.30.0), pinning both
extras **and** widening the isolation gate to install `[all]` and import the
MCP server, so the coverage gap closes rather than just the constraint.

This is the run's strongest evidence for the mandated doneness-skeptic step:
the code was right, the tests were green, the gates passed, and the *claim*
was still overstated. Recorded as kindex node `14e6f04c9a3d`.
