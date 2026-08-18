# Architecture Specification — state-resilience run

Authority state: local AI-ratified technical guidance derived from the Product
Specification. No human signature was supplied. Binding parts are the schema,
public interfaces, state transitions, trust predicate, and failure semantics;
internal decomposition may improve without weakening those contracts.

Base revision: `666e20864fdcd3a21d5683f2c23085cf32d23257`.

## A1 — Component and state ownership

| Component | Files | Owns |
|---|---|---|
| Schema/migration | `src/kindex/schema.py`, `src/kindex/store.py` | schema v8, one-transaction v7 upgrade, typed persistence |
| Trust policy | new `src/kindex/trust.py` | UTC parsing, current/verified predicate, contradiction reasons |
| Candidate state machine | `src/kindex/store.py` | immutable payload, review token, atomic transitions, minimization |
| Automatic capture adapter | `src/kindex/cli.py` (`cmd_compact_hook`) | transcript extraction to candidates only |
| Resume projection | `src/kindex/sessions.py` | deterministic priority packing and omission disclosure |
| Recall projection | `src/kindex/retrieve.py` | optional trusted-only filtering; default recall unchanged |
| CLI adapter | `src/kindex/cli.py` | candidate/verify/invalidate/trusted flags and safe rendering |
| MCP adapter | `src/kindex/mcp_server.py` | parity tools/flags over the same Store functions |
| Retention cadence | existing cron path in `src/kindex/daemon.py`/CLI | calls deterministic candidate prune; never promotes |
| Configuration | `src/kindex/config.py` | `capture.candidate_ttl_days` (default 7) |

No network service, port, transport protocol, external queue, or separate audit
service is added.

Configuration adds the following typed field without changing existing defaults:

```python
class CaptureConfig(BaseModel):
    candidate_ttl_days: int = 7

class Config(BaseModel):
    # existing fields remain
    capture: CaptureConfig = Field(default_factory=CaptureConfig)
```

`candidate_ttl_days` MUST be positive when configuration is validated.

## A2 — Schema v8

`SCHEMA_VERSION = 8`.

### Nodes

Add nullable text columns:

- `verified_at`: normalized UTC RFC 3339 instant at which the verification
  assertion was recorded;
- `verified_by`: asserted reviewer identifier;
- `prov_method`: asserted verification method;
- `valid_at`: optional normalized UTC start of claim validity;
- `invalid_at`: optional normalized UTC exclusive end of claim validity.

`prov_when` remains capture/provenance time. No existing row is backfilled as
verified.

### Edges and suggestions

- Add `edges.updated_at TEXT NOT NULL DEFAULT ''` and backfill it from
  `created_at`. `Store.add_edge` writes the current UTC time on replace/insert.
  This gives review-state tokens a mutable-edge clock.
- Add `suggestions.kind TEXT NOT NULL DEFAULT 'bridge'` and backfill `bridge`.
  Existing dream/suggestion behavior continues to operate only on bridge rows.
  Capture candidates never use this table.

These two columns preserve the already-recorded v8 direction while remaining
behaviorally neutral in this slice.

### Capture candidates

Create `capture_candidates`:

```sql
CREATE TABLE capture_candidates (
    id TEXT PRIMARY KEY,
    title TEXT,
    content TEXT,
    node_type TEXT,
    domains TEXT,
    connections TEXT,
    source_digest TEXT NOT NULL,
    payload_digest TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_method TEXT,
    disposition_code TEXT,
    conflict_ids TEXT NOT NULL DEFAULT '[]',
    conflict_codes TEXT NOT NULL DEFAULT '[]',
    created_node_id TEXT,
    CHECK (status IN ('pending','conflicted','accepted','rejected','expired'))
);
```

`domains`, `connections`, conflict lists are canonical compact JSON. Create
indexes on `(status, created_at DESC)` and `(status, expires_at)`, plus a partial
unique index on `payload_digest` while status is pending/conflicted. A duplicate
automatic capture returns the existing live candidate ID and does not create a
second review subject.

Terminal minimization sets `title`, `content`, `node_type`, `domains`, and
`connections` to NULL. `source_digest` and `payload_digest` remain as the only
payload-derived receipt fields; the source itself is never stored.

### Migration protocol

The v8 migration is a sequence of individual `execute` statements, not
`executescript` (whose implicit commit semantics are unsuitable here):

1. `BEGIN IMMEDIATE`;
2. apply each `ALTER`, backfill, table, and index statement;
3. verify required columns/table/indexes through SQLite metadata;
4. update `meta.schema_version` to `8`;
5. commit.

Any `BaseException` rolls back and propagates. No blanket exception swallowing
is permitted in the v8 path. A fresh database obtains the same columns/tables
from `CREATE_TABLES`. Reopen at v8 performs no DDL.

For hermetic failure-at-each-statement testing, `Store.__init__` gains an
optional keyword-only `migration_step_hook: Callable[[int, str], None] | None =
None`. The v8 path calls it immediately before every migration statement with a
zero-based index and stable statement label. Production callers omit it. The
hook itself is never persisted or invoked outside a migration.

## A3 — Time and trust interface

`src/kindex/trust.py` exposes:

```python
@dataclass(frozen=True)
class TrustDecision:
    eligible: bool
    reason: str
    conflict_ids: tuple[str, ...] = ()

def parse_rfc3339(value: str, *, field: str) -> datetime
def normalize_rfc3339(value: str | datetime, *, field: str) -> str
def validate_interval(valid_at: str | None, invalid_at: str | None) -> tuple[str | None, str | None]
def node_trust_decision(store: Store, node: dict, *, at: datetime | str | None = None) -> TrustDecision
def filter_trusted_nodes(store: Store, nodes: Iterable[dict], *, at: datetime | str | None = None) -> tuple[list[dict], dict[str, int]]
```

Accepted timestamp syntax is RFC 3339 with `Z` or explicit numeric offset.
Normalize to `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`. Reject no-offset values and second
`60`. Capture one `at` per operation.

Trust reason codes are stable strings:

- `trusted`
- `inactive`
- `unverified`
- `not_yet_valid`
- `invalidated`
- `mutual_contradiction`

Contradiction evaluation considers incoming and outgoing `contradicts` edges.
An endpoint suppresses the subject only when that endpoint independently passes
status, verification, and valid-time checks without considering contradictions.
This two-stage evaluation avoids recursion and prevents an unverified poison
node from suppressing verified knowledge. Both current verified endpoints of a
contradiction receive `mutual_contradiction`.

## A4 — Store verification interface

Add Store methods:

```python
def verify_node(
    self, node_id: str, *, verified_by: str, prov_method: str,
    verified_at: str | None = None, valid_at: str | None = None,
    invalid_at: str | None = None,
) -> dict

def invalidate_node(
    self, node_id: str, *, invalidated_by: str,
    disposition_code: str, invalid_at: str | None = None,
) -> dict
```

Both resolve an existing node, validate bounded non-empty audit strings, update
typed columns in one transaction, update `updated_at`, and write an activity-log
event. Invalidate defaults its timestamp to now and does not change node status
or content. Verification never rewrites original provenance fields.

`disposition_code`, reviewer, and method are stripped, control-character-free,
and capped at 128 characters. The invalidation actor/code are recorded in the
activity event; `invalid_at` is the typed trust field.

## A5 — Candidate Store interface and state machine

Add exceptions derived from `ValueError`: `CandidateNotFoundError`,
`CandidateStateError`, `StaleReviewError`, `TitleCollisionError`, and
`InvalidIntervalError`.

Add Store methods:

```python
def add_capture_candidate(
    self, *, title: str, content: str, node_type: str = 'concept',
    domains: list[str] | None = None,
    connections: list[dict] | None = None,
    source_digest: str, now: str | None = None,
    ttl_days: int | None = None,
) -> str

def list_capture_candidates(self, *, status: str = '', limit: int = 20) -> list[dict]
def get_capture_candidate(self, candidate_id: str) -> dict | None
def candidate_review_token(self, candidate_id: str) -> str

def accept_capture_candidate(
    self, candidate_id: str, *, review_token: str,
    reviewed_by: str, prov_method: str,
    valid_at: str | None = None, invalid_at: str | None = None,
    now: str | None = None,
) -> dict

def reject_capture_candidate(
    self, candidate_id: str, *, reviewed_by: str,
    disposition_code: str, now: str | None = None,
) -> dict

def prune_capture_candidates(self, *, now: str | None = None) -> int
def erase_capture_candidate(self, candidate_id: str) -> bool
```

Candidate payload canonicalization:

- title is stripped, non-empty, at most 500 characters;
- content is non-empty and capped at the existing extraction boundary;
- type must be an allowed node type;
- domains are sorted/deduplicated strings;
- each connection is canonicalized to `{from_title,to_title,type,why}` with an
  allowed edge type and bounded strings;
- NUL and terminal control characters are rejected; newline and tab remain in
  content only;
- payload digest is SHA-256 of canonical UTF-8 JSON; source digest is SHA-256 of
  the extracted transcript text and is never a path.

Review token is SHA-256 of canonical JSON containing:

1. candidate ID, payload digest, status, updated time, and expiry;
2. all current same-title nodes, sorted by ID, with ID/status/updated time and
   verification/validity fields;
3. every durable node explicitly referenced by a proposed connection, with the
   same fields;
4. all `contradicts` edge rows among those nodes, including ID/from/to/type and
   created/updated times.

This token is state freshness only.

### Accept transaction

1. `BEGIN IMMEDIATE` before reading mutable candidate/graph state.
2. Re-read candidate; require pending or conflicted and `now < expires_at`.
3. Recompute review token and compare with constant-time digest comparison.
4. Reject any same-title durable node independently of token comparison.
5. Validate reviewer/method/time interval.
6. Resolve proposed connection endpoints. If an explicit `contradicts`
   proposal targets an independently current verified node, conditionally mark
   the candidate `conflicted` with IDs/codes, commit that candidate-only state,
   and return its typed dictionary. No node or edge has been written at this
   point. A later accept requires a newly computed review token and still cannot
   promote while the conflict remains current.
7. Insert one new node with verification fields; never use `INSERT OR REPLACE`.
8. Insert only proposed edges for which both endpoints resolve to the new node
   or an existing durable node. Edge inserts are in the same transaction.
9. Conditional terminal update:
   `... WHERE id=? AND status IN ('pending','conflicted') AND expires_at>?`;
   require rowcount 1 while clearing raw payload.
10. Write the normal activity events on the same connection and commit. The
    candidate transition, node, edges, and activity receipt are one transaction.

Any exception rolls back node, edges, and candidate transition together.

Reject and prune use the same write lock plus conditional transition and
row-count check. Prune handles only pending/conflicted rows with
`expires_at <= now`. Erase deletes any candidate/receipt by exact ID and logs
only ID/status, not payload.

## A6 — Compact-hook boundary

After extraction, `cmd_compact_hook` computes one source digest and calls
`add_capture_candidate` for each content-bearing concept. Connection proposals
touching that concept are embedded in its candidate payload. It performs no
node lookup as an authorization shortcut and contains no `add_node`/`add_edge`
call for extracted concepts. Duplicate live payloads count as already staged.

Context emitted after compaction is retrieved from the existing durable graph;
it cannot include newly staged candidate content. Per-candidate insertion
failures are caught, recorded through the existing degraded mechanism if
available, and never fail the host event. Success output reports staged count,
not learned/added node count.

## A7 — Resume projection

Binding signature:

```python
def format_resume_context(
    store: Store,
    name: str,
    max_tokens: int = 1500,
    *,
    counter: Callable[[str], int] | None = None,
    evaluation_time: str | datetime | None = None,
    trusted_only: bool = True,
) -> str
```

Default counter is `len(text.encode('utf-8'))` and is documented as Kindex byte
budget units, not universal provider tokens. The function normalizes labels and
values, assembles complete labeled lines/blocks in fixed priority, and checks the
complete output after every addition. If one high-priority value alone is too
large, it may truncate only that value on a Unicode-codepoint boundary while
keeping its complete label and optional ellipsis inside the count. No partial
list item is emitted. Output is checked once more before return.

Related nodes are evaluated with `filter_trusted_nodes`. Omission counts are
rendered before related items so denial information is not crowded out by the
items it describes. `trusted_only=False` exists for direct library compatibility
and diagnostic use; CLI/MCP tag resume use the safe default and do not expose a
silent fallback.

## A8 — Trusted recall

Add keyword-only `trusted_only: bool = False` and `evaluation_time` to
`hybrid_search`. Filtering occurs while consuming ranked candidates, before the
`top_k` break, preserving survivor order and backfilling within the existing
candidate window. Default behavior is byte-compatible. Add the same option to
CLI `search`/`context` and MCP `search`/`context`; result top-level shapes do not
change. Human fence/disclosure text adds trusted omission counts only when the
option is selected.

## A9 — CLI and MCP contracts

CLI commands:

```text
kin candidate list [--status ...] [--limit N] [--json]
kin candidate show ID [--json]
kin candidate accept ID --review-token TOKEN --by WHO --method METHOD [--valid-at TS] [--invalid-at TS] [--json]
kin candidate reject ID --by WHO --code CODE [--json]
kin candidate prune [--now TS] [--json]
kin candidate erase ID [--json]
kin verify NODE --by WHO --method METHOD [--verified-at TS] [--valid-at TS] [--invalid-at TS] [--json]
kin invalidate NODE --by WHO --code CODE [--at TS] [--json]
kin search ... --trusted-only
kin context ... --trusted-only
```

MCP tools use the same names prefixed by their server mapping:
`candidate_list`, `candidate_show`, `candidate_accept`, `candidate_reject`,
`candidate_prune`, `candidate_erase`, `verify`, and `invalidate`; existing
`search` and `context` gain `trusted_only: bool = False`.

Adapters catch the typed Store errors and return stable `Error: <code>: ...`
messages. JSON output is ordinary typed dictionaries/lists; human output uses
delimiters and replaces C0/C1 control characters except newline/tab in content.

## A10 — Security and failure bounds

- SQL uses parameters; dynamic status values are validated against enums.
- Candidate payload and review strings are untrusted data, never evaluated as
  instructions, shell, SQL, HTML, or template source.
- Review tokens use constant-time equality but are not secrets.
- Store operations use the existing SQLite timeout. Lock failure propagates to
  interactive CLI/MCP; compact-hook catches it and fails soft.
- No payload is copied into terminal activity-log details or disposition codes.
- Full CLI/MCP authentication, filesystem encryption, semantic conflict
  inference, and disk-full journal recovery are outside this slice and remain
  explicit residual risks.
