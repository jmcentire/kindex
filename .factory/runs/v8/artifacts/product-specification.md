# Product Specification — run v8 (kindex config seam + batch0 residual defects)

Source: TASK.md (verbatim, digest in run.json). Research ground already banked in
kindex: 45d67353a6d8 (confirmed defects, Validator-reproduced), bfed298fcb46 (the
descope ruling), 42a959585b28 (monitor/liveness failure), 91c44e3e65ca (the 0.30.x
release). Ratified in local AI-verdict mode; no human signature — stated in the verdict.

Version target: kindex 0.31.0. **Hard invariant: no database schema change**
(SCHEMA_VERSION stays 7).

## V1 — Config resolution has a test seam

Defect: `kindex.config` resolves the data directory from `HOME` at import time and
caches at module level, so a test cannot point the library at a temp directory after
import. Observed consequence: an MCP acceptance test could not isolate its store and
**queried the developer's live graph twice** during judge runs, and was descoped.

- **R1.1** There MUST be a documented, supported way for a test to bind config
  resolution to an explicit root **without** relying on process-start environment,
  and it MUST take effect for callers that have already imported the module.
- **R1.2** The seam MUST be honored by every surface that resolves config, including
  `kindex.mcp_server`'s store accessor — that is the surface whose absence caused the
  descope.
- **R1.3** Production behavior with the seam unused MUST be byte-identical to today:
  same resolution order, same precedence, same defaults.
- **R1.4** The seam MUST be reversible within a process (bind, then release), so a
  test cannot leak its binding into a later test in the same session.
- **R1.5** Attempting to resolve config while a binding is active MUST NEVER read or
  write any path outside that binding. This is the requirement whose violation
  motivated the run: a false negative here is a live-graph read.

## V2 — Weight decay is schedule-independent

Defect: the one-day gate makes the observed weight depend on run *phase*. Reproduced:
from one checkpoint, runs at 24h+47h leave 0.9923; runs at 23h+46h+47h leave 0.9853.

- **R2.1** For a node last accessed at A with half-life H, its weight observed at time
  T after any number of decay runs MUST equal `w₀ · 0.5^((T − max(A,S))/H)` within a
  stated tolerance, where S is when decay accounting began — **for every run schedule,
  including schedules with sub-day intervals**. The staircase is the defect.
- **R2.2** Running decay twice in immediate succession MUST be a no-op the second time
  (this must remain true, and must not be achieved by gating the fold itself).
- **R2.3** The cold-start contract stands: the first run after upgrade establishes
  accounting and decays nothing.
- **R2.4** Decay MUST NOT starve: no weight may be permanently frozen by an
  accumulation threshold, at any half-life and any cadence. R2.1 and R2.4 together are
  the real constraint — batch0 satisfied one by breaking the other.
- **R2.5** The floor (0.01) is preserved, and a just-accessed node is untouched.

## V3 — The fence note describes what the flag actually does

- **R3.1** The escape-hatch note MUST NOT promise content the flag will not reveal.
  Either the note names only what `include_archived` un-fences, or `include_archived`
  un-fences superseded too — implementer's choice, stated in the report.

## V4 — The degraded ledger does not lose events

The batch0 clause was self-contradictory; this replaces it.

- **R4.1** An append that occurs concurrently with a size-cap rewrite MUST either be
  present in the resulting file or the loss MUST be recorded as a counted event; a
  silent drop is a defect. Torn or interleaved lines remain forbidden.
- **R4.2** The ledger write path MUST NOT raise into its caller under any
  circumstance — it exists to record failures and must not become one.

## V5 — Backfill bound is honest

- **R5.1** Either `hybrid_search` backfills until `top_k` live results or genuine
  exhaustion, **or** the bounded candidate window is stated in the API documentation
  and the fence note. An unstated bound that silently under-fills is the defect.

## V6 — The MCP failure surface is testable in isolation

**AMENDED (see Amendment 2): this section previously named a specific test file and
function to the implementation lane. That was a defect in this specification — a
requirement must state an observable effect, never the oracle that checks it — and
it is corrected below. The behavior required is unchanged.**

- **R6.1** A store failure that SQLite surfaces only at first query MUST yield the
  typed memory-unavailable result at the MCP surface **while a config binding is
  active**, and the whole interaction MUST be observable without any access to a
  real graph. In other words: the V1 seam must make this failure mode reachable in
  a test without the test needing the developer's own data directory. Whether an
  existing skipped test is what demonstrates it is a matter for the oracle, not for
  the implementation.

## Cross-cutting invariants

- **I1** No schema change; SCHEMA_VERSION remains 7.
- **I2** No new LLM calls on any path.
- **I3** The existing suite passes unmodified. A test asserting defective behavior is
  a specification-defect report, never a silent edit.
- **I4** New code degrades: no new failure may crash a hook or an MCP turn.
- **I5** Public signatures change additively only.
- **I6 (oracle quality, new and binding)** Every test carrying a requirement MUST fail
  at base **for the reason that requirement names**, its fixture MUST demonstrably
  reach the code path, and its assertion MUST discriminate. A fixture that makes every
  compared call a no-op, or that contains one survivor where ordering is asserted, is
  a defective test even when red at base and green at head. This invariant exists
  because batch0 shipped a vacuous oracle on its headline requirement.

---

## AMENDMENT 2 — a requirement names an effect, never a test (Validator, post-hoc)

Raised by the founder: the original R6.1 named
`test_r2_5_mcp_store_open_failure_returns_typed_error` to the implementation lane in
this signed artifact, and the coder dispatch compounded it by identifying that
requirement as "the Tester's edit."

Both are defects in this specification, not in either lane's conduct. The Coder is
not supposed to know what the oracle contains; a requirement written in terms of a
test is an instruction to satisfy the test rather than the behavior, and it is the
same error as an acceptance criterion shaped by its check. R6.1 is restated above as
an observable effect. Recorded rather than quietly corrected because the coder acted
on the original text, and its handling of R6.1 must be judged against what it was
actually told.

---

## AMENDMENT 4 — R3.1 states an identity, not a courtesy (Validator, retraction)

I ruled during the run that a superseded item redirecting to a live successor
should be dropped from the fenced set, on the reasoning that nothing is really
being withheld when the successor is present. That ruling was wrong and is
retracted. It generalized from one edge case and broke the identity R3.1
actually requires.

**R3.1 (restated).** The fenced set MUST be exactly the set of items the default
query withheld AND that `include_archived` reveals. The note's named statuses
and its count are derived from that set. Over-promising (naming what the flag
will not reveal) and under-promising (the flag revealing what the note never
named) are the same defect in opposite directions. There is no redirect
exception: if a redirecting item should not be surfaced, the flag must stop
revealing it, and the two must agree either way.

## AMENDMENT 5 — a disclosure is observable or it is not a disclosure (Validator)

R5.1 required the bound to be "stated in the API documentation and the fence
note" and said nothing about where the note is emitted. The implementation
states it, but routes it to standard error when the result set is empty and to
standard output otherwise — the stream is chosen by result count.

This is a gap in this specification, not a defect the implementation invented,
and it is recorded rather than quietly fixed because the lane satisfied R5.1 as
written. Closing it:

- **R5.1 (added clause)** The bound disclosure MUST appear on the surface's
  primary output stream in every branch, including the branch where the window
  suppressed every candidate. A caller reading that stream must never receive a
  short or empty result set with the explanation on a different channel — that
  is the silent under-fill R5.1 exists to eliminate, and the empty case is where
  the bound has done the most damage. A status line ("No results.") is a status
  and may stay on the diagnostic stream; the note is search output.

## AMENDMENT 6 — the disclosure channel is fixed per output MODE, not per result count (Validator)

Amendment 5 was too blunt and I caused a regression with it. Requiring the note
on "the primary output stream in every branch" was applied to `--json` as well,
which appended a non-JSON line after the JSON document and made `kin search
--json | jq` fail to parse. Machine-readable output is a contract, and I5 freezes
its top-level shape, so the note cannot be carried inside it either.

The distinction Amendment 5 was reaching for is this: the channel must be a
property of the declared output MODE, never of how many results happened to come
back. A stream that varies with the data is a disclosure a caller cannot rely on;
a stream that varies with an explicitly requested format is part of that format's
contract.

- **R5.1 (restated, superseding the Amendment 5 clause).** Within a given output
  mode the disclosure MUST land on the same channel regardless of the result
  count, including the empty case. In human-readable mode that channel is
  standard output. In `--json` mode the document on standard output MUST remain
  parseable and keep its existing top-level shape (I5), so the disclosure goes to
  standard error — always, not only when empty. What is forbidden is what the run
  actually shipped before this: the same note landing on different channels in
  the same mode depending on whether results were found.

Follow-up recorded, not fixed here: in `--json` mode with no results the CLI
emits nothing on standard output rather than an empty array. That is pre-existing
0.30.1 behavior, outside this run's requirements, and is logged for a later run.
