# Kindex state-resilience build — constraint input

## User authority

Improve Kindex as part of a production-software build using the Factory and
`~/Code/tools` review model, the `/engineer`, `/test`, `/validate`, and
`/orchestrate` contracts, Constrain, Simulacrum, Advocate, and a deliberate
planning pass. Prefer Codex and Ollama for execution; Antigravity may provide a
bounded independent receipt; Claude must not be the build or orchestration host.

## Problem

Kindex is valuable as an external durable-memory and coordination layer for
long-horizon coding agents, but two current paths can amplify state corruption:

1. `format_resume_context(..., max_tokens=...)` accepts a budget and ignores it.
2. `kin compact-hook` treats model-extracted transcript claims as durable truth,
   immediately creating graph nodes and edges without a review boundary.

The existing graph also lacks a uniform, machine-readable way to distinguish a
verified, currently valid fact from an unverified, stale, invalidated, or
contradicted claim.

## Desired observable outcomes

- Resume output is deterministic and never exceeds its declared budget.
- Required operational session state survives within that bound; lower-priority
  history is shed first and truncation is explicitly disclosed.
- Automatic PreCompact extraction creates reviewable candidates only. It never
  creates or links durable graph nodes before an explicit review action.
- Durable nodes can carry verification method, verification time, and valid-time
  metadata without changing the meaning of existing provenance fields.
- Resume selection refuses stale, not-yet-valid, explicitly invalidated, and
  unresolved-contradictory related knowledge by default, and reports omissions.
- Existing nodes remain readable and editable. Unverified legacy nodes are not
  silently relabeled as verified.
- Hooks remain fail-soft: capture or verification failures never break the host
  compaction event.
- No new LLM call is added to resume, verification, or selection hot paths.

## Existing mechanisms and constraints

- SQLite schema version is currently 7. Historical run-specific tests assert
  that earlier decay work did not change that schema; they are evidence for that
  earlier run, not automatically authority for this one.
- Nodes already have an `extra` JSON object and `prov_when`; the latter currently
  means when provenance was recorded, not the claim's valid-time interval.
- There is an atomic file-backed inbox (`write_inbox_item`) and an explicit
  `kin learn --from-inbox` action, but that action currently re-extracts and
  commits all unprocessed inbox items rather than exposing typed candidate
  acceptance.
- Suggestions are bridge-specific and may be auto-applied by dream. Reusing that
  table for untrusted knowledge candidates risks accidental promotion.
- Search already fences archived and superseded nodes. Verification gating must
  not silently rewrite the global search contract unless the specification says
  so.
- Package behavior and public signatures must remain additive where possible.
- The implementation and the acceptance tests must be independently authored
  from the same frozen contract; the Validator alone runs the judging suite.

## Decisions this constraint pass must force

1. Is explicit schema v8 necessary now, or can typed metadata live safely in
   `extra` until query/index pressure justifies a migration?
2. Is the existing inbox an adequate candidate boundary, or is a typed candidate
   store and explicit accept/reject interface required for the claim
   "reviewable" to be honest?
3. What exact trust predicate controls resume inclusion, including legacy nodes,
   validity intervals, verification freshness, and contradiction edges?
4. What deterministic definition backs `max_tokens` without binding Kindex to a
   provider tokenizer?
5. Which CLI and MCP surfaces are required for agents to verify, invalidate, and
   review candidates without direct database access?

## Explicit non-goals

- Host transcript clearing, context-window compaction, or model reasoning repair.
- Replay/time-travel of arbitrary tool execution.
- Git worktree, process, database, port, or container isolation.
- Autonomous truth adjudication by an LLM.
- A release, deployment, or registry publish in this build unless separately
  authorized.

## Evidence bar

The run must produce content-addressed Product, Architecture, and Testing
artifacts; isolated Coder and Tester lanes; base-red and head-green receipts;
negative and boundary tests for poisoned, conflicting, stale, future-valid, and
oversized state; at least one recorded mutation proving the oracle turns red;
the full existing suite; bounded adversarial review of the exact patch; and an
honest verdict that identifies the absence of a human artifact signature.
