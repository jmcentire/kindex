# Strategy draft — Kindex verified resume and capture quarantine

Status: pre-authority review. This document is a bounded review subject, not an
intent artifact and not permission to implement.

## Outcome

Kindex will remain the durable, auditable memory layer. It will not attempt to
own host context clearing, execution replay, worktree isolation, runtime
sandboxing, or semantic truth adjudication. This slice hardens the state Kindex
does own:

1. Resume projections are deterministically bounded and admit only currently
   trusted linked knowledge by default.
2. Automatic PreCompact extraction is quarantined as review candidates and has
   no path to durable nodes or edges.
3. Verification, valid-time, invalidation, and explicit contradiction state are
   machine-readable and correctable through CLI and MCP surfaces.

## Authority and criticality

- The user's implementation request authorizes this build, but no human has
  signed the derived Product, Architecture, or Testing artifact. The eventual
  verdict must say `local AI-ratified; human artifact signature absent`.
- The durable graph is Standard, local, versioned, and recoverable. Migration
  integrity and accidental automatic promotion are blocking data-integrity
  controls. This is not classified as a regulated/compliance system.
- Kindex context remains data. It is never product intent, test authority,
  target state, tool authority, or a verdict.

## Forced design decisions

### D1 — Typed schema v8, not governance hidden in `extra`

Add nullable node columns `verified_at`, `verified_by`, `prov_method`,
`valid_at`, and `invalid_at`. Add a dedicated `capture_candidates` table.
Legacy rows remain NULL/unverified. `prov_when` keeps its current meaning.
Migration is additive, idempotent, and one fail-loud SQLite transaction; the
schema version advances only inside the successful transaction.

Rejected alternative: store the new fields in `nodes.extra`. That avoids a
migration but makes trust state weakly typed, harder to inspect, and easy to
silently omit from future query paths.

### D2 — Typed quarantine, not suggestions or bulk inbox learning

`compact-hook` inserts immutable pending candidate payloads. It never calls
`add_node` or `add_edge`. The suggestions table remains bridge-only because its
dream path may auto-apply. `kin learn --from-inbox` remains a separate explicit
bulk-extraction feature and is not a review gate.

Each candidate records an ID, proposed title/content/type/domains/connections,
source and payload digests, status, created/updated/expiry times, structured
conflict IDs/codes, terminal disposition, review metadata, and created node ID.
Raw transcript paths are not stored. Pending payloads are plaintext within the
existing local SQLite trust boundary.

Candidate states are `pending`, `conflicted`, `accepted`, `rejected`, and
`expired`. Accept/reject/prune use `BEGIN IMMEDIATE`, reread state, and perform
one compare-and-transition. Terminal transitions clear title/content/type/
domains/connections, retaining only a minimal receipt with digests rather than a
raw source reference. Explicit erase can delete that receipt. No legal
immutability or encryption claim.

Rejected alternatives: files as the canonical review queue; flagged graph
nodes; or the bridge-suggestion table. Each permits an existing consumer to
mistake candidate data for knowledge or lacks atomic review transitions.

### D3 — Explicit trust predicate, no semantic oracle

A linked node is eligible for trusted resume only when all are true at the
evaluation time:

- `status == active`;
- `verified_at`, `verified_by`, and `prov_method` are non-empty;
- `valid_at` is absent or not later than evaluation time;
- `invalid_at` is absent or later than evaluation time;
- it has no `contradicts` edge to another node that independently satisfies the
  preceding current-and-verified checks.

Legacy, stale, future-valid, invalidated, and unresolved-contradictory nodes are
omitted and counted by distinct machine reason. When two independently current,
verified nodes contradict one another, both are omitted and counted as
`mutual_contradiction`; this is an intentional denial, not an error. Ordinary
search behavior does not change. Unverified contradiction endpoints cannot
suppress a verified node. Kindex checks only explicit edges and same-title
collisions; absence of a warning is not proof of semantic consistency.

Node `verify` records asserted reviewer, method, verification time and optional
valid interval. Node `invalidate` sets an audit timestamp/reason without
deleting the node. In this slice, resolving a contradiction means invalidating
or superseding one endpoint; Kindex does not choose the winner.

All temporal inputs are timezone-aware RFC 3339 and normalized to UTC. Naive
timestamps, leap-second notation, and an interval with `invalid_at <= valid_at`
are rejected. Evaluation time is captured once per operation and may be injected
by a test; equality at `valid_at` is current, equality at `invalid_at` is not.

Ordinary search remains backward-compatible, but CLI and MCP search gain an
explicit `trusted_only` option using this same predicate. Help text must state
the dual view: default search is recall, while trusted resume and trusted-only
search are admission-controlled projections. This is a compatibility boundary,
not a claim that unverified search results are safe to act on.

### D4 — Honest portable budget

`format_resume_context` accepts an optional counter callback. `max_tokens` is
retained for API compatibility, but its exact guarantee is against that selected
counter. The default counts UTF-8 bytes as deterministic Kindex budget units; it
is a provider-neutral size ceiling, not a universal model-token measurement.
Callers that need a provider-token guarantee must inject that provider's exact
counter. The complete emitted string, including omission/truncation notices,
must satisfy `counter(output) <= max_tokens` with no margin. A non-positive
budget returns an empty string.

Packing order is deterministic: data-not-authority warning and session identity,
current focus/status, remaining work, current segment, description/project,
recent completed segments newest first, omission counts, then eligible related
knowledge. Items that do not fit are omitted; the final included field may be
truncated. The marker itself must fit or disappear. The exact truncation
algorithm is implementation guidance, not an observable requirement. Values are
normalized into labeled Markdown lines; truncation cannot split a label, emit a
partial list item, break UTF-8, or copy terminal control characters.

Rejected alternative: characters divided by four. It is an estimate presented
as a guarantee and cannot support a hard bound across providers.

### D5 — CLI and MCP parity without raw database access

Expose candidate `list`, `show`, `accept`, `reject`, `prune`, and `erase`; node
`verify` and `invalidate`; and trusted tag resume through both CLI and MCP.
`show` returns a review token: a digest over the immutable payload plus the
status/update/verification/validity state of explicitly referenced and
same-title durable nodes. `accept` requires that token and recomputes it while
holding the write transaction. A mismatch fails stale without creating or
overwriting a node. The token proves snapshot freshness, not caller identity or
authorization. Independently of token validation, `accept` performs a same-title
collision check inside the transaction; namespace integrity does not depend on
the review token having observed a later-created node.

Accept requires non-empty `reviewed_by` and `prov_method`, validates interval
ordering, rechecks candidate expiry and every explicit contradiction inside the
same `BEGIN IMMEDIATE` transaction, blocks unresolved explicit contradictions
and same-title collisions, atomically creates the verified node plus only
resolvable proposed edges, then terminalizes the candidate. A same-title block
is intentionally conservative because current Kindex capture/dedup paths already
use title as a lookup key; a genuinely distinct entity must be re-submitted with
a disambiguated title rather than overridden in this automated flow. A review
model may recommend an action but no automatic path may call accept.

All terminal transitions use a conditional update over the expected source
status and verify exactly one affected row. `BEGIN IMMEDIATE` serializes every
SQLite writer, so no node or edge writer can enter between the in-transaction
conflict check and commit; SQLite row locks or `SELECT FOR UPDATE` are neither
required nor claimed.

Candidate content remains untrusted on display. JSON/MCP surfaces use structured
serialization, and human CLI output replaces terminal control characters and
uses explicit delimiters. This is output safety, not content censorship.

## Failure posture and retention

- Hook extraction/provider failures and database contention remain fail-soft for
  the host event. They may lose a candidate; they must never bypass quarantine.
  The existing degraded ledger records the failure where available. This slice
  does not claim zero-loss capture under database or disk failure. Each candidate
  insertion is its own atomic transaction: either one complete pending row is
  visible or no row is visible. A failed insert must not leave a partial row or
  emit output that implies capture succeeded. Degraded-ledger recording is a
  separate best-effort write and is never evidence that a candidate exists.
- Pending candidates have a bounded default expiry. Prune racing with accept is
  deterministic: the transaction that commits first wins; the second observes
  terminal/expired state and fails or skips. `show` is not a lease and does not
  extend retention.
- Terminal receipts use bounded machine disposition/conflict codes and never
  retain a free-form review note.
- Candidate and reviewer erasure/pseudonymization are mechanics only; no GDPR,
  legal-retention, compliance, or encryption-at-rest certification is claimed.
- Reviewer identity is an asserted audit value inside the existing local process
  trust boundary. This slice adds no CLI/MCP authentication or authorization;
  possession of a review token does not grant or prove review authority.

## Independent evidence plan

The Tester and Coder receive the same frozen three-artifact contract in separate
Codex contexts and separate worktrees, with no shared implementation/test view.
The Tester owns acceptance tests and historical run-test disposition; the Coder
owns implementation only. The root Validator integrates and runs the judge.

Required red-now evidence:

- controlled compact extraction creates a graph node at base instead of only a
  candidate;
- resume output exceeds a small supplied counter budget at base;
- legacy/unverified, stale, future-valid, invalidated, and mutually contradicted
  linked nodes are admitted or cannot be represented at base;
- stale review, concurrent terminal transitions, invalid intervals, same-title
  collision, expiry, and payload erasure are denied;
- valid-time boundaries are exact: `valid_at == evaluation_time` is admitted,
  while a value one representable unit later is excluded;
- CLI and MCP reach the same behavior without direct database mutation.
- default search remains unchanged while `trusted_only` search and trusted
  resume share one eligibility predicate;

Required green/effect evidence:

- each new test proves fixture reachability and assertion discrimination;
- base revision fails for the named reason and head passes;
- at least one protection is temporarily mutated and the judge test turns red;
- migration from a real v7 fixture and fresh v8 creation both pass; injected DDL
  failure leaves version 7 and no partial v8 surface;
- the full existing suite passes, with historical schema-7 assertions revised
  only where the new user-authorized schema contract makes their old global
  wording false;
- bounded review of the exact patch by Advocate and Simulacrum yields no open
  blocking finding.

## Explicitly rejected Constrain synthesis claims

There is no new HTTP service, port 8080, TCP/protobuf data plane, authentication
credential, immutable compliance log, regulated tier, soak duration, or
mandatory-human-only gate in this slice. CLI/MCP functions call the existing
local library and SQLite store. The review token is optimistic concurrency data,
not authentication.

## Residual risks and non-claims

- Explicit conflict detection is incomplete by design; hidden semantic conflicts
  remain the reviewer's risk.
- Asserted reviewer identity is auditable text, not cryptographic identity.
- Ordinary search can still return unverified nodes; changing global retrieval is
  a separate compatibility decision.
- The change does not repair corrupted model context or guarantee that an agent
  interprets a correct resume projection correctly.
- No release, deployment, registry update, or hosted behavior is part of this run.
