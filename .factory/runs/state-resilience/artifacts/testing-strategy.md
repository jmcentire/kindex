# Testing and Monitoring Strategy — state-resilience run

Authority state: local AI-ratified oracle contract derived from the Product and
Architecture Specifications. No human signature was supplied. The Tester owns
test authoring only; the root Validator owns base/head execution, mutation
execution, artifact integrity, and the verdict.

Base revision: `666e20864fdcd3a21d5683f2c23085cf32d23257`.

Amendment 1 (Tester-raised specification defect): bind time-dependent CLI/MCP
tests to the Architecture A9 `operation_now` seams rather than ambient time.

Amendment 3 (Validator-raised specification defect): authorize the one inherited
session-resume assertion that required an unverified linked node to appear by
default. Its original linked-node purpose remains, but its fixture must now meet
the Product P1 trust-admission precondition.

## T0 — Lane and oracle rules

- **T0.1 Isolation.** The Tester receives only the three frozen artifacts, their
  digests, the base SHA, repository build/test metadata, documentation, and the
  existing tests. It MUST NOT inspect `src/kindex/**`, the Coder worktree, Coder
  messages, implementation diffs, or a judge run.
- **T0.2 Ownership.** New acceptance tests are Tester-owned. The Tester may edit
  only the historical tests explicitly dispositioned in T1. The Coder may not
  see or edit Tester tests. The Validator reports only bare requirement-level
  failures to the Coder.
- **T0.3 Marking.** New tests asserting changed behavior carry
  `@pytest.mark.red_now`. Guard tests expected to pass at base are unmarked.
- **T0.4 Falsifiability.** Every new requirement-carrying test docstring names:
  the requirement ID, fixture reachability, the forbidden and demanded values,
  and the smallest mutation/reversion that turns it red. A test that cannot
  discriminate is not handed over.
- **T0.5 Hermeticity.** No network, provider key, live graph, home-directory
  state, sleep, or wall-clock-dependent assertion. Use temp SQLite stores,
  stripped provider env, deterministic extraction fallback or a test-owned fake,
  explicit Store instants, and the declared adapter `operation_now` seams for
  time-dependent CLI/MCP calls.
- **T0.6 Interface-only oracle.** Tests exercise the interfaces declared in the
  Architecture Specification. If a declared interface is incoherent or absent
  in a way that prevents authoring, report `SPEC_DEFECT` rather than reading the
  implementation to guess.
- **T0.7 No test execution by Tester.** The Tester may compile/collect its own
  files only if the environment requires it to produce syntactically valid
  artifacts; it does not execute the judging suite or observe base/head output.

## T1 — File ownership and prior-oracle disposition

New files:

- `tests/test_state_resilience_schema.py`
- `tests/test_capture_candidates.py`
- `tests/test_trusted_resume.py`
- `tests/test_trusted_search.py`
- `tests/test_state_resilience_surfaces.py`

Existing files the Tester may revise, and only for the named superseded
expectation:

- `tests/test_compact_hook.py`: preserve envelope/transcript/fail-soft oracles,
  but replace expectations that automatic compact-hook creates nodes with the
  new expectation of zero nodes and complete pending candidates.
- `tests/test_batch0_capture.py`: preserve issue-14 envelope precedence,
  malformed/truncated transcript, and setup-hook oracles; revise only the old
  "minted node" observation to "staged candidate, zero node". Update its
  historical provenance note to identify this superseding run rather than
  pretending the batch0 requirement still controls promotion.
- `tests/test_v8_decay_schedule.py`: replace the historical assertion that
  decay globally requires schema version 7 with an assertion that decay causes
  no schema mutation relative to the current declared version-8 inventory.
- `tests/test_batch0_decay.py`: same disposition for its exact version/inventory
  guard; decay behavior remains unchanged and all other assertions are read-only.
- `tests/test_sessions.py`: revise only
  `TestResumeContext.test_resume_includes_linked_nodes` so its linked concept is
  explicitly verified before asserting default resume inclusion. Preserve the
  test's session/link reachability and title-in-output purpose; do not introduce
  an unsafe fallback for unverified nodes.

No other existing test may be edited without a Validator-issued specification
amendment. The old assertions are not being weakened to fit code; the new user-
authorized Product Specification makes their run-scoped promotion/schema claims
false as global invariants.

## T2 — Declared test-visible interfaces

The Tester may rely on the exact Store and `kindex.trust` signatures in the
Architecture Specification, the CLI commands in A9, and these MCP callables:

- `candidate_list`, `candidate_show`, `candidate_accept`, `candidate_reject`,
  `candidate_prune`, `candidate_erase`, `verify`, `invalidate`;
- existing `search` and `context` with additive `trusted_only=False`;
- existing `tag_resume(name, tokens)` using trusted resume by default.

Candidate dictionaries expose the schema field names in A2 plus a computed
`review_token` on show. Node dictionaries expose the five new typed fields.
Human error strings begin `Error: <machine_code>:`; JSON/MCP behavior may be
asserted on exact machine fields, not prose paragraphs.

The declared in-process clock seams are `kindex.cli.operation_now` and
`kindex.mcp_server.operation_now`. They are test bindings only; no CLI/MCP caller
may submit its own operation time.

## T3 — Schema and migration suite

`tests/test_state_resilience_schema.py` covers P6 and D1:

1. Fresh store reports version 8 and contains all five node fields,
   `edges.updated_at`, `suggestions.kind`, candidate table/check/indexes.
2. A real version-7 fixture with representative node, edge, suggestion,
   reminder, activity, and pheromone data upgrades to the identical inventory;
   values survive; new node fields are NULL; suggestion kind is bridge; edge
   update time is backfilled.
3. Reopen after upgrade performs no DDL/data change.
4. Parameterized injected failure at each v8 migration statement leaves meta
   version 7, original data intact, and no partial v8 object/column/index.
   Injection must reach the migration statement rather than fail before open.
5. A migration lock/contention error is visible to an interactive Store caller;
   no fake version-8 stamp appears.

Falsifiability mutation: move the version update outside the transaction or
swallow one injected DDL exception; rollback test must turn red.

## T4 — Candidate quarantine and state-machine suite

`tests/test_capture_candidates.py` covers P2/P3:

1. Direct add stores one complete canonical pending candidate, stable payload
   digest, source digest, UTC times, and default seven-day expiry.
2. Duplicate live payload returns the existing ID; after terminal erasure a new
   capture may create a new candidate receipt.
3. Bad type, empty title/content, invalid connection type, NUL/control input,
   malformed source digest, and non-positive TTL are denied with zero rows.
4. List never includes payload fields unless the declared show interface is
   used; show returns exact payload plus review token.
5. Accept requires reviewer/method/token, validates time, creates one verified
   node and resolvable edges, creates no implicit endpoint, never overwrites,
   and clears raw candidate payload in the same committed result.
6. Changed payload status, same-title graph state, referenced-node verification,
   validity, or explicit contradiction edge makes a prior review token stale.
7. Same-title collision is denied independently even with a freshly recomputed
   token.
8. Explicit contradiction proposal to a current verified node returns conflict,
   stores only IDs/codes, and creates no node/edge. An unverified counterpart
   does not constitute a trusted contradiction block.
9. Expiry is rechecked inside accept. `now == expires_at` is denied.
10. Reject and prune clear every raw payload field and retain only the allowed
    receipt fields, including source/payload digests; erase removes the receipt.
11. Two independent Store connections race accept/reject, accept/prune, and two
    accepts. Exactly one terminal outcome commits; node count is at most one;
    losers receive a typed outcome and no lock leak.
12. Candidate display containing ANSI, carriage return, and HTML-looking text is
    structurally encoded/neutralized while ordinary newlines in content survive
    structured JSON.

Falsifiability mutations: bypass review-token comparison; remove the
`status IN (...)` predicate; or stop clearing `content`. Each named test must
turn red under its corresponding mutation.

## T5 — Automatic compact-hook suite

The revised `tests/test_compact_hook.py`, revised
`tests/test_batch0_capture.py`, and focused additions in
`tests/test_capture_candidates.py` cover P2:

1. A deterministic transcript extraction yields zero durable nodes/edges and at
   least one content-bearing pending candidate derived from transcript text.
2. Parseable hook envelope still preempts `--text`; missing transcript still
   yields nothing; non-envelope stdin still uses explicit text.
3. Envelope metadata, transcript path, and session ID never become candidate
   title/content/source reference. The source field is a digest.
4. Title-only keyword hints create neither candidate nor node.
5. Garbage/truncated transcript lines do not erase extractable remainder.
6. Forced candidate insert failure exits zero, leaves no partial candidate or
   graph node, contains no success wording, and does not fall back to old direct
   persistence.
7. Static reachability guard parses/inspects `cmd_compact_hook` sufficiently to
   prove the extraction loop cannot call `add_node`, `add_edge`, or candidate
   accept. This guard is supplemental; behavioral tests remain authoritative.

Base-red attribution: at the frozen base, the ordinary transcript fixture
creates a durable node and exposes no candidate table/API. That is the named
failure, not a collection accident alone.

## T6 — Verification, valid-time, and contradiction suite

`tests/test_trusted_resume.py` covers P1/P4:

1. Verify records typed fields without changing `prov_when`; invalidate records
   an exclusive end and does not delete/change content/status.
2. Empty audit values, naive time, leap second, malformed offset, and
   `invalid_at <= valid_at` are denied.
3. Exact boundaries use one injected instant:
   `valid_at == at` eligible, one microsecond later not-yet-valid;
   `invalid_at == at` invalidated, one microsecond later eligible.
4. Legacy/unverified, not-yet-valid, invalidated, inactive, and candidate records
   are absent from trusted resume with distinct counts.
5. Two current verified linked nodes with bidirectional or one-way
   `contradicts` are both absent and counted `mutual_contradiction`.
6. A verified node contradicted only by an unverified, future, invalidated, or
   inactive endpoint remains eligible (poison-denial cases).
7. Invalidate or supersede one current contradiction endpoint and the remaining
   current verified node becomes eligible.
8. The resume warning says context is data, not authority.

Falsifiability mutation: let any active contradiction endpoint suppress without
first passing verification/current checks; poison-denial test turns red.

## T7 — Budget and structural projection suite

`tests/test_trusted_resume.py` also covers R1.1-R1.4:

1. Supply exact counters with deliberately different units (UTF-8 bytes,
   Unicode code points, word counter). Every complete output is within each
   selected budget; non-positive returns exactly empty.
2. Test budgets just below/at/above complete line and marker sizes, including a
   budget too small for any warning.
3. Oversized focus, description, remaining item, segment summary, and Unicode
   related title cannot overflow, split UTF-8, emit a dangling Markdown list
   prefix, or retain terminal controls.
4. Lower-priority history/knowledge disappears before current focus/remaining.
5. Same graph/time/counter/budget returns byte-identical output; changing only
   evaluation time changes only eligibility-derived sections/counts.
6. The default counter assertion is byte-budget behavior only. No test labels it
   as a universal provider-token measurement.

Base-red attribution: construct an oversized tag and call the existing function
with a small injected/default budget; base ignores the argument and exceeds it.

## T8 — Trusted search/context suite

`tests/test_trusted_search.py` covers P5:

1. Default `hybrid_search`, CLI search/context, and MCP search/context return the
   same IDs/order/top-level shape as base for a mixed legacy fixture.
2. `trusted_only=True` uses the exact trust predicate, preserves ranked order of
   survivors, and continues through the existing candidate window until top-k
   trusted results or genuine window exhaustion.
3. Mutual contradictions and poisoned unverified endpoints match resume
   behavior; no duplicate predicate is allowed to drift.
4. Human disclosure states trusted omissions only in trusted mode; JSON remains
   parseable and its existing top-level shape remains unchanged.
5. No LLM/vector-provider call is needed for trust filtering; vector mode may be
   stubbed to prove post-retrieval parity.

Mutation: invert the trusted flag default; compatibility guard turns red.

## T9 — CLI/MCP parity suite

`tests/test_state_resilience_surfaces.py` covers P3/P4/P5:

1. Every candidate and verification lifecycle action is reachable in CLI and
   MCP against the same temp store contract.
2. Required arguments, machine errors, terminal status, review token, created
   node ID, timestamps, and trust fields agree across adapters.
3. MCP/JSON serialization of hostile payload is structured data; human CLI
   output has no raw ANSI/control sequence.
4. `tag_resume` uses trusted filtering and hard budget by default; it offers no
   hidden unsafe fallback.
5. Search/context trusted-only flags are additive and false by default.

The Tester may monkeypatch only declared store access/config binding seams to
route MCP to the temp store. It may not access the developer's live graph.

## T10 — Validator-only execution and mutation receipts

The Validator, not the Tester, performs:

1. Artifact digest/provenance verification and negative scan for mutable-context
   authority.
2. Test collection in the integrated validation worktree.
3. Base run at the exact frozen SHA with Tester tests applied, recording each
   red-now failure reason and distinguishing expected missing-interface failures
   from unrelated collection errors.
4. Head run of focused suites, then `pytest tests/ -v` with zero automatic
   retry.
5. At least these temporary mutations, one at a time, never committed:
   remove compact quarantine; bypass review token; remove conditional terminal
   predicate; admit unverified contradiction endpoint; and disable final budget
   check. Each associated test must turn red. Restore and rerun focused tests.
6. Static scans for new LLM calls, direct automatic `add_node`/`add_edge`/
   accept reachability, TODO/stub placeholders, unsafe SQL interpolation, and
   changed existing tests outside T1.
7. Bounded Advocate and Simulacrum review of the exact final patch and explicit
   disposition of every high/blocking finding.

## T11 — Monitoring and release applicability

This is a local library/CLI/MCP change. No deployed service, production metric,
alert, SLO, live smoke, soak, package publish, or release is authorized. Runtime
signals in scope are existing structured activity/degraded records and CLI/MCP
machine outcomes. Release/public-package verification is `N/A — not requested`,
not evidence of deployment.

## Tester handoff format

The Tester returns only:

- commit SHA containing test-only changes;
- files changed;
- requirement-to-test/falsifiability ledger;
- existing-test dispositions made under T1;
- any `SPEC_DEFECT` with exact artifact section;
- a sentence confirming it did not read implementation or run the judge.

It does not return pass/fail, merge readiness, or implementation advice.
