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

## v8 amendments (pre-dispatch, not mid-run)
- Cross-family lanes: coder=ollama(glm-5.2/opencode), tester=codex, orchestrator=
  gemini. Derived independence tier is read from dispatches.jsonl, never asserted.
- Coder MAY capture to kindex under a `v8-coder` tag (search first, link). Tester
  may not touch kindex at all this run — the graph holds this run's reproduction
  values and defect detail.
- Both lanes: no test edits except the Tester's single permitted un-skip (T0.7).
- Release mechanics remain Validator-owned.
