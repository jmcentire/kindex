# Kindex release and MCP registry notes

Date: 2026-05-15

These notes are tracked in git with the project so future release agents do not
have to reconstruct the release, registry, and install-path state from chat
history or private graph nodes.

## Project policy decision

Kindex `.kin` project data/config is a git-shipped project artifact, not local
working cache. Safe shared project state belongs in tracked `.kin` files.
Private/runtime state belongs under ignored paths such as `.kin/local/`,
`.kin/cache/`, `.kin/tmp/`, `.kin/private/`, or local user data dirs.

The enforcement direction is actor-agnostic:

- `.kin/config` is the source of truth for work policy.
- Agent instructions, git hooks, CI/PR checks, and Linear writeback should all
  read the same policy.
- Linear integration is opt-in per project. Personal projects without Linear
  should not require Linear IDs.
- Linear should be a work-state record and summary write-target, not the
  primary medium for engineering reasoning.

## v0.19.0 release state

Released `kindex` v0.19.0 with tracked project policy support.

- Kindex commit: `69a5b65 Add tracked project policy support`
- Kindex-Tools commit: `6f0569d Document tracked kindex project policy`
- GitHub release: `https://github.com/jmcentire/kindex/releases/tag/v0.19.0`
- PyPI latest verified: `kindex==0.19.0`
- GitHub Pages docs verified: `https://jmcentire.github.io/kindex/`
- Kindex Tools site verified: `https://kindex.tools/`

Verification completed during release:

- Full test suite: `1022 passed`
- Focused project-policy/hook tests: `14 passed`
- `npm run build` passed in `../kindex-tools`
- GitHub Actions publish succeeded
- GitHub Actions Fly deploy succeeded
- Fresh PyPI install of `kindex[mcp]==0.19.0` succeeded
- `python3 -m kindex.cli --version` returned `kin 0.19.0 (Kindex)`
- MCP import check passed
- Both `kindex` and `kindex-tools` worktrees were clean after release

Local `fly deploy --remote-only` could not run because no local Fly token was
available. The GitHub Actions Fly deployment succeeded using repository
secrets.

## Install pathways

Current intended install commands in README/docs/site:

```bash
pip install 'kindex[mcp]'
uv tool install 'kindex[mcp]'
uvx --from 'kindex[mcp]' kin-mcp --help
git clone https://github.com/jmcentire/kindex && cd kindex && make install
```

The recently enabled fast-path is `uv` / `uvx`, not `uvm`. `uvx` is the
ephemeral runner form; `uv tool install` is the persistent tool install form.
No `uvm` command or documentation reference exists in the repo as of this note.

Agent setup snippets use:

```bash
claude mcp add --scope user --transport stdio kindex -- kin-mcp
kin setup-codex-mcp
kin setup-gemini-mcp
kin setup-opencode-mcp
kin setup-cursor-mcp
```

There is no public npm package named `kindex` as of 2026-05-15. The npm usage
in `../kindex-tools` is only for building/deploying the Astro documentation
site.

Important registry-install caveat: isolated `pip install kindex==0.19.0`
installs the CLI, but `kin-mcp` requires the optional `mcp` extra. In an
isolated Python check, importing/running `kindex.mcp_server` from the base
package exits with:

```text
Error: the 'mcp' package is not installed.
Install with: pip install kindex[mcp]  (or: uv tool install kindex[mcp])
```

Because the official MCP Registry package record uses a plain PyPI identifier
(`"identifier": "kindex"`), marketplace/registry-driven installs may not
install the `mcp` extra. Fix before relying on registry install buttons:

- make `mcp[cli]` a base dependency, or
- publish a dedicated PyPI package for the MCP server, or
- confirm the registry/client supports PyPI extras in `identifier` and that it
  validates/publishes successfully.

## Official MCP Registry state

The official registry is `https://github.com/modelcontextprotocol/registry`.
It provides the `mcp-publisher` binary used to publish `server.json`.

Registry API check on 2026-05-15:

```bash
curl 'https://registry.modelcontextprotocol.io/v0.1/servers?search=io.github.jmcentire/kindex'
```

Result: the official registry only had Kindex versions `0.4.0` and `0.4.1`.
The `0.4.1` entry was marked latest. This explains why MCPMarket still showed
old Kindex metadata even after the repo `server.json`, PyPI, docs, and live
server card were updated.

Current local `server.json` was updated to `0.19.0`. Initial validation failed
against the official registry because `description` exceeded the current
100-character limit, so the description must stay short:

```text
validation failed: expected length <= 100 at body.description
```

Release preflight automation:

```bash
make distribute
```

This target runs tests, builds the distribution, verifies the built wheel can
install with the MCP extra, and validates `server.json` when `mcp-publisher` is
installed. It requires the Python `build` module; install it directly or run
`make dev`. It is intentionally a preflight, not an implicit publisher.

Fix before publishing to the official registry:

1. Run `make distribute`.
2. Authenticate with `mcp-publisher login github`.
3. Publish with `mcp-publisher publish server.json`.
4. Re-check the official registry API and then `https://mcpmarket.com/server/kindex`.

## MCPMarket state

MCPMarket listing checked after v0.19.0 release:

- URL: `https://mcpmarket.com/server/kindex`
- Still appeared stale/cached.
- Showed old registry-era metadata around `0.4.1`, old install command, and old
  tool count.
- No working unauthenticated update endpoint was found from the shell.

Kindex graph follow-up created:

- Task: refresh MCPMarket Kindex listing to current v0.19.0 metadata or
  document the marketplace owner/manual refresh path.
- Watch: verify MCPMarket after releases and escalate/manual-refresh if it does
  not show current `server.json` / server-card data.

Likely path: publish the new `server.json` to the official MCP Registry first,
then wait for MCPMarket to ingest it or request a manual refresh from MCPMarket
if it remains stale.
