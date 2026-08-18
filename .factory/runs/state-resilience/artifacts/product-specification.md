# Product Specification — state-resilience run

Authority state: local AI-ratified from the user's explicit request to devise
and implement a Kindex improvement strategy. No human signature was supplied.
That gap is binding on the verdict and may not be rewritten as human approval.

Base revision: `666e20864fdcd3a21d5683f2c23085cf32d23257`.

Criticality: Standard local data-management change, with two blocking
data-integrity controls: schema migration may not partially apply, and automatic
capture may not promote unreviewed knowledge. No regulated-system,
authentication, compliance, encryption, release, or deployment claim is made.

## P1 — Resume state is bounded and admission-controlled

- **R1.1 Hard bound.** Given a positive budget and a selected deterministic
  counter, the complete resume output MUST satisfy
  `counter(output) <= budget`. Notices and truncation markers count. There is no
  margin. A non-positive budget MUST return an empty string.
- **R1.2 Honest units.** The default counter MUST be deterministic and its unit
  MUST be documented. A caller needing a provider-token guarantee MUST be able
  to supply that provider's exact counter. Kindex MUST NOT describe an
  approximate character heuristic as an exact provider-token count.
- **R1.3 Deterministic priority.** Repeated calls against the same graph,
  evaluation time, counter, and budget MUST be byte-identical. Current session
  identity/focus and remaining work outrank history and related knowledge.
- **R1.4 Structural output.** A bounded projection MUST remain valid labeled
  Markdown: no split label, partial list prefix, invalid UTF-8, or terminal
  control character. If a truncation notice does not fit, it is omitted rather
  than causing overflow.
- **R1.5 Trust admission.** Related knowledge is admitted only when it is active,
  explicitly verified with asserted reviewer and method, currently valid, not
  invalidated, and not in an unresolved contradiction with another independently
  current verified node.
- **R1.6 Denial disclosure.** Omitted related knowledge MUST be counted by
  machine reason, distinguishing at least legacy/unverified, not-yet-valid,
  invalidated/expired, and mutual contradiction. Two current verified nodes
  joined by `contradicts` are both omitted and counted as mutual contradiction.
- **R1.7 Poison resistance.** An unverified or inactive contradiction endpoint
  MUST NOT suppress an otherwise eligible verified node. Staged capture data
  MUST never appear in resume output.
- **R1.8 Authority warning.** The projection MUST identify itself as contextual
  data, not instruction or intent authority.

## P2 — Automatic extraction is quarantined

- **R2.1 No automatic promotion.** A successful automatic PreCompact extraction
  MUST create reviewable candidate state and MUST create zero durable knowledge
  nodes and zero durable edges.
- **R2.2 Exact review subject.** A reviewer MUST be able to inspect the exact
  proposed title, content, type, domains, resolvable connection proposals,
  source digest, payload digest, capture time, expiry, status, and structured
  conflict evidence without re-running extraction.
- **R2.3 Isolation.** Candidate state MUST be absent from ordinary graph search,
  graph expansion, context formatting, resume, dream auto-apply, and node APIs.
- **R2.4 Atomic capture.** Each proposed candidate MUST be either one complete
  visible record or absent. A failed write MUST leave no partial candidate and
  MUST NOT emit a success signal.
- **R2.5 Host fail-soft.** Extraction, provider, parse, or candidate-write
  failure MUST NOT fail the host compaction event and MUST NOT fall back to
  direct node/edge creation. Candidate loss during database or disk failure is
  an explicit residual risk, not hidden as success.

## P3 — Review transitions are explicit, fresh, and atomic

- **R3.1 Review surfaces.** Candidate list, show, accept, reject, prune, and
  erase MUST be available through both CLI and MCP without direct SQL access.
- **R3.2 Fresh review.** Show MUST return a deterministic review-state token.
  Accept MUST require and recompute it against the candidate plus relevant
  durable state inside the write transaction. Changed relevant state MUST fail
  stale with no graph mutation.
- **R3.3 Token non-claim.** The review-state token proves snapshot freshness
  only. It MUST NOT be described as caller authentication, authorization, or
  proof of reviewer identity.
- **R3.4 Explicit provenance.** Accept MUST require non-empty asserted reviewer
  and verification method. Empty or whitespace-only values MUST be denied.
- **R3.5 Current candidate.** Accept MUST recheck candidate status and expiry
  inside the same write transaction. Expired, rejected, accepted, or erased
  candidates MUST create nothing.
- **R3.6 Conflict and collision denial.** Accept MUST deny an unresolved explicit
  contradiction and a same-title durable-node collision. A same-title collision
  is a conservative automated-capture boundary, not proof of semantic identity;
  a genuinely distinct entity must be submitted under a disambiguated title.
- **R3.7 Atomic promotion.** A successful accept MUST atomically create exactly
  one verified durable node, add only connection proposals whose endpoints can
  be resolved without creating another node, and terminalize the candidate. It
  MUST never overwrite a durable node.
- **R3.8 One terminal winner.** Concurrent accept, reject, and prune operations
  MUST yield at most one terminal winner. Losers MUST observe a typed stale,
  expired, or already-terminal failure; no lost update is allowed.
- **R3.9 Terminal minimization.** Accept, reject, and expiry MUST erase raw title,
  content, type, domains, and connection proposals from candidate storage. The
  source is stored only as a digest, never as a raw reference. A minimal receipt
  MAY retain IDs/digests, timestamps, machine disposition/conflict codes,
  asserted reviewer/method, and created node ID. Explicit erase MUST be able to
  remove the receipt.
- **R3.10 Untrusted display.** Candidate payload is untrusted data. Structured
  output MUST be encoded by the serializer, and human output MUST neutralize
  terminal control characters and visibly delimit the payload.

## P4 — Verification and valid time are first-class and correctable

- **R4.1 Verify.** Existing durable nodes MUST support an explicit verify action
  through CLI and MCP that records asserted reviewer, method, verification time,
  and an optional valid interval without changing original provenance time.
- **R4.2 Invalidate.** Existing durable nodes MUST support explicit invalidation
  through CLI and MCP that records time, asserted actor, and a bounded machine
  reason without deleting the node.
- **R4.3 Time contract.** Temporal inputs MUST be timezone-aware RFC 3339,
  normalized to UTC, and compared against one operation-scoped evaluation time.
  Naive timestamps, leap-second notation, and intervals with
  `invalid_at <= valid_at` MUST be rejected.
- **R4.4 Boundary semantics.** A node is current when `valid_at` is absent or
  `valid_at <= evaluation_time`, and `invalid_at` is absent or
  `evaluation_time < invalid_at`. Equality at valid-at is included; equality at
  invalid-at is excluded.
- **R4.5 Contradiction resolution boundary.** Kindex MUST evaluate explicit graph
  relations only and MUST NOT infer semantic contradiction or choose a winning
  claim. Invalidating or superseding an endpoint can restore eligibility; absent
  explicit relation is not proof of consistency.

## P5 — Trusted recall is opt-in outside resume

- **R5.1 Compatibility.** Default ordinary search behavior and result shape MUST
  remain unchanged. Legacy unverified nodes remain searchable.
- **R5.2 Trusted view.** CLI and MCP search/context surfaces MUST offer an
  explicit trusted-only option that uses the same node-eligibility predicate as
  resume. Help text MUST state that default search is recall, while trusted-only
  search and resume are admission-controlled projections.
- **R5.3 No hot-path model call.** Resume admission, trusted search filtering,
  verification, invalidation, candidate review, expiry, and migration MUST add
  no LLM call.
- **R5.4 Additive interfaces.** Existing public function signatures and output
  shapes MUST change additively unless this specification explicitly names the
  changed default. Existing callers that do not request trusted-only behavior
  continue to work.

## P6 — Schema evolution is atomic and compatible

- **R6.1 Fresh and upgraded stores.** A fresh store and an existing version-7
  store MUST expose the same version-8 schema and behavior.
- **R6.2 Transactional migration.** Every version-8 DDL/backfill statement and
  the schema-version update MUST occur in one transaction. Injected failure at
  any migration statement MUST roll back to version 7 with no partial version-8
  table, column, index, or version stamp.
- **R6.3 Idempotence.** Reopening an upgraded database MUST perform no migration
  write and preserve data.
- **R6.4 Legacy preservation.** Existing node, edge, suggestion, reminder,
  activity, and pheromone data MUST survive the upgrade. Legacy nodes MUST NOT
  be silently marked verified.

## Cross-cutting invariants

- **I1** Candidate state is not knowledge and cannot become search-visible by
  status or query accident.
- **I2** Automatic extraction has no capability that calls candidate accept.
- **I3** Verification identity is asserted audit text within the local trust
  boundary; no authentication claim is implied.
- **I4** Machine disposition/conflict codes never copy candidate snippets.
- **I5** No HTTP/TCP/protobuf service, external port, regulated tier, mandatory
  soak, release, deployment, or registry work is introduced.
- **I6** Existing test assertions tied to a prior run's version-7 no-schema-change
  requirement may be revised only to express this new, user-authorized schema
  contract; unrelated existing oracles remain immutable.

## Explicit non-goals and residual risks

- Host transcript clearing, arbitrary execution replay/time-travel, worktree or
  runtime isolation, semantic truth adjudication, and model reasoning repair.
- Cryptographic reviewer identity, CLI/MCP access control, encryption at rest,
  legal retention, GDPR/compliance certification, and zero-loss capture.
- Global verified-only search by default. The recall/trusted duality is explicit
  compatibility debt, not a hidden integrity guarantee.
- A correct projection cannot guarantee that a downstream model interprets it
  correctly.
