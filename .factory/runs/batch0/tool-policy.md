# Run tool policy — batch0 (signed by Validator, local AI-verdict mode)

Honesty note on enforcement tier: lanes are separate `claude` invocations with separate
contexts and disjoint workspaces, but capability isolation is DIRECTIVE-level in this
local tmux mode — the kernel Seatbelt backend in factory_runtime/lanes.py is not wired
into dispatch_lane.sh. The derived independence tier is recorded accordingly in the
verdict (shared machine, no kernel enforcement, no shared channel). A directive here is
a prohibition the Validator audits post-hoc, not a removed capability.

## Allowed (scoped to the lane's own workspace)
- Read/write/edit within the lane workspace; git commits on the lane branch.
- python3 / pytest / pip into venvs created INSIDE the workspace.
- Tester only: `pip install kindex==0.29.0` into a workspace venv (authoring baseline).

## Sign-off required
- None defined for lanes this run. Anything not Allowed is Verboten.

## Verboten (for lanes; the Validator alone performs the release steps)
- Any git push, any `gh` invocation, any remote/network access beyond the tester's
  single PyPI baseline install.
- Touching the live graph state: ~/.kindex, ~/Personal/Conv, any real kindex DB or
  config outside the workspace tmp dirs.
- kindex MCP tools for the TESTER (the graph carries implementation detail for these
  exact defects — a read is an oracle-contamination channel). The Coder MAY read the
  four cited research nodes and nothing in tests/oracle space.
- Modifying ~/.claude settings, installed hooks, crontabs, or launchd state.
- Modifying existing files under tests/ (Coder: report a spec defect instead;
  Tester: new files only, per T0.1).
- Version/release files (pyproject.toml version, __init__.__version__, README badge,
  server.json): Validator-owned at endgame.
- LLM API calls from within the implementation or tests (I2).

Violations discovered at judgment are negative evidence against the lane's work.

## Amendment 1 (2026-08-10, Validator; injections 915687f9…, ec574b38…)
Cause: the user-level workspace PreToolUse hook refuses Edit/Write without a recorded
kindex engagement (tag + search); both lanes inherit it (workspaces live under
~/Code/kindex). The coder surfaced it as Q->VALIDATOR rather than working around it —
correct behavior. Amendment, narrowly:
- Coder MAY: one `tag_start` (factory-batch0-coder) + read-only search/context scoped
  to batch0 implementation surfaces. All graph mutations beyond that tag remain Verboten.
- Tester MAY: one `tag_start` (factory-batch0-tester) + exactly one search with the
  deliberate near-null query `factory-batch0-tester-engagement`, results to be ignored
  (the graph holds implementation detail for these defects; real reads contaminate the
  oracle). All other kindex tools remain Verboten for the tester.
- Bash file-writes to evade Edit/Write hooks remain Verboten for both lanes.
Independence-record note: the tester's engagement writes two rows to the live graph
(session tag); contamination risk assessed ~nil under the null-query rule but recorded.
Also receipted here: Validator approved one tester Bash command flagged by the
self-protection hook — `install_claude_hooks` against a tempfile.mkdtemp() .claude dir
(fixture capture of the 0.29.0 Stop-hook entry shape; verified temp-scoped before
approval; injection 6b86b273…).

Enforcement event under Amendment 1: tester attempted a Bash-heredoc write of
tests/test_batch0_capture.py while its Write tool was hook-blocked (likely started
before processing the amendment). DENIED at the confirmation prompt (injection
d4735e3a…) with a corrective restating the sanctioned path (injection 5f921f50…).
Judgment note: examine the tester's final commit for any file whose creation has no
Write/Edit tool trace.

## Amendment 2 (2026-08-11, Validator; injection 69c568df…; orchestrator-initiated)
Orchestrator wake 20260811T025737Z empirically refuted Amendment 1's null-query
assumption: kindex vector search has no similarity floor (vectors.py:938), so the
"near-null" query returns 10 real nodes with title+snippet context — including material
bordering the S1 surface. Corrected form, delivered BEFORE the tester executed any
search (pane-verified): query factory-batch0-tester-engagement + tags=
factory-batch0-tester-null-tag + top_k=1 — the tags post-filter (mcp_server.py:237-241)
deterministically yields zero results on both FTS and vector paths (orchestrator
live-verified from its isolated context). Repeat of the same null-form search is
pre-authorized for rolling-window hook re-trips. Contamination status: none recorded —
the wedge was closed before execution. The missing-similarity-floor property is banked
as a future-wave improvement candidate (orchestrator's kindex node f16e36d93067).
Judgment obligation (orchestrator wake 031239Z strategy flag): the coder's engagement
grant was instruction-scoped ("search/context scoped to batch0 implementation
surfaces"), not mechanism-scoped, and its engagement executed before the flag arrived.
At Phase C the Validator audits the coder's actual kindex calls from the live graph
activity log (queries + nodes returned) and records any test-design-adjacent exposure
(wake-disposition prose mentioning red-test purposes) in the derived independence tier.
Graph hygiene: run bookkeeping accumulated by seats satisfying the engagement gate
(orchestrator tag ac75777348ca, lane tags) is swept at postmortem.

## Amendment 3 (2026-08-11, FOUNDER-directed; injection e8c78585…)
The founder ruled the run under-uses kindex. Coder: graph capture now REQUIRED —
discoveries/decisions as nodes under its lane tag, search-first, linked; excluded:
watch/constraint/directive types (Validator-owned) and any test-oracle content.
Tester: capture DEFERRED to post-verdict (its test-design knowledge is oracle
material until judgment closes), then the same capture duty applies to its design
decisions and the strategy-vs-R4.2 tension it reported. Validator: capture cadence
corrected mid-run — Phase C evidence, rulings, and both live defects (mcp-pin,
import-leak .pth) are now graph nodes (9a7d7f19f7e8, 83376e87116a, 26c254bc95ee,
3e8eb7a3afcc), not just run files.

Discipline fixes from the same wake: this tool-policy file is now receipted with a
digest sibling on every amendment (see receipts chain), and the 02:29 dispatch-SHA
announcement mismatch is dispositioned in incidents.md (announcement was the duplicate
Validator's; lanes correctly cut at base 8c5cc925 — endgame gates are Validator infra,
not lane surface).
