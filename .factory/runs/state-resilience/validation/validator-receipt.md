# State-resilience Validator receipt

Date: 2026-08-18

## Authority and subject

- Frozen base: 666e20864fdcd3a21d5683f2c23085cf32d23257.
- Integrated implementation head before this receipt: b9752d3fd686bb15a3a1fa56228fcbf942f35f0d.
- Product digest: ece927181a13c89d8fdfeb927656e8e65157367bac333051b76578d237a93ca9.
- Architecture digest: c96a1839b7b722bb3f281a5ed62d1ed95598cee69fcfc1eae42bb8d2c63bac39.
- Testing digest: 9541808a24e3068352efb3cb137d6317a2028c2223894039b1d86180b8d7e589.
- Tool-policy digest: b9776a7e7d5c58fd63337d557096b16bac49d6dd4b39802757774f14bbec5f96.
- No human signature was supplied. This receipt is engineering evidence, not a
  human ratification, release, deployment, or production-readiness claim.

The Coder and Tester worked from the frozen artifacts in separate worktrees.
The Tester did not read implementation or run the judge. The Coder did not
read tests or Validator output and did not run pytest.

## Baseline and base-red attribution

- The frozen base suite passed: 1738 passed in 72.08 seconds.
- With the independently authored red-now tests applied to frozen-base source:
  75 failed, 50 passed, 1672 deselected in 8.62 seconds.
- An unmarked legacy-recall canary still passed on the frozen base.
- The workstation editable-install alias was reproduced. Every authoritative
  integrated run below used PYTHONPATH=$PWD/src so it executed this worktree,
  not /Users/jmcentire/Code/kindex/src.

## Integrated verification

- Red-now suite after integration and final contract correction:
  125 passed, 1672 deselected. The deselected tests were not counted as passed
  in this marker run; the subsequent full run executed all 1797 tests.
- Candidate and surface focus after the final expiry-predicate correction:
  32 passed.
- Complete final suite: 1797 passed in 79.48 seconds, with no retry.
- compileall, make lint, artifact checksum verification, git diff --check, the
  changed-test allowlist, the no-new-provider/placeholder scan, the
  no-interpolated-SQL scan, and source authority guards all passed.
- An isolated PEP 517 wheel build succeeded as
  kindex-0.31.0-py3-none-any.whl with 61 files. The wheel contains trust.py,
  store.py, cli.py, and mcp_server.py.
- A database created by the exact frozen-base source reported schema version 7.
  Opening that same database with the final source upgraded it to version 8,
  preserved both nodes and the edge, added the trust columns, and created the
  candidate table.

## Mutation receipt

Each temporary mutation was applied in a detached worktree, observed, restored,
and never committed.

1. Replacing compact-hook candidate staging with direct add_node persistence
   failed the automatic-promotion boundary control.
2. Bypassing the review-token comparison failed the stale-state control.
3. Removing the live terminal-state protections failed the one-winner race
   control.
4. Allowing an active but unverified contradiction endpoint to suppress a node
   failed the poison-resistance control.
5. Disabling incremental budget admission failed the byte, code-point, and word
   counter hard-bound controls.
6. Removing only the conditional terminal SQL predicate was behaviorally masked
   by BEGIN IMMEDIATE plus the in-transaction precheck, but failed the exact
   Validator source guard.
7. Removing only the final defensive budget recheck was behaviorally masked by
   incremental admission, but failed the exact Validator source guard.

The last two controls are correctly recorded as defense-in-depth static
mutations rather than overstated behavioral mutations. Their source guards
inspect the final function bodies: one requires the conditional status/expiry
SQL predicate, and the other requires the final complete-output budget recheck.

## Exact-patch adversarial review

The first monolithic Advocate attempt reported an input truncation at 200,000
characters and is not counted as exact-patch evidence. The final patch was then
partitioned without omission into:

- production source and user documentation: 139,927 characters;
- tests: 135,832 characters;
- frozen Factory artifacts: 95,501 characters before this receipt.

Advocate reviewed every partition through the local Ollama OpenAI-compatible
endpoint with qwen3.5:cloud. Simulacrum reviewed every partition with
claude-sonnet-4-6; its key resolver selected WANDER_ANTHROPIC_API_KEY because
ANTHROPIC_API_KEY was unset. Both model routes completed successfully.
These probabilistic reviews are non-authoritative pressure tests. The verdict
rests on frozen authority, deterministic tests, mutations, static guards, the
historical-schema probe, and the isolated build rather than model assent.

High and blocking findings were dispositioned as follows:

1. Reviewer authentication and impersonation: accepted as an explicit local
   trust-boundary limitation, not an omitted claim. Review tokens prove
   freshness only; verify and invalidate identities are asserted audit text.
   Full CLI/MCP authentication remains outside this authorized slice.
2. Untrusted candidate content reaching MCP clients: accepted residual risk,
   with the declared control intact. candidate_show explicitly labels the exact
   payload untrusted and returns serializer-encoded structured data; CLI output
   neutralizes terminal controls. Semantic rewriting would destroy the exact
   review subject and is not claimed to defeat prompt injection.
3. SQLite row-lock, TOCTOU, deduplication, prune, and accept-race claims:
   rejected as inapplicable. Each mutable read occurs after BEGIN IMMEDIATE,
   which serializes competing SQLite writers; concurrent readers cannot mutate
   the reviewed state. Focused races, base-red tests, and mutations exercised
   the one-terminal-winner, rollback, freshness, and deduplication properties.
4. Missing expiry recheck: one exact Architecture A5 mismatch was valid. The
   accept, conflict, and reject conditional transitions were corrected to use
   expires_at greater than the operation-scoped instant in commit b9752d3.
   Focused and full suites passed afterward.
5. Transactional DDL and crash-recovery claims: rejected on the tested SQLite
   3.53.4 runtime. SQLite DDL was exercised inside one transaction, failure was
   injected at every declared migration step, and the version-7 database probe
   upgraded with data preserved. Disk-full journal recovery remains a declared
   residual risk. State resilience here is a bounded agent-memory property, not
   a claim of universal storage fault tolerance.
6. FastMCP cross-thread SQLite claim: rejected for the installed MCP runtime.
   Its FuncMetadata executes synchronous tools directly on the server event-loop
   thread rather than a worker pool; synchronous calls are therefore serialized.
   That same design can block the event loop under load and is retained as a
   pre-existing availability limitation; no load, latency, or SLO claim is made.
   Broader multi-process/runtime isolation is not claimed.
7. Mixed legacy timestamp representations: not a trust-admission defect.
   Trust fields use strict normalized UTC RFC 3339. Legacy created-at strings are
   preserved, and review-token edge rows are ordered by ID rather than timestamp.
8. Default trusted resume as a breaking change: accepted and intentional. The
   changed default is the authorized product outcome, is documented, and has no
   hidden unsafe fallback. This compatibility break is limited to resume;
   ordinary search remains backward compatible unless trusted-only is requested.
9. max_tokens naming versus byte units: accepted compatibility debt, not a
   false token claim. The retained parameter is explicitly documented as an
   exact UTF-8 byte budget by default; callers needing provider-token guarantees
   must inject the exact deterministic provider counter.
10. Same-title collisions: accepted conservative automated-capture policy, not
    a semantic-identity claim. Titles are stripped and compared case-insensitively;
    distinct homonyms require a disambiguated title.
11. Operation-clock duplication: rejected. The frozen architecture deliberately
    requires independent operation_now seams in both CLI and MCP adapters so
    each surface can capture and inject one operation instant.
12. Store ambient-time defaults and direct resume defaults: accepted library
    behavior. Hermetic tests inject declared instants; CLI/MCP time-dependent
    operations pass their captured adapter instant and expose no caller-controlled
    backdating option.
13. Test monkeypatch leakage: rejected. The tests use pytest's monkeypatch
    fixture, which restores bindings automatically.
14. Static compact-hook guard blindness: accepted only as a limitation of one
    defense-in-depth check. Independent behavioral tests assert zero durable
    nodes and edges, while the static guard additionally denies direct and
    accept reachability.
15. Unclosed-store and WAL hash concerns: rejected by the zero-retry full run and
    explicit close/checkpoint fixture boundaries. No lock leak occurred in the
    concurrent Store tests.
16. Version-7 fixture drift: addressed with the additional exact-base database
    migration probe described above.
17. Optional MCP skips hiding coverage: not present in this environment. The
    MCP extra was installed and the complete suite reported no skips.
18. prov_who representation inconsistency: rejected. Existing Store.add_node
    stores prov_who as canonical JSON and decodes it in _row_to_dict; candidate
    promotion uses the same representation.
19. Separate normal versus trusted expiry dates: accepted compatibility boundary.
    Ordinary recall retains its pre-existing semantics; trusted projections use
    the explicit operation-scoped UTC evaluation date.
20. N+1 contradiction lookup: accepted bounded performance debt, not a
    correctness or release blocker for the bounded resume/search candidate
    windows in this local CLI/MCP slice.
21. Claims that the receipt date, SQLite version, or model name are speculative:
    rejected as reviewer training-cutoff error. 2026-08-18 is the execution
    environment's current date, and SQLite 3.53.4 plus the named model routes
    were observed successfully in this run.
22. Claims that marker arithmetic counted deselected tests as passing: rejected.
    The marker run executed 125 selected tests; the later unmarked full run
    independently executed all 1797 tests.

No high or blocking review finding remains undispositioned.

## Scope conclusion

This receipt supports the local implementation claim only: Kindex now has an
atomic schema-v8 trust layer, quarantined automatic captures, explicit review
transitions, valid-time verification/invalidation, poison-resistant
contradiction admission, bounded trusted resume, and opt-in trusted recall.

It does not authorize or prove package publication, release, deployment,
production telemetry, full authentication, runtime sandboxing, semantic
contradiction inference, time-travel execution, or a human-signed Factory gate.
