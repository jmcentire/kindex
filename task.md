# Kindex Dream — Post-Session Knowledge Consolidation

Add a `kin dream` subsystem to Kindex that performs memory consolidation:
fuzzy deduplication, suggestion auto-application, cluster detection, and
optional LLM-powered semantic analysis. Triggered manually, by cron, or
as a detached subprocess on Claude Code session exit.

## Context

Kindex already runs a cron maintenance cycle (`daemon.py:cron_run`) that handles
weight decay, orphan archival, auto-linking, and suggestion generation. These are
structural maintenance tasks — they keep the graph tidy but don't improve it.

What's missing is consolidation: discovering that two nodes mean the same thing,
that a cluster of fine-grained session artifacts should be promoted to a single
authoritative concept, or that a pending suggestion is obviously correct and
should be applied automatically. This is the "dreaming" step — replay, strengthen
important paths, prune noise, promote from episodic to semantic memory.

Claude Code reportedly has an "auto-dream" feature that consolidates flat memory
files via a background subagent. Kindex can do this better because the graph
structure preserves relationships during consolidation. Merging two nodes
transfers all edges rather than losing context.

## Constraints

- Never delete nodes during dream — only merge (which archives source) or archive.
- Fuzzy matching threshold must be configurable with a conservative default (>= 0.85).
- LLM calls (`claude -p`) only in `--deep` mode, never in lightweight or detach.
- Dream must be idempotent — running N times produces the same result as running once.
- Detach mode must use `start_new_session=True` for process group isolation.
- File lock (`~/.kindex/dream.lock` via `fcntl.flock`) prevents concurrent cycles.
- All dream operations must set `prov_activity="dream-cycle"` for provenance tracking.
- Dream results logged to `~/.kindex/logs/dream.log`.
- Dream must never touch nodes of type: constraint, directive, checkpoint.
- `dream_lightweight()` must complete in under 5 seconds for typical graphs (<5000 nodes).

## Requirements

### Fuzzy Deduplication
- `find_duplicates(store, threshold)` returns list of `(node_a, node_b, score)` tuples.
- Similarity scored by normalized title Levenshtein distance + content n-gram overlap.
- Pairs where both nodes have type constraint, directive, or checkpoint are excluded.
- Auto-merge when score >= `merge_threshold` (configurable, default 0.95).
- Below merge_threshold but above `suggest_threshold` (default 0.85): create suggestion.

### Suggestion Auto-Apply
- Pending suggestions where both concept_a and concept_b exist as active nodes
  and have title similarity > 0.7 are auto-applied as `relates_to` edges.
- Applied suggestions marked as `status="accepted"` with `source="dream-auto-apply"`.

### Cluster Consolidation (deep mode only)
- Identify clusters of 4+ nodes within 2-hop radius sharing the same domain.
- For each cluster, use `claude -p` to generate a summary node title and content.
- Summary node linked to all cluster members via `context_of` edges.
- Summary node weight set to max weight in cluster.
- Cluster members' weights are NOT reduced (they remain independently discoverable).

### CLI
- `kin dream` — run full dream cycle (non-LLM parts).
- `kin dream --deep` — include LLM-powered cluster consolidation.
- `kin dream --detach` — fork detached subprocess, return immediately.
- `kin dream --lightweight` — fast path: dedup + suggestion auto-apply only.
- `kin dream --dry-run` — report what would happen without making changes.

### Integration
- `daemon.py:cron_run()` calls `dream_lightweight()` as a new step.
- Stop hook spawns `kin dream --detach --lightweight` after existing compact-hook.
- Detached process uses `start_new_session=True` and redirects stdio to dream.log.
- File lock acquired at start of any dream cycle; if locked, exit silently.
