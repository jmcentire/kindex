# Canonical JSONL knowledge and migration

JSONLs are the canonical bearer of knowledge. SQLite databases, including
`kindex.db`, are disposable caches. The agent is explicitly responsible for
ensuring that all durable knowledge it reads or writes in the database is
maintained losslessly in canonical JSONLs. For Git-backed project graphs, commit
those sources with the related code so collaborators receive the same knowledge.

Worktrees are ephemeral and deletion is outside our control. Knowledge guarantees
must hold without preserving, archiving, merging, or rescuing a SQLite database,
and without any teardown hook or pre-deletion action. Maintain the canonical
sources continuously. Optional database merging may improve efficiency; it is
never a correctness prerequisite.

## Prompt your agent on an existing installation

Re-run the instruction-file setup for your client to install the updated rules.
Then give your agent this migration request:

> Ensure all existing knowledge in my Kindex `kindex.db` is represented losslessly
> in the canonical JSONLs. Inventory every durable knowledge record and
> relationship, including complete content, metadata, provenance, lifecycle
> state, and source bindings. Compare that inventory with the canonical sources,
> migrate database-only knowledge, and reconcile legacy JSON with JSONL without
> truncation, dropped fields, or silent format selection. Respect audience and
> secret boundaries. Verify complete coverage and source/reference consistency,
> commit the project sources with the code, and keep them current after every
> capture, edit, and link. Report exact unsupported fields, record types,
> conflicts, or reader limitations; do not claim a complete migration while
> omissions remain. Treat SQLite as disposable. Do not depend on preserving or
> rescuing databases when worktrees disappear.

A version upgrade alone does not perform this migration. This change provides
agent instructions and release-note guidance, not a new canonical serializer,
automatic migration, or cache reconstruction implementation.

## Agent coverage checklist

1. Establish scope and audience. Inventory the selected project graph and its
   existing canonical files. Do not mix a personal/global graph into a project's
   tracked sources. Private knowledge needs appropriately protected canonical
   JSONLs; never publish secrets or private material into a public repository.
2. Inventory all durable knowledge by identity and revision, not just titles or
   counts. Include every knowledge type, full content, tags/domains, meaningful
   metadata and lifecycle state, relationships and their direction/provenance,
   and structured source references. Rebuildable search indexes and caches are
   not additional durable knowledge.
3. Compare database knowledge with source records field by field. Preserve
   database-only knowledge in canonical JSONLs. Preserve independent source
   knowledge too: an incomplete local cache must not overwrite it. Do not resolve
   conflicting identities or edits solely by newest timestamp.
4. Reconcile legacy JSON explicitly. Existing `knowledge.json` remains the
   selected runtime artifact where present; new `repo-memory` publications in
   #69 use `knowledge.jsonl`. If both contain records, the current importer can
   silently select only JSON. Compare and reconcile both losslessly rather than
   treating either file as empty or dropping it. Verify complete coverage before
   retiring a superseded source artifact.
5. Verify relationships and evidence references against canonical sources.
   Preserve source identity and the relevant evidence revision/digest; a cache
   path, cache UUID, or same-ID node in another worktree cannot establish the
   historical evidence. Report unresolved bindings instead of substituting an
   unrelated node or relabeling provenance to match a rebuilt cache.
6. Validate JSONL parsing, record coverage, metadata equality, and relationship
   endpoints. Check source-to-source consistency after clone, checkout, and
   merge. If a supported source reader/rebuilder is available, verify equivalent
   knowledge reconstruction; if it cannot represent a type or field, report the
   precise limit separately and do not claim end-to-end reconstruction passed.
7. Commit the canonical project files with related code. Repeat the coverage and
   reference checks as captures, edits, and links occur, including relationship
   changes that create no new nodes. Completion requires a lossless coverage
   report and an explicit account of any remaining reader limitations.

## Current tooling limits and real defects

- `.kin/index.json` contains selected summaries, not complete knowledge.
- `kin repo-memory publish` transports explicitly selected active shareable
  concepts, decisions, and questions and selected-peer relationships. It omits
  other types and metadata, including `extra.source_refs`; its quarantined import
  is not a complete database rebuild. Do not use it alone as proof of migration.
- The mixed-format import issue in #69 can hide JSONL evidence when JSON also
  exists. The relationship-only `learn` path in #71 can omit supplied durable
  source references. Both are actual serialization/reference-coverage issues,
  independent of whether a worktree or database survives. They remain open.
- #71's resolver inspects saved SQLite locators read-only; it does not resolve
  canonical JSONL source bindings. `database_missing` after deletion is expected
  cache unavailability, not a requirement to rescue that cache or proof that the
  derived claim is false.

The agent must expose these limits and maintain canonical knowledge without
claiming guarantees the current serializer, importer, or resolver does not
provide. Runtime changes to close those gaps belong in separately scoped work.
