# Run v8 — After-Action Review

11.6 h wall clock. 29 implementation commits, 22 oracle commits, 22 judge runs,
109 injections, 6 spec amendments. Compare batch0: ~40 h for a smaller scope that
shipped a vacuous oracle on its headline requirement.

## What worked

**Mutation testing was the only control that found blind oracles.** It caught the
R1.5 canary watching a door the code never used — on the run's CRITICAL
requirement, the one whose violation is a live-graph read. Nothing else in the
run would have found it. It then caught two freshly-written guards that guarded
nothing.

**Independent probing before dispatch prevented four wrong dispatches.** Of the
last six defect classifications, only one was a plain implementation defect.
Reviewer claims were wrong more often than right about *mechanism* while being
useful about *region*: the skeptic's decay theory was wrong (access does not bump
weight) though a real regression sat next to it; one reviewer rated R2.1
"Satisfied" and missed that regression entirely; its "catastrophic data
corruption" claim was refuted by measurement. Acting on any of them directly
would have sent lanes to change correct code — twice.

**The oracle-leak guard held, including against me.** It blocked one of my own
dispatches for the word "tests." A rule the executor must remember is not a
control; a rule the pipe enforces is.

**Asymmetric projections did their job.** The tester correctly refused to label a
case red-now that it could not observe as red from its own tree — and the fault
was my instruction, not its execution.

## What failed

**One failure shape produced nearly every worthless check in the run:** the
check was written against the fix's *artifact* instead of the prohibited
*action*. Four instances, three parties, including the Validator:

| Asserted | What actually escaped |
|---|---|
| canaries at a monkeypatched `HOME` | code read an import-time constant elsewhere |
| the git probe's *start directory* was contained | the *upward walk* from it |
| invocations carrying `kwargs["cwd"]` | code passes `git -C <dir>` |
| the *returned path* was contained | a forbidden *read*, clamped before return |

The third was written *after* being told the second was non-discriminating.

**Phase 1 did not do a specification's work.** All four artifacts existed with
digests, yet six amendments were authored after ratification — pinning the seam
contract, the MCP accessor shape, the note/flag identity, and the disclosure
channel. Amendment 6 retracts Amendment 5, an hour apart, both mine.

**The Validator became the single point of failure.** Both lanes executed well,
but every diagnosis came from me. A triumvirate whose judgment concentrates in
one seat has the failure mode it was designed to prevent.

**My own tooling produced a false green.** The ad-hoc mutation runner reported
`SURVIVED` for a patch that had died on an `IndentationError` — the exact defect
class the run was hunting, in the instrument built to hunt it.

**I directed the tester to assert on private implementation state** its
projection cannot see by design. I reached past the seam this run built.

**Four probes of mine disagreed with each other** on the decay question. I nearly
dispatched a starvation finding off an uncontrolled one. Reading the code proved
reliable where my timing experiments did not.

## Betterments

**Implemented this session** (in `~/Code/factory`, uncommitted):

1. `harness/mutate.sh` — the factory had *no* mutation tooling. Fails closed on
   four preconditions: code loads from the mutated tree, clean tree green first,
   patch actually applied (`PATCH-FAILED` ≠ `SURVIVED`), full suite ran.
   Dogfooded both ways before shipping.
2. `docs/practices/oracle-quality.md` — the prohibited-action rule, the table
   above, survivors-are-a-question, and document-the-property-when-unobservable.
3. `VALIDATION-DIRECTIVE.md` item 7 **corrected against evidence**: it previously
   *required* that a mutation redden the specific test carrying the requirement.
   v8 disproved that as written (an equivalent mutant legitimately reddens
   elsewhere), while batch0's opposite case is also real. It now runs the full
   suite and treats a green carrier as a question with two readings.
4. Item 8's final gate now demands the **entire suite on the integrated tree**
   with import resolution verified, re-run after every late change.

**Still open:**

5. **Make Phase 1 failable.** A specification that cannot state an observable
   effect per requirement, and a testing strategy that does not say how each
   oracle will be shown non-vacuous, should not be ratifiable. Six post-hoc
   amendments is the measurement.
6. **Break the Validator monopoly on diagnosis.** Lanes executed my conclusions
   rather than forming their own. The pre-endgame feedback loop failed outright:
   the coder was asked twice for critique and returned status both times.
7. **Resolve stalls, don't just detect them.** The codex lane blocked twice on
   interactive approval prompts; the monitor reported `BLOCKED` and the Validator
   had to hand-send an Enter through tmux — outside the receipted path, so
   unrecorded. Detection that costs the full stall duration is half a control.
8. **The independent audit needs a liveness proof, not just its own failure
   domain.** The seat has now failed in *both* runs, by different mechanisms:
   batch0 logged 28 dead-seat wakes (detected, on a stray-flag prompt); v8 had 5
   that went entirely **undetected**, because the repair I wrote mid-run discarded
   the invocation's exit status and its fallback string matched none of the
   phrases the detector greps for. Zero runs so far in which the seat auditing the
   Validator was reliably alive — which makes it the least trustworthy control in
   the factory while being the only one pointed at the Validator.

   A failure domain is necessary but insufficient: both failures were *silent*.
   The seat needs a dead-man's switch — it must prove it ran, and its absence must
   page, rather than the harness inferring health from a non-empty string. The
   exit-code fix in `orchestrator_wake.sh` closes this instance; it does not
   establish liveness in general.

   Relatedly, the shared spend cap has removed an independent check from two
   consecutive runs (simulacrum is hard-blocked until 2026-09-01).

## The honest metric

Three times I judged this work done. Each time a *new lens* found real defects.
The curve flattened when I ran out of lenses, not when the code ran out of
defects. Any factory that reports "green" without reporting **which lenses were
pointed at it** is reporting the same thing v8 reported at `fixed19` — where the
product had a data regression and four containment escapes.
