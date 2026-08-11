# Cross-family review dispositions — run batch0

Three reviewers of different model lineage than the implementer, each finding
verified or refuted by the Validator against the code before it could act.
Detection is cheap; escalation is earned.

## Round 1 — stable surfaces (S1/S3/S4), pre-integration

**Codex** — 10 findings: 5 CONFIRMED and returned to the lane, 4 REFUTED, 1
already-ruled.

Confirmed (all fixed in `ca75f3b`):
- R1.1 envelope detection over-matched: `session_id`-only JSON suppressed
  `--text` and routed raw hook JSON into extraction (the issue-#14 junk class on
  a partial-JSON edge). Now three-way: envelope requires both `hook_event_name`
  and `transcript_path`.
- R1.2 Stop-entry migration replaced only the first matching entry; duplicate
  legacy entries survived.
- R3.2 `store.recent_nodes` had no status predicate, so topicless MCP
  context/prime/orient still surfaced archived and superseded nodes. **This was
  the finding the entire same-family chain missed** — coder, tester, and
  Validator all read "operational pulls" as the two named formatters.
- R3.5 fence-note counted before tag/`--mine` filtering and without the expiry
  filter the real results use.
- Decay read/update/stamp was not serialized.

Refuted (recorded so the lane did not chase them):
- "Sub-day gate discards the interval" — the gate returns *without* advancing the
  stamp, so the interval is preserved for the next run.
- "Large half-life starves decay permanently" — arithmetic requires a weight
  below the 0.01 floor at the shipped 90/30 constants; latent only if the
  half-life ever becomes configurable.
- "Fencing before normalization re-scores survivors" — set-dependence is inherent
  to fencing at source; ghost-scoring by retired junk would be worse (R3.4 note).
- 4-dp skip semantics — pre-existing storage quantization, already ruled.

**Ollama glm-5.2** — 0 net-new findings. Independently re-derived the S4 skip
threshold deviation *blind* (matching what the coder disclosed and the Validator
ruled), and independently worked through and cleared the R4.1 telescoping algebra
and the R3.5 note logic. Corroboration, and evidence the finder is not
rubber-stamping.

## Round 2 — whole-tree conformance on the integrated branch

**Gemini (antigravity)** — 1 finding: MCP prompts and resources call
`_get_store()` without the `@_tool()` guard, so a store failure there is not
converted to the degraded result string.

Disposition: **finding CONFIRMED, severity REFUTED, recorded as a declared gap.**
- Verified by direct probe: the prompt raises a *typed* `MemoryUnavailableError`;
  FastMCP renders that as a protocol error for that one request, and the server
  survives — tools continue answering afterward. The claim "crashes the server"
  is not what the code does.
- R2.5's spec text scopes the requirement to *tool results*; prompts and
  resources are out of that scope by construction.
- The coder disclosed exactly this as residue item 11 before any reviewer saw it.
- Round cap was already declared. Reopening a lane for an out-of-spec
  consistency nicety after declaring a cap is scope creep, so: shipped as a
  declared gap, remediation queued for the next wave (wrap prompt/resource
  entry points with the same guard).

## Method note

Same-family lanes plus a same-family Validator converged on a reading of "R3.2
operational pulls" that a different model family punctured on first contact. The
doctrine's "three readings of one specification are one reading" is not
theoretical, and the cost of the cross-family pass was one background invocation.
