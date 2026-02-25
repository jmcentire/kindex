#!/usr/bin/env bash
# Pre-commit hook: sync version, tool/command/test counts across all files.
# Install: ln -sf ../../scripts/sync-version.sh .git/hooks/pre-commit

set -euo pipefail

ROOT="$(git rev-parse --show-toplevel)"

# ── Gather facts ──────────────────────────────────────────────────────
VERSION=$(grep '^version' "$ROOT/pyproject.toml" | head -1 | sed 's/.*"\(.*\)"/\1/')
MCP_TOOLS=$(grep -c '@mcp\.tool' "$ROOT/src/kindex/mcp_server.py" || echo 0)
CLI_CMDS=$(grep -c 's\.set_defaults(func=' "$ROOT/src/kindex/cli.py" || echo 0)
TESTS=$(cd "$ROOT" && python3 -m pytest tests/ --collect-only -q 2>/dev/null | tail -1 | grep -oE '^[0-9]+' || echo 0)

if [ -z "$VERSION" ]; then
    echo "ERROR: Could not read version from pyproject.toml"
    exit 1
fi

echo "sync-version: v${VERSION} | ${MCP_TOOLS} MCP tools | ${CLI_CMDS} CLI commands | ${TESTS} tests"

CHANGED=0

# ── README.md ─────────────────────────────────────────────────────────
if [ -f "$ROOT/README.md" ]; then
    sed -i '' -E "s/version-[0-9]+\.[0-9]+\.[0-9]+/version-${VERSION}/g" "$ROOT/README.md"
    sed -i '' -E "s/\[v[0-9]+\.[0-9]+\.[0-9]+\]/[v${VERSION}]/g" "$ROOT/README.md"
    # Test count badge: tests-NNN%20passing
    sed -i '' -E "s/tests-[0-9]+%20passing/tests-${TESTS}%20passing/g" "$ROOT/README.md"
    if ! git diff --quiet "$ROOT/README.md"; then
        git add "$ROOT/README.md"
        CHANGED=1
    fi
fi

# ── docs/index.html ──────────────────────────────────────────────────
if [ -f "$ROOT/docs/index.html" ]; then
    # Version badges
    sed -i '' -E "s/v[0-9]+\.[0-9]+\.[0-9]+/v${VERSION}/g" "$ROOT/docs/index.html"
    # MCP tools count
    sed -i '' -E "s/[0-9]+ MCP Tools/${MCP_TOOLS} MCP Tools/g" "$ROOT/docs/index.html"
    # CLI commands count
    sed -i '' -E "s/[0-9]+ CLI Commands/${CLI_CMDS} CLI Commands/g" "$ROOT/docs/index.html"
    # Test count
    sed -i '' -E "s/[0-9]+ Tests/${TESTS} Tests/g" "$ROOT/docs/index.html"
    if ! git diff --quiet "$ROOT/docs/index.html"; then
        git add "$ROOT/docs/index.html"
        CHANGED=1
    fi
fi

if [ "$CHANGED" -eq 1 ]; then
    echo "sync-version: staged updated files"
fi
