# Run v8 — Validator Verdict

- **Base SHA**: `d4ecf5cfed00a43a75ba0878d56a731cec13f7d5`
- **Implementation head**: `835e17b` · **Oracle head**: `35f6d45`
- **Product Specification**: `b92667f7d9f7` (6 amendments) · Architecture: `6d779d074aa7` · Testing Strategy: `ede46bf4dc37`
- **Elapsed**: 11.6 h · 29 implementation commits, 22 oracle commits, 22 judge runs, 109 injections
- **Ratified in local AI-verdict mode. No human signature.**

## Disposition

**`NO KNOWN OPEN BLOCKER` — a statement about my knowledge, not about the code.**

Head `29fb27d` / tester `db73dfe`. Judge `fixed28`: 0 failed / 43 passed red-now,
0 / 17 green-now. Full suite 1736 passed. **Superseded 2026-08-13:** 0.31.0 shipped (commit `39e5f20`, tag `v0.31.0`). The
lane SHAs cited above no longer resolve — the worktrees were `git archive` exports
with no ancestry and `.harness/` was removed at teardown, so this verdict's evidence
chain is not independently checkable. See `~/Code/fucked_up_factory.md` §3.

The two R1.5 symlink escapes below are closed and, unlike most fixes in this run,
their guards are **proven**: flipping the containment branch from `return None` to
`return resolved` is killed by exactly those two guards and nothing else. An
earlier, cruder mutation was killed by unrelated activity-log and adapter cases,
which proves nothing about them, so it was re-cut rather than banked.

**Read the disposition as bounded by the section below.** Five declarations of
doneness on this run, four wrong. The record does not support a stronger claim
than "I no longer know of an open blocker."

History of this line, which is the most useful thing in this document:
`READY_PENDING_HUMAN_RELEASE` → `BLOCKED` (nine-lens review found a data
regression and four containment escapes every gate had passed) → blockers closed
→ `BLOCKED` again (a second review found more in the *fix* commits) → briefly
`NO KNOWN OPEN BLOCKER` → **`BLOCKED`**. Five declarations, four of them wrong.

### The open blockers, and why they were wrongly cleared

A review reported an R1.5 **write** escape and an R1.5 **read** escape through
symlinks. I probed both, found nothing, and recorded both as refuted. Both are
real, and both reproduce at head `f76b07e`:

- **Write.** With the bound global config path a symlink to an existing outside
  file, a `--global` config write **overwrote it** — `editor: USER_REAL_VALUE`
  became `editor: PWNED`. Creating through such a symlink also makes directories
  outside the root.
- **Read.** With the project config path a symlink to an outside file, that file
  is read under an active binding. The reviewing agent reproduced this against
  the developer's **actual** `~/.config/kindex/kin.yaml`.

Common cause: containment is applied **before** `.resolve()` and never after, so
a symlink walks straight out of the root.

**Why my probe cleared them.** I built the symlink pointing at a target that does
not exist, which raises `FileNotFoundError`. The real case points at an existing
path and succeeds silently. I chose the variant of the class that fails loudly and
used it to declare the class clean. A probe that exercises only the noisy failure
mode of a defect class cannot clear that class — and R1.5 is the one requirement
this specification says blocks with no risk acceptance.

The suite cannot see the neighbouring crash either: `tests/test_profiles.py`'s
`cli_home` fixture pre-creates `.config/kindex` before every case, pre-satisfying
the precondition the implementation stopped establishing.

**Head `f76b07e`. Judge `fixed26`: 0 failed / 41 passed red-now, 0 / 17 green-now.
Full suite 1734 passed on the integrated tree. Nothing committed, tagged, or
published.**

What closed since the `BLOCKED` ruling, each verified by direct probe against
both v8 and 0.30.1 rather than accepted from a lane or a reviewer:

| defect | classification | evidence |
|---|---|---|
| `include_archived` lost the FTS index entirely | v8 regression | trace showed no FTS statement; flag returned *fewer* rows than the default |
| decay weight depended on fold count | v8 regression | 25 920 folds at the shipped 5-min cron breached the run's own 5e-4 tolerance |
| `config_path` read outside the binding | v8 regression | outside file opened and honored |
| identity resolution shelled out to git | v8 regression | read the real `~/.gitconfig` above the root |
| config/profile `--global` **wrote** outside the root | v8 regression | first confirmed *write* escape; all prior were reads |
| ledger blocked forever on `flock` | v8 regression | R4.2 vs R4.1 tension I resolved wrongly, then corrected |
| decay checkpoint not monotonic | v8 regression | backwards clock erased R2.3 protection |
| fence count the flag could not deliver | v8 regression | R3.1 identity, over-promising direction |
| decay churned git-tracked `index.json` / `code-map.json` | v8 worsened the rate 48× | one 30-min cron cycle rewrote a git-tracked file |
| dangling-symlink crash in profile create | v8 regression | unhandled `FileNotFoundError`; 0.30.1 clean |

**Deliberately not fixed — measured as pre-existing in 0.30.1**, so they do not
block a release that improves on it: the 10 000-row fence window, graph-expansion
and vector fenced candidates, tag/owner post-filter attribution, R6.1 coverage on
MCP resources and prompts, `SQLITE_BUSY` handling, the unchecked `os.write`
return, and the ledger size cap failing to bound an already-oversized file.

**Why this still is not a recommendation to ship.** Every one of the four times I
reached this state, the next lens found real defects — including in code I had
personally verified an hour earlier. The defect curve has never flattened on its
own; it flattens when I stop finding new ways to look. Two of my own verification
probes were vacuous and reported success on live defects. Three defects came from
my own instructions. I ruled against correct code twice. The release call belongs
to a human who has not spent 62 hours becoming invested in this being finished.

**BLOCKER — `include_archived` silently loses the FTS index.** In v8,
`fts_search(..., include_archived=True)` never reaches FTS5: it emits malformed
SQL, the `sqlite3.OperationalError` is swallowed by the LIKE fallback, and the
query degrades to an unindexed `LIKE '%term%'` full-table scan with `rank`
hardcoded to `0`. Verified by tracing every statement sqlite actually receives:

| tree | `include_archived=True` | ranking |
|---|---|---|
| main (0.30.1) | uses FTS | real BM25 |
| v8 head `835e17b` | **LIKE full scan** | all ranks `0` |

Consequences, all on the default path:
1. Every `--include-archived` search loses BM25 relevance ordering entirely.
2. Substring matching replaces tokenized search — a different, wrong result set.
3. `retrieve.py:467` computes the **fenced set** with
   `fts_search(query, limit=10000, include_archived=True)` — so the R3.1 note is
   derived from a full scan of up to 10 000 rows, per search, whenever results
   fall short of `top_k`. That is both a correctness fault (the fenced set is
   built by substring matching) and an O(n) cost on the hot path.

Found independently by two of nine lenses, then reproduced directly by the
Validator against both trees. Attribution is mine, not the lane's: my R3.1 ruling
— retracting the redirect exemption and demanding the note/flag identity — is what
drove the supplementary query onto that path.

Nothing is committed, tagged, or published.

## Evidence

| Gate | Result |
|---|---|
| Judge `fixed22` red-now | 0 failed / 39 passed |
| Judge `fixed22` green-now | 0 failed / 14 passed |
| Full suite, **integrated tree**, import resolution verified | 1729 passed / 0 failed |
| Mutation sweep | **14 mutations across every requirement area V1–V6**: 12 killed, 2 classified equivalent, 1 property documented unguarded |
| Containment probe | 24 adversarial input shapes, all contained |

Judge trajectory: 14 red at base → 3 → 2 → 1 → 0 (`fixed19`), then reopened twice
by adversarial review and closed again at `fixed22`.

## Requirements

| Req | Status | Note |
|---|---|---|
| R1.1–R1.4 | Met | Seam contract defined in Architecture Amendment 1 |
| **R1.5** | Met after **four** containment escapes | relative traversal; `KIN_PROJECT` ahead of binding; explicit arg ahead of binding (resolved to the real repo); git-walk ascending past the root. Closed with one exit invariant plus skipping the probe under a binding. |
| R2.1 | Met after a **release-blocking regression** | the per-row tracker discarded any weight raise between folds (0.9 → 0.3674 where 0.30.1 preserves 0.8931) |
| R2.2–R2.5 | Met | mutation-verified |
| R3.1 | Met | note/flag identity; my redirect ruling retracted (Amendment 4) |
| R4.1, R4.2 | Met | flock serialization and no-raise contract both mutation-verified |
| R5.1 | Met | disclosure channel fixed per output mode (Amendments 5, 6) |
| R6.1 | Met | reachable under a binding; was an oracle defect, not implementation |
| I1–I6 | Held | `SCHEMA_VERSION` remains 7 |

## What this verdict does not establish

1. **Every adversarial pass found something.** Three times I judged this work
   done — at `fixed19`, `fixed20`, and again after the first mutation sweep — and
   each time a *new lens* found real defects: a data regression, four containment
   escapes, and two decorative guards. The passes stopped finding defects when I
   ran out of lenses, which is not the same as the code running out of defects.

   Mutation coverage is now complete across V1–V6 — the last four areas (R3.1,
   R5.1, R6.1, and the R1.1–R1.4 seam) were mutated after this verdict was first
   drafted, because the draft claimed verification "to the limit of the lenses
   available" while those areas had never been mutated at all. All four killed;
   the R1.4 no-op-unbind mutation reddened four seam controls, and R6.1 was killed
   by the very test batch0 had to descope. Completeness of *this* lens is now a
   fact rather than an impression — which says nothing about lenses not yet
   invented.

2. **Six amendments, all authored after ratification** while lanes were coding.
   Amendment 6 retracts Amendment 5, written about an hour apart, both mine.
   Phase 1 produced documents shaped like specifications that had not yet done a
   specification's work.

3. **One invariant is knowingly unguarded** — the `w_stored` consistency fix. It
   is correct and it stays, but reverting it moves the observable weight by
   nothing at practical scale (~7e-7 over 400 folds against a 4-dp quantity), so
   no honest oracle can carry it. Recorded rather than faked.

4. **Two defense-in-depth controls are unobservable** while their primaries hold
   (the exit clamp, the zero-interval no-op guard). They stay; no test can prove
   they earn their place.

5. **The independent-audit surface did not merely degrade — it went silently
   absent, and the control built to notice that failed through itself.** Five of
   sixteen orchestrator responses are 33-byte "(orchestrator invocation failed)"
   stubs, with **zero** dead-wake records, all timestamped 06:13Z onward: the
   entire endgame, the adversarial review round, and the factory work. The seat
   whose charter is auditing the Validator was gone for ~31% of its wakes during
   precisely the window of the most consequential judgments, while reporting
   itself healthy.

   The cause is this run's signature shape, in a control I added *during* this run
   after the same seat died once already: the invocation was wrapped in
   `|| echo "(orchestrator invocation failed)"`, which discards the exit status and
   returns a non-empty string matching none of the phrases the detector greps for.
   It watched for symptoms it imagined instead of the failure it existed to catch.
   Fixed in `~/Code/factory` by branching on the exit code, with the phrase match
   demoted to a secondary net, and drilled.

   Also: simulacrum is hard-blocked by the shared spend cap until 2026-09-01 — the
   second consecutive run in which that cap removed an independent check. One
   reviewer refused for a missing dispatch file; another timed out on a full-diff
   payload and had to be re-scoped.

   The code evidence in this verdict stands on its own — every finding was
   established by direct probe or mutation, none of it routed through the
   orchestrator. What was missing is the check on the Validator's *judgment*, which
   is an argument for founder review rather than for this document's sign-off.

## Residuals

- `--json` with no results emits nothing on stdout rather than an empty array
  (pre-existing 0.30.1 behavior, out of scope, logged in Amendment 6).
- The coder lane was asked twice for critical feedback and returned status both
  times; the pre-endgame feedback loop did not function on this lane.
- Factory learnings written but uncommitted: `harness/mutate.sh`,
  `docs/practices/oracle-quality.md`, and corrections to VALIDATION-DIRECTIVE
  items 7 and 8 — item 7 previously mandated a rule this run disproved.

## Recommendation

Release 0.31.0 is defensible on the evidence. It should be a human decision, not
mine, because the defect-discovery curve never flattened on its own — it flattened
when I stopped finding new ways to look.
