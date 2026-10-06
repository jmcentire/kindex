# Repository-bound MCP

Run a separate Kindex endpoint when an agent should remember only within one
repository or Factory lane:

```bash
kindex-lite --repo /absolute/path/to/repo
```

The command is included in `kindex[mcp]`. It serves MCP over stdio. The repository
must exist; an empty repository can start a fresh graph without a home-level
Kindex configuration. The server uses the repository's canonical
`.kin/local/kindex` store, or the supported populated legacy `.kin/local` store.
It refuses conflicting populated layouts instead of choosing silently.

## Client configuration

Keep the MCP server name `kindex` so existing agent instructions and tool-name
checks continue to recognize it. Configure it in the agent's isolated host
configuration, replacing the unrestricted Kindex entry for that agent.

```json
{
  "mcpServers": {
    "kindex": {
      "command": "kindex-lite",
      "args": ["--repo", "/absolute/path/to/repo"]
    }
  }
}
```

For clients that use TOML:

```toml
[mcp_servers.kindex]
command = "kindex-lite"
args = ["--repo", "/absolute/path/to/repo"]
```

Use an absolute executable path when the host does not inherit your shell's PATH.
Each lane gets its own repository path and local store. Do not point Coder and
Tester at the same repository or copy one lane's runtime graph into the other.

## What is restricted

The repository is chosen by the launcher, not by tool arguments. Home-level
graphs, profile selection, parent configuration, and environment graph selectors
cannot redirect this endpoint. The endpoint uses fixed defaults rather than
loading routing or provider settings from YAML.

Every exposed handler gets its graph, configuration, and repository binding from
the single startup repository. A scope violation returns an explicit error; it
does not become an empty search result or silently redirect to the local graph.

An explicit tool allowlist retains local memory, session tags, tasks, coordination,
and Kinbase sync/status/explain/submission operations. Global graph requests
and paths outside the bound repository are refused. Host-session ingestion,
executable reminders, and general maintenance tools are not exposed. New tools
added to the full server are not automatically admitted to this endpoint.

Graph path validation rejects linked storage and checks the bound location again
for each call. Missing or invalid storage never falls back to the user's graph.
Existing databases that require migration must be upgraded explicitly before
use; this endpoint does not run migration backups outside the repository.

The local runtime graph persists across server restarts. Starting the server
does not automatically import the tracked `.kin/index.json` or
`.kin/knowledge.json` snapshots. Prepare approved shared context in each local
graph before dispatching agents when the task requires it.

## Kinbase I/O

`kinbase_sync`, `kinbase_status`, `kinbase_explain`, and `kinbase_submit` target only the bound
repository. They retain their existing semantics: sync verifies and imports
signed evidence into the local Kindex graph; explain reads an exact key; status
can close overdue apologies through Kinbase.

`kinbase_submit(repo, text, node_type="concept")` submits an explicit observation
through `kinbase ingest kindex`. Supported types are `concept`, `decision`,
`constraint`, and `question`; the payload is always agent-authored evidence for
the repository's team. The tool rejects empty, oversized, and recognized
credential-bearing text. A content-addressed SQLite artifact under the local
graph's `kinbase-submissions/` directory makes unchanged retries use the same
source identity. Existing artifacts are checked before reuse.

Native intake can derive and admit local facts; its receipt reports those effects.
The tool does not run a separate bulk admission command or request authority
ratification. A timeout leaves the outcome unknown:
Kinbase may already have recorded the observation. Retry the same text and type
to reuse its identity. Kindex writes no signed source events itself.
See [Kinbase integration](kinbase.md).

Kinbase may contact its configured authority service and refresh its own caches.
Repository binding does not narrow Kinbase's authority projection beyond the
permissions Kinbase already enforces for that repository.

## Boundary

This restricts what this MCP endpoint can do. It is not an operating-system
sandbox for the agent, its shell, hooks, other MCP servers, or the Kinbase binary.
Use host isolation when those surfaces also need restrictions. In particular,
an unrestricted Kindex primer hook or a second full Kindex server can still supply
outside context even while this endpoint is correctly scoped.

The normal `kin` CLI and `kin-mcp` server retain their existing behavior.
