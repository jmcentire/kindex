# Knowledge preservation and planned canonical JSONL migration

Kindex currently stores durable knowledge in SQLite. A database may be the only
complete copy of its records, operational state, and source bindings. **Do not
delete Kindex databases or their worktrees as disposable caches.** Existing
JSON/JSONL exports and tracked project snapshots do not provide a complete rebuild.
An upgrade or an instruction-file update does not migrate existing knowledge.

## Supported workflow today

Use Kindex's capture, edit, link, task, and session tools against the selected
graph. Commit matching `.kin/config` and `.kin/index.json` changes alongside code.
Keep databases and private/local state out of Git, while preserving them during
planned worktree cleanup or migration.

`kin repo-memory publish` exports explicitly selected active public/team concepts,
decisions, and questions, including supported relationships between selected
peers. Imported repository evidence is quarantined for review. This transport is
not a full backup, and publishing private knowledge is refused. Do not change a
node's audience merely to make a migration export succeed.

New repository-evidence artifacts use `.kin/knowledge.jsonl`; an existing
`.kin/knowledge.json` keeps its format. If both exist, publication and import
refuse until their records are explicitly reconciled into one artifact. Neither
format takes precedence. Preserve both inputs while comparing records and
resolving conflicts. There is no automatic union, dual-write, or old-client JSONL
support. Other `.kin/*.jsonl` notes are documentation records; `repo-memory` does
not automatically discover or import them.

Generated `.kin/index.json` and `.kin/code-map.json` are projections. The structured
merge driver preserves distinct identities, but same-ID index conflicts select
by `updated_at` (ties keep ours), and code-map conflicts can keep ours. Supported
deletions are honored. Preserve both input snapshots and relevant knowledge;
merge success does not prove complete source coverage. Do not hand-edit generated
snapshots or overwrite another branch's knowledge by regenerating from an
incomplete local graph. Use `kin index` / `kin export code-map` after establishing
that the selected graph and merged code represent the intended snapshot.

## Prompt for an existing installation

> Inventory the selected Kindex graph and its project evidence files. Preserve
> the database and private/local state; do not delete a database or worktree as
> a cache. Commit matching project configuration and indexes with the code.
> Publish only explicitly selected shareable evidence through `kin repo-memory
> publish`, preserving audience and credential boundaries. If JSON and JSONL
> evidence files coexist, retain both inputs while reconciling their records into
> one artifact. Report exactly what was preserved or published, unsupported fields
> and record types, unresolved source bindings, and any missing knowledge. Do not
> claim that selected exports or summary indexes provide a lossless migration or
> a complete database rebuild.

Re-run instruction-file setup for your client to receive this guidance. There is
no instruction to perform an automatic full migration after every capture.

## Current recovery limits

- `.kin/index.json` contains selected summaries, not complete node content or
  provenance. It cannot reconstruct the complete database.
- `repo-memory` transports selected shareable types and fields into quarantine.
  It omits other node types and metadata, including `extra.source_refs`.
- Graph-transfer JSON/JSONL exports preserve represented graph fields, but filter
  `extra` to supported lifecycle keys on export and import. They omit structured
  `source_refs` and operational metadata, so they are not lossless backups.
- Durable source references identify saved SQLite locators and graph UUIDs.
  They do not discover canonical JSONL evidence or rebind to a recreated database.
  A missing database can yield `database_missing`; replacing it at the same path
  can yield `graph_identity_mismatch`. Preserve the source database rather than
  assuming reimport repairs historical bindings.
- Historical captures whose only evidence was an expired session handle are not
  automatically backfilled. Reconstruct a binding only from verifiable evidence;
  otherwise report that the binding is unresolved.
- Relationship-only `learn` captures retain supplied source records on per-call
  learned-text documents linked to existing concepts and relationship endpoints.
  This shipped improvement does not supply canonical serialization or recovery.

If a database or worktree disappears unexpectedly, report the exact available
and missing evidence. Do not fabricate recovered knowledge or treat missing
source storage alone as proof that a derived claim is false.

## Planned canonical JSONL architecture

The historical design goal is to make complete canonical knowledge sources
sufficient to reconstruct SQLite and resolve historical evidence references,
including after uncoordinated worktree deletion. In that future architecture,
databases could become disposable projections. This is a **planned architecture,
not a shipped guarantee or a current agent cleanup instruction**.

Implementing it requires a serializer covering every durable record type, full
content, lifecycle, relationships, provenance, and source binding; a reader and
rebuilder; and canonical source discovery and reference rebinding independent of
database paths and UUIDs. Private knowledge needs an explicitly protected source
location, never automatic publication to project files.

A future migration must inventory source and destination records by identity and
revision, compare supported fields and relationships, preserve independent
source edits, and verify reconstruction and reference consistency. Any intentional
exclusions require explicit scope, reasons, consequences, and release notes;
report unexplained omissions separately. A coverage report must distinguish
retained knowledge from exclusions and unsupported features, and cannot describe
an incomplete migration as lossless. These requirements preserve the original
recovery intent without transferring an unimplemented guarantee to today's agents.
