"""Kindex MCP Server — agent plugin for the knowledge graph.

Exposes Kindex tools, resources, and prompts via the Model Context Protocol.
Run with: kin-mcp (stdio transport, for Claude Code, Codex, and other MCP clients)
"""

from __future__ import annotations

import atexit
import json
import os
import sys
from typing import Any

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    print(
        "Error: the 'mcp' package is not installed.\n"
        "Install with: pip install kindex[mcp]  (or: uv tool install kindex[mcp])\n"
        "See: https://github.com/jmcentire/kindex#installation",
        file=sys.stderr,
    )
    sys.exit(1)

mcp = FastMCP(
    "kindex",
    instructions=(
        "Kindex is a persistent knowledge graph that remembers across sessions. "
        "You MUST use these tools proactively — the user depends on this graph as external memory.\n\n"

        "## Session lifecycle\n"
        "1. START: `tag_start` or `tag_resume` to name this session\n"
        "2. ORIENT: `search` the current topic to see what's already known\n"
        "3. DURING: capture as you go (see node types below)\n"
        "4. END: `tag_update` with action='end' and a summary\n\n"

        "## What to capture (use `add` with the right node_type)\n"
        "- concept: patterns, facts, key files, domain terms, how things work\n"
        "- decision: architectural choices, trade-offs, why X over Y\n"
        "- question: open problems, things to investigate later\n"
        "- task: actionable work items — link to related concepts so they surface contextually\n"
        "- skill: demonstrated abilities with evidence\n"
        "- constraint: invariants that MUST hold (hard rules, with trigger/action: warn|verify|block)\n"
        "- directive: behavioral guidelines, style rules (soft rules with scope)\n"
        "- watch: things that need monitoring — known instabilities, flaky tests, "
        "APIs that might break, items needing periodic attention (set owner + expires)\n"
        "- checkpoint: pre-flight checklists — things to verify before an event\n\n"

        "## When to use each tool\n"
        "- `search`: ALWAYS before adding (avoid duplicates) and when starting work on a topic\n"
        "- `add`: capture discoveries as they happen — don't batch, don't wait\n"
        "- `link`: when you notice two concepts relate — specify the relationship type "
        "(relates_to, depends_on, implements, contradicts, blocks, context_of)\n"
        "- `learn`: after reading long files/outputs — bulk-extracts multiple concepts at once\n"
        "- `task_add`: for work items — ALWAYS link to relevant concepts via link_to parameter\n"
        "- `task_done`/`task_list`: manage tasks — they surface contextually via graph proximity\n"
        "- `watch_add`: for ONGOING monitoring — flaky tests, unstable APIs, items to revisit. "
        "Set owner and expires. Watches surface in every session's context automatically.\n"
        "- `watch_resolve`: when a watched issue is fixed or no longer relevant\n"
        "- `remind_create`: for TIME-BASED triggers — use `action` for shell commands, "
        "`instructions` for Claude to follow when the reminder fires\n"
        "- `suggest`: check for bridge opportunities between disconnected graph clusters\n"
        "- `graph_heal`: diagnose graph health — find orphans, bridges, fading nodes\n"
        "- `graph_merge`: merge duplicate nodes (moves edges, archives source)\n"
        "- `ask`: query the graph conversationally\n\n"

        "## Linking strategy\n"
        "The graph's value is in connections. When you add a node, think: what does this relate to? "
        "Use `link` aggressively. Edge types: relates_to (general), depends_on (prerequisite), "
        "implements (realization), contradicts (tension), blocks (impediment), "
        "context_of (background), answers (resolves a question), supersedes (replaces)."
    ),
)

# ── Lazy singleton ────────────────────────────────────────────────────

_store = None
_config = None


def _get_store():
    """Lazy-init Store and Config singletons."""
    global _store, _config
    if _store is None:
        from .config import load_config
        from .store import Store

        _config = load_config()
        _store = Store(_config)
        atexit.register(_store.close)
    return _store, _config


def _json(obj: Any, **kw) -> str:
    """JSON serialize with date/path handling."""
    import datetime
    from pathlib import Path

    def default(o):
        if isinstance(o, (datetime.date, datetime.datetime)):
            return o.isoformat()
        if isinstance(o, Path):
            return str(o)
        if isinstance(o, set):
            return sorted(o)
        raise TypeError(f"Not JSON serializable: {type(o)}")

    return json.dumps(obj, default=default, **kw)


def _node_summary(node: dict) -> str:
    """One-line summary of a node."""
    ntype = node.get("type", "concept")
    title = node.get("title", node.get("id", "?"))
    weight = node.get("weight", 0)
    return f"[{ntype}] {title} (w={weight:.2f}, id={node['id']})"


def _node_detail(store, node: dict) -> str:
    """Multi-line detail view of a node with edges."""
    lines = [
        f"# {node.get('title', node['id'])}",
        f"Type: {node.get('type', 'concept')}  |  Weight: {node.get('weight', 0):.2f}  |  "
        f"Audience: {node.get('audience', 'private')}",
        f"ID: {node['id']}",
    ]
    if node.get("domains"):
        lines.append(f"Tags: {', '.join(node.get('tags') or node.get('domains') or [])}")
    if node.get("aka"):
        lines.append(f"AKA: {', '.join(node['aka'])}")
    if node.get("content"):
        lines.append(f"\n{node['content']}")

    edges = store.edges_from(node["id"])
    if edges:
        lines.append(f"\n## Connections ({len(edges)})")
        for e in edges[:20]:
            lines.append(f"  -> {e.get('to_title', e['to_id'])} ({e['type']}, w={e['weight']:.2f})")

    prov_parts = []
    if node.get("prov_who"):
        prov_parts.append(f"who={node['prov_who']}")
    if node.get("prov_activity"):
        prov_parts.append(f"activity={node['prov_activity']}")
    if node.get("prov_source"):
        prov_parts.append(f"source={node['prov_source']}")
    if prov_parts:
        lines.append(f"\nProvenance: {', '.join(prov_parts)}")

    extra = node.get("extra")
    if extra and isinstance(extra, dict):
        state = extra.get("current_state")
        if state:
            lines.append(f"\nState: {_json(state)}")

    return "\n".join(lines)


# ── Tools ─────────────────────────────────────────────────────────────


@mcp.tool()
def search(query: str, top_k: int = 10, tags: str = "") -> str:
    """Search the knowledge graph with hybrid FTS5 + graph traversal.

    USE THIS: before starting work on a topic, before adding nodes (to avoid
    duplicates), and whenever you need context about a concept.

    Uses Reciprocal Rank Fusion to merge full-text and graph results.
    Returns ranked nodes with scores.

    Args:
        query: Search query text.
        top_k: Maximum results to return.
        tags: Comma-separated tags to filter results (only nodes with these tags).
    """
    store, _ = _get_store()
    from .retrieve import hybrid_search

    fetch_k = top_k * 3 if tags else top_k
    results = hybrid_search(store, query, top_k=fetch_k)

    if tags:
        filter_tags = {t.strip().lower() for t in tags.split(",") if t.strip()}
        results = [r for r in results
                   if filter_tags & {d.lower() for d in (r.get("domains") or r.get("tags") or [])}]
        results = results[:top_k]

    if not results:
        return "No results found."

    from .retrieve import _node_age_str, _staleness_caveat

    lines = [f"Found {len(results)} results for '{query}':\n"]
    for i, r in enumerate(results, 1):
        score = r.get("rrf_score", 0) or r.get("confidence", 0)
        age = _node_age_str(r)
        caveat = _staleness_caveat(r)
        age_tag = f", {age}" if age else ""
        lines.append(f"{i}. [{r.get('type', 'concept')}] {r.get('title', r['id'])} "
                      f"(score={score:.3f}, id={r['id']}{age_tag}){caveat}")
        content = (r.get("content") or "")[:150]
        if content:
            lines.append(f"   {content}")
    return "\n".join(lines)


@mcp.tool()
def add(
    text: str,
    node_type: str = "concept",
    tags: str = "",
    domains: str = "",
    audience: str = "private",
) -> str:
    """Add a knowledge node to the graph. ALWAYS `search` first to avoid duplicates.

    Choose the right node_type — it determines how the node behaves:
    - concept: facts, patterns, key files, domain terms (default, most common)
    - decision: "we chose X over Y because..." — architectural choices with rationale
    - question: open problems to investigate later — gets surfaced until answered
    - task: actionable work — prefer `task_add` instead (supports priority/due/linking)
    - skill: demonstrated ability with evidence
    - constraint: hard rule that MUST hold — set trigger/action in text (e.g. "never push to main without tests")
    - directive: soft behavioral guideline with scope (e.g. "use snake_case in Python modules")
    - watch: something needing periodic attention — flaky test, unstable API, known tech debt.
      Set owner and expiry in text. Gets surfaced in every session until resolved or expired.
    - checkpoint: pre-flight checklist item — verify before a specific event

    Args:
        text: The knowledge to capture (becomes title + content). Be specific.
        node_type: See types above. Default: concept.
        tags: Comma-separated tags for contextual surfacing (e.g. "kindex,python").
        domains: Alias for tags (deprecated, use tags instead).
        audience: Visibility scope (private, team, org, public). Default: private.
    """
    store, config = _get_store()
    from .extract import keyword_extract

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    domain_list = [d.strip() for d in domains.split(",") if d.strip()] if domains else []

    # Create the node
    title = text[:60].strip()
    nid = store.add_node(
        title=title,
        content=text,
        node_type=node_type,
        domains=domain_list,
        tags=tag_list,
        audience=audience,
        prov_activity="mcp-add",
    )

    # Try auto-linking
    existing_titles = [n["title"] for n in store.all_nodes(limit=200)]
    extraction = keyword_extract(text, existing_titles=existing_titles)
    link_count = 0
    for conn in extraction.get("connections", []):
        target = store.get_node_by_title(conn.get("to_title", ""))
        if target and target["id"] != nid:
            store.add_edge(nid, target["id"], edge_type="relates_to", weight=0.4,
                           provenance="auto-linked via MCP")
            link_count += 1

    return f"Created node: {nid} ({node_type})" + (
        f" with {link_count} auto-link(s)" if link_count else ""
    )


@mcp.tool()
def context(
    topic: str = "",
    level: str = "abridged",
    max_tokens: int = 0,
) -> str:
    """Get a formatted context block for injection into conversation.

    Args:
        topic: Topic to search for (auto-detects from cwd if empty).
        level: Context tier (full, abridged, summarized, executive, index).
        max_tokens: Token budget (overrides level with auto-selection if set).
    """
    store, _ = _get_store()
    from .retrieve import format_context_block, hybrid_search

    if topic:
        results = hybrid_search(store, topic, top_k=15)
    else:
        # Fall back to recent high-weight nodes
        results = store.recent_nodes(n=15)

    if not results:
        return "No relevant knowledge found."

    kwargs = {"level": level}
    if max_tokens > 0:
        kwargs = {"max_tokens_approx": max_tokens}

    return format_context_block(store, results, query=topic, **kwargs)


@mcp.tool()
def show(node_id: str) -> str:
    """Show full details of a node including edges and provenance.

    Args:
        node_id: Node ID or title to look up.
    """
    store, _ = _get_store()
    node = store.get_node(node_id) or store.get_node_by_title(node_id)
    if not node:
        return f"Node not found: {node_id}"
    return _node_detail(store, node)


@mcp.tool()
def link(
    node_a: str,
    node_b: str,
    relationship: str = "relates_to",
    weight: float = 0.5,
    reason: str = "",
) -> str:
    """Create an edge between two nodes. Links are the graph's primary value — use liberally.

    Choose the right relationship type:
    - relates_to: general connection (default)
    - depends_on: A requires B to function
    - implements: A is a concrete realization of B
    - contradicts: A and B are in tension
    - blocks: A prevents progress on B
    - answers: A resolves question B
    - supersedes: A replaces B
    - context_of: A provides background for B
    - spawned_from: A was derived from B
    - exemplifies: A is an example of B

    Args:
        node_a: Source node ID or title.
        node_b: Target node ID or title.
        relationship: Edge type (see above). Default: relates_to.
        weight: Edge strength 0.0-1.0. Use 0.7+ for strong connections, 0.3-0.5 for weak ones.
        reason: Why this connection exists (stored as provenance — always provide this).
    """
    store, _ = _get_store()
    a = store.get_node(node_a) or store.get_node_by_title(node_a)
    b = store.get_node(node_b) or store.get_node_by_title(node_b)
    if not a:
        return f"Source node not found: {node_a}"
    if not b:
        return f"Target node not found: {node_b}"

    store.add_edge(a["id"], b["id"], edge_type=relationship, weight=weight,
                   provenance=reason or "linked via MCP")
    return f"Linked: {a['title']} -> {b['title']} ({relationship}, w={weight})"


@mcp.tool()
def list_nodes(
    node_type: str = "",
    status: str = "",
    audience: str = "",
    tags: str = "",
    limit: int = 100,
) -> str:
    """List nodes in the knowledge graph with optional filters.

    Args:
        node_type: Filter by type (concept, decision, skill, person, project, etc.).
        status: Filter by status (active, archived, deprecated).
        audience: Filter by audience (private, team, org, public).
        tags: Filter by tags (comma-separated, AND logic — node must have all).
        limit: Maximum number of nodes to return.
    """
    store, _ = _get_store()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    nodes = store.all_nodes(
        node_type=node_type or None,
        status=status or None,
        audience=audience or None,
        tags=tag_list,
        limit=limit,
    )
    if not nodes:
        return "No nodes found matching filters."

    lines = [f"{len(nodes)} node(s):\n"]
    for n in nodes:
        lines.append(_node_summary(n))
    return "\n".join(lines)


@mcp.tool()
def status() -> str:
    """Get knowledge graph health and statistics.

    Returns node/edge counts, type distribution, orphan count,
    and active operational nodes (constraints, watches, directives).
    """
    store, _ = _get_store()
    stats = store.stats()
    op = store.operational_summary()

    lines = [
        "# Kindex Status\n",
        f"Nodes: {stats.get('nodes', 0)}",
        f"Edges: {stats.get('edges', 0)}",
        f"Orphans: {stats.get('orphans', 0)}",
    ]

    type_counts = stats.get("types", {})
    if type_counts:
        lines.append("\n## Node Types")
        for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
            lines.append(f"  {t}: {c}")

    constraints = op.get("constraints", [])
    if constraints:
        lines.append(f"\n## Active Constraints ({len(constraints)})")
        for c in constraints[:10]:
            lines.append(f"  - {c.get('title', c['id'])}")

    watches = op.get("watches", [])
    if watches:
        lines.append(f"\n## Active Watches ({len(watches)})")
        for w in watches[:10]:
            lines.append(f"  - {w.get('title', w['id'])}")

    return "\n".join(lines)


@mcp.tool()
def ask(question: str) -> str:
    """Ask a question of the knowledge graph.

    Classifies the question type (factual, procedural, decision, exploratory),
    searches for relevant knowledge, and returns a formatted answer.

    Args:
        question: Natural language question.
    """
    store, config = _get_store()
    from .retrieve import format_context_block, hybrid_search

    # Simple question classification
    q_lower = question.lower()
    if any(p in q_lower for p in ["how do i", "how to", "steps to", "guide to"]):
        qtype = "procedural"
    elif any(p in q_lower for p in ["should i", "which is better", " vs ", "pros and cons"]):
        qtype = "decision"
    elif any(p in q_lower for p in ["what is", "who is", "when did", "define"]):
        qtype = "factual"
    else:
        qtype = "exploratory"

    top_k = {"factual": 5, "procedural": 8, "decision": 10, "exploratory": 12}.get(qtype, 10)
    results = hybrid_search(store, question, top_k=top_k)

    if not results:
        return f"[{qtype}] No relevant knowledge found for: {question}"

    level = "full" if qtype in ("procedural", "decision") else "abridged"
    block = format_context_block(store, results, query=question, level=level)
    return f"[{qtype} question]\n\n{block}"


@mcp.tool()
def suggest(limit: int = 10) -> str:
    """Show pending bridge opportunity suggestions.

    These are potential connections between concepts that Kindex detected
    but hasn't confirmed yet.

    Args:
        limit: Maximum suggestions to show.
    """
    store, _ = _get_store()
    suggestions = store.pending_suggestions(limit=limit)
    if not suggestions:
        return "No pending suggestions."

    lines = [f"{len(suggestions)} pending suggestion(s):\n"]
    for s in suggestions:
        lines.append(f"  #{s['id']}: {s['concept_a']} <-> {s['concept_b']}")
        if s.get("reason"):
            lines.append(f"      Reason: {s['reason']}")
    return "\n".join(lines)


@mcp.tool()
def learn(text: str) -> str:
    """Extract knowledge from text and add it to the graph.

    USE THIS: after reading long files, articles, command outputs, or completing
    complex multi-step tasks. Summarize what happened and pass the text here
    for automatic concept extraction and linking.

    Analyzes the text for concepts, decisions, questions, and connections.
    Creates nodes and links automatically.

    Args:
        text: Text to extract knowledge from (session notes, documentation, etc.).
    """
    store, config = _get_store()
    from .budget import BudgetLedger
    from .extract import extract

    ledger = BudgetLedger(config.ledger_path, config.budget)
    existing = [n["title"] for n in store.all_nodes(limit=200)]

    extraction = extract(text, existing, config, ledger)

    created = 0
    linked = 0

    for concept in extraction.get("concepts", [])[:10]:
        existing_node = store.get_node_by_title(concept["title"])
        if existing_node:
            continue
        store.add_node(
            title=concept["title"],
            content=concept.get("content", ""),
            node_type=concept.get("type", "concept"),
            domains=concept.get("domains", []),
            prov_activity="mcp-learn",
        )
        created += 1

    for conn in extraction.get("connections", []):
        a = store.get_node_by_title(conn.get("from_title", ""))
        b = store.get_node_by_title(conn.get("to_title", ""))
        if a and b and a["id"] != b["id"]:
            store.add_edge(a["id"], b["id"],
                           edge_type=conn.get("type", "relates_to"),
                           weight=0.4,
                           provenance=conn.get("why", "extracted via MCP"))
            linked += 1

    decisions = extraction.get("decisions", [])
    questions = extraction.get("questions", [])
    bridges = extraction.get("bridge_opportunities", [])

    # Store bridge suggestions
    for bridge in bridges[:5]:
        store.add_suggestion(
            concept_a=bridge.get("concept_a", ""),
            concept_b=bridge.get("concept_b", ""),
            reason=bridge.get("potential_link", ""),
            source="mcp-learn",
        )

    parts = [f"Extracted: {created} concept(s), {linked} link(s)"]
    if decisions:
        parts.append(f"{len(decisions)} decision(s)")
    if questions:
        parts.append(f"{len(questions)} question(s)")
    if bridges:
        parts.append(f"{len(bridges)} bridge suggestion(s)")
    return ", ".join(parts)


@mcp.tool()
def graph_stats() -> str:
    """Get graph analytics: density, components, centrality, and communities."""
    store, _ = _get_store()
    from .graph import store_bridges, store_centrality, store_communities, store_stats

    stats = store_stats(store)
    centrality = store_centrality(store, method="betweenness", top_k=5)
    communities = store_communities(store)

    lines = [
        "# Graph Analytics\n",
        f"Nodes: {stats.get('nodes', 0)}",
        f"Edges: {stats.get('edges', 0)}",
        f"Density: {stats.get('density', 0):.4f}",
        f"Components: {stats.get('components', 0)}",
        f"Avg Degree: {stats.get('avg_degree', 0):.1f}",
    ]
    if stats.get("truncated"):
        lines.append(f"\n*Note: graph analysis used a subset of nodes. "
                     f"Density/centrality/community stats are approximate.*")

    if centrality:
        lines.append("\n## Top Nodes (Betweenness Centrality)")
        for nid, title, score in centrality:
            lines.append(f"  {title}: {score:.4f}")

    if communities:
        lines.append(f"\n## Communities ({len(communities)})")
        for i, comm in enumerate(communities[:5], 1):
            members = ", ".join(n.get("title", n["id"]) for n in comm[:5])
            lines.append(f"  Cluster {i} ({len(comm)} nodes): {members}")

    return "\n".join(lines)


@mcp.tool()
def graph_heal() -> str:
    """Diagnose and report graph health issues with actionable recommendations.

    Reports:
    - Orphan nodes (no connections) — candidates for linking or archival
    - Disconnected components — candidates for cross-component links
    - Low-weight nodes approaching archive threshold
    - Bridge edges (single points of failure in the graph)

    Use this to understand what needs attention, then use `link`, `add`,
    or other tools to fix issues.
    """
    store, _ = _get_store()
    from .graph import store_bridges, store_stats

    lines = ["# Graph Health Report\n"]

    # Stats overview
    stats = store_stats(store)
    lines.append(f"Nodes: {stats.get('nodes', 0)}, Edges: {stats.get('edges', 0)}, "
                 f"Components: {stats.get('components', 0)}, "
                 f"Density: {stats.get('density', 0):.4f}\n")

    # Orphans
    orphans = store.orphans()
    if orphans:
        lines.append(f"## Orphans ({len(orphans)} — need links or archival)")
        for o in orphans[:10]:
            weight = o.get('weight', 0)
            lines.append(f"  - [{o.get('type', '?')}] {o['title']} "
                         f"(id={o['id']}, w={weight:.2f})")
            if weight < 0.15:
                lines.append(f"    -> Low weight, candidate for archival")
            else:
                lines.append(f"    -> Use `link` to connect to related nodes")
        if len(orphans) > 10:
            lines.append(f"  ... and {len(orphans) - 10} more")
    else:
        lines.append("## Orphans: None (healthy)")

    # Bridges (single points of failure)
    bridges = store_bridges(store, top_k=5)
    if bridges:
        lines.append(f"\n## Bridge Edges (critical connections)")
        for b in bridges:
            lines.append(f"  - {b['from_title']} <-> {b['to_title']} "
                         f"(betweenness: {b['betweenness']:.4f})")
        lines.append("  -> Consider adding parallel links to reduce fragility")

    # Low-weight nodes approaching archive
    try:
        low = store.conn.execute(
            """SELECT id, title, type, weight FROM nodes
               WHERE status = 'active' AND weight < 0.1
               ORDER BY weight ASC LIMIT 10"""
        ).fetchall()
        if low:
            lines.append(f"\n## Fading Nodes ({len(low)} below 0.1 weight)")
            for r in low:
                lines.append(f"  - [{r['type']}] {r['title']} "
                             f"(id={r['id']}, w={r['weight']:.3f})")
            lines.append("  -> Access these nodes to refresh weight, or let them fade to archive")
    except Exception:
        pass

    # Component info
    if stats.get('components', 0) > 1:
        lines.append(f"\n## Disconnected Components: {stats['components']}")
        lines.append("  -> Use `suggest` to find cross-component link candidates")

    return "\n".join(lines)


@mcp.tool()
def graph_merge(source_id: str, target_id: str, keep: str = "target") -> str:
    """Merge two nodes that represent the same concept.

    Moves all edges from the source node to the target node, then archives
    the source. Use when you find duplicate or near-duplicate nodes.

    Args:
        source_id: Node to merge FROM (will be archived).
        target_id: Node to merge INTO (will receive edges).
        keep: Which node to keep: 'target' (default) or 'source'.
    """
    store, _ = _get_store()

    if keep == "source":
        source_id, target_id = target_id, source_id

    source = store.get_node(source_id)
    target = store.get_node(target_id)
    if not source:
        return f"Source node not found: {source_id}"
    if not target:
        return f"Target node not found: {target_id}"

    # Move edges from source to target
    moved = 0
    for edge in store.edges_from(source_id):
        if edge["to_id"] != target_id:
            store.add_edge(target_id, edge["to_id"],
                           edge_type=edge.get("type", "relates_to"),
                           weight=edge.get("weight", 0.3),
                           provenance=f"merged from {source['title']}")
            moved += 1
    for edge in store.edges_to(source_id):
        if edge["from_id"] != target_id:
            store.add_edge(edge["from_id"], target_id,
                           edge_type=edge.get("type", "relates_to"),
                           weight=edge.get("weight", 0.3),
                           provenance=f"merged from {source['title']}")
            moved += 1

    # Merge content if source has content the target lacks
    source_content = source.get("content", "")
    target_content = target.get("content", "")
    if source_content and source_content not in (target_content or ""):
        merged_content = f"{target_content}\n\n[Merged from: {source['title']}]\n{source_content}"
        store.update_node(target_id, content=merged_content)

    # Boost target weight
    source_weight = source.get("weight", 0.5)
    target_weight = target.get("weight", 0.5)
    store.update_node(target_id, weight=min(1.0, max(target_weight, source_weight)))

    # Archive source
    store.update_node(source_id, status="archived", weight=0.01,
                      extra={"merged_into": target_id})

    return (f"Merged '{source['title']}' into '{target['title']}': "
            f"{moved} edges moved, source archived.")


@mcp.tool()
def dream(
    mode: str = "lightweight",
    dry_run: bool = False,
) -> str:
    """Run knowledge consolidation (dream cycle).

    Performs memory consolidation: fuzzy deduplication, suggestion
    auto-application, and domain-based edge strengthening. Like sleep
    consolidating memory — replay, strengthen, prune.

    Args:
        mode: 'lightweight' (fast, <5s), 'full' (non-LLM), or 'deep' (LLM clusters).
        dry_run: If True, report what would happen without making changes.
    """
    store, config = _get_store()

    from .dream import dream_cycle

    results = dream_cycle(config, store, mode=mode, dry_run=dry_run)

    if results.get("skipped"):
        return f"Dream skipped: {results['skipped']}"

    lines = [f"Dream ({results.get('mode', mode)}) complete:"]
    lines.append(f"  Merged: {results.get('merged', 0)}")
    lines.append(f"  Suggested: {results.get('suggested', 0)}")
    lines.append(f"  Suggestions applied: {results.get('suggestions_applied', 0)}")
    if "edges_strengthened" in results:
        lines.append(f"  Edges strengthened: {results['edges_strengthened']}")
    if "cluster_summaries" in results:
        lines.append(f"  Cluster summaries: {results['cluster_summaries']}")
    return "\n".join(lines)


@mcp.tool()
def changelog(since: str = "", days: int = 7) -> str:
    """Show recent changes to the knowledge graph.

    Args:
        since: ISO date/timestamp to look back from (e.g. '2026-02-20').
        days: Look back N days from now (default 7, ignored if 'since' is set).
    """
    store, _ = _get_store()
    import datetime

    if since:
        since_iso = since
    else:
        dt = datetime.datetime.now(tz=None) - datetime.timedelta(days=days)
        since_iso = dt.isoformat(timespec="seconds")

    entries = store.activity_since(since_iso)
    if not entries:
        return f"No changes since {since_iso}."

    lines = [f"{len(entries)} change(s) since {since_iso}:\n"]
    for e in entries[:50]:
        ts = e.get("timestamp", "?")[:19]
        action = e.get("action", "?")
        target = e.get("node_id", "?")
        actor = e.get("actor", "")
        detail = e.get("detail", "")
        actor_str = f" by {actor}" if actor else ""
        lines.append(f"  {ts} {action} {target}{actor_str}")
        if detail:
            lines.append(f"    {detail[:80]}")
    return "\n".join(lines)


@mcp.tool()
def ingest(source: str, limit: int = 50, repo: str = "", since: str = "") -> str:
    """Ingest knowledge from external sources.

    Args:
        source: Adapter name (github, linear, files, commits, projects, sessions) or 'all'.
        limit: Maximum items to ingest per adapter.
        repo: GitHub owner/repo for github adapter (e.g. 'jmcentire/kindex').
        since: ISO date — only ingest items after this date.
    """
    from .adapters.pipeline import IngestConfig, run_adapter, run_all
    from .adapters.registry import discover, get

    store, cfg = _get_store()
    config = IngestConfig(since=since or None, limit=limit, verbose=False)
    extra: dict = {"_config": cfg}
    if repo:
        extra["repo"] = repo

    if source == "all":
        results = run_all(store, config, **extra)
        lines = []
        for name, result in sorted(results.items()):
            lines.append(f"  {name}: {result}")
        total = sum(r.created + r.updated for r in results.values())
        lines.append(f"\nTotal: {total} node(s) across {len(results)} adapter(s)")
        return "\n".join(lines)

    adapter = get(source)
    if not adapter:
        names = ", ".join(sorted(discover().keys()))
        return f"Unknown adapter '{source}'. Available: {names}, all"

    result = run_adapter(adapter, store, config, **extra)
    if result.errors:
        return f"Errors: {'; '.join(result.errors)}"
    return f"{adapter.meta.name}: {result}"


# ── Resources ─────────────────────────────────────────────────────────


@mcp.resource("kindex://status")
def resource_status() -> str:
    """Current knowledge graph statistics."""
    store, _ = _get_store()
    stats = store.stats()
    return _json(stats, indent=2)


@mcp.resource("kindex://node/{node_id}")
def resource_node(node_id: str) -> str:
    """Full details of a specific knowledge node."""
    store, _ = _get_store()
    node = store.get_node(node_id) or store.get_node_by_title(node_id)
    if not node:
        return f"Node not found: {node_id}"
    return _node_detail(store, node)


@mcp.resource("kindex://recent")
def resource_recent() -> str:
    """Recently active nodes in the knowledge graph."""
    store, _ = _get_store()
    nodes = store.recent_nodes(n=20)
    lines = [_node_summary(n) for n in nodes]
    return "\n".join(lines) if lines else "No recent nodes."


@mcp.resource("kindex://orphans")
def resource_orphans() -> str:
    """Nodes with no connections (candidates for linking or removal)."""
    store, _ = _get_store()
    orphans = store.orphans()
    if not orphans:
        return "No orphan nodes."
    lines = [_node_summary(n) for n in orphans]
    return f"{len(orphans)} orphan(s):\n" + "\n".join(lines)


# ── Prompts ───────────────────────────────────────────────────────────


@mcp.prompt()
def prime(topic: str = "") -> str:
    """Generate a full context priming block for the current session.

    Args:
        topic: Optional topic to focus on.
    """
    store, _ = _get_store()
    from .retrieve import format_context_block, hybrid_search

    if topic:
        results = hybrid_search(store, topic, top_k=15)
    else:
        results = store.recent_nodes(n=15)

    if not results:
        return "No knowledge available for priming."

    stats = store.stats()
    header = (
        f"# Kindex Context\n\n"
        f"Graph: {stats.get('node_count', 0)} nodes, {stats.get('edge_count', 0)} edges\n\n"
    )
    block = format_context_block(store, results, query=topic, level="full")
    return header + block


@mcp.prompt()
def orient() -> str:
    """Quick orientation: graph stats, recent activity, and key nodes."""
    store, _ = _get_store()
    from .graph import store_stats

    stats = store_stats(store)
    recent = store.recent_nodes(n=10)
    op = store.operational_summary()

    lines = [
        "# Kindex Orientation\n",
        f"Graph: {stats.get('nodes', 0)} nodes, {stats.get('edges', 0)} edges, "
        f"{stats.get('components', 0)} component(s)\n",
    ]

    if recent:
        lines.append("## Recently Active")
        for n in recent[:10]:
            lines.append(f"  - {_node_summary(n)}")

    constraints = op.get("constraints", [])
    if constraints:
        lines.append(f"\n## Active Constraints ({len(constraints)})")
        for c in constraints[:5]:
            lines.append(f"  - {c.get('title', c['id'])}")

    watches = op.get("watches", [])
    if watches:
        lines.append(f"\n## Active Watches ({len(watches)})")
        for w in watches[:5]:
            lines.append(f"  - {w.get('title', w['id'])}")

    return "\n".join(lines)


# ── Session tags ──────────────────────────────────────────────────────


@mcp.tool()
def tag_start(name: str, description: str = "", focus: str = "",
              remaining: str = "") -> str:
    """Start a new session tag for tracking work context.

    Args:
        name: Human-readable tag name (e.g. 'auth-refactor').
        description: What this session is about.
        focus: Current focus area.
        remaining: Comma-separated list of remaining items.
    """
    store, _ = _get_store()
    from .sessions import start_tag
    import os
    remaining_list = [r.strip() for r in remaining.split(",") if r.strip()] if remaining else []
    try:
        nid = start_tag(store, name, description=description, focus=focus,
                        remaining=remaining_list, project_path=os.getcwd())
        return f"Started session tag: {name} ({nid})"
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def tag_update(name: str = "", focus: str = "", description: str = "",
               remaining: str = "", add_remaining: str = "",
               done: str = "", summary: str = "",
               action: str = "update") -> str:
    """Update, segment, pause, or end a session tag.

    Args:
        name: Tag name (auto-detects active tag if empty).
        focus: New focus area (used for update and segment actions).
        description: Updated description.
        remaining: Replace remaining items (comma-separated).
        add_remaining: Add items to remaining (comma-separated).
        done: Remove items from remaining (comma-separated).
        summary: Summary for segment/pause/end actions.
        action: One of: update, segment, pause, end.
    """
    store, _ = _get_store()
    from .sessions import (update_tag, add_segment, pause_tag,
                           complete_tag, get_active_tag, get_tag)
    import os

    if not name:
        active = get_active_tag(store, project_path=os.getcwd())
        if not active:
            return "No active session tag found. Start one with tag_start."
        name = (active.get("extra") or {}).get("tag", active["title"])

    try:
        if action == "update":
            update_tag(
                store, name,
                focus=focus or None,
                description=description or None,
                remaining=[r.strip() for r in remaining.split(",") if r.strip()] if remaining else None,
                append_remaining=[r.strip() for r in add_remaining.split(",") if r.strip()] if add_remaining else None,
                remove_remaining=[r.strip() for r in done.split(",") if r.strip()] if done else None,
            )
            return f"Updated tag: {name}"
        elif action == "segment":
            add_segment(store, name, new_focus=focus or "New segment", summary=summary)
            return f"New segment on {name}: {focus}"
        elif action == "pause":
            pause_tag(store, name, summary=summary)
            return f"Paused: {name}"
        elif action == "end":
            complete_tag(store, name, summary=summary)
            return f"Completed: {name}"
        return f"Unknown action: {action}"
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def tag_resume(name: str = "", tokens: int = 1500) -> str:
    """Resume a session tag — get full context for continuing work.

    Args:
        name: Tag name to resume (shows active/paused tags if empty).
        tokens: Token budget for context block.
    """
    store, _ = _get_store()
    from .sessions import format_resume_context, list_tags

    if not name:
        tags = list_tags(store, status="active", limit=5)
        tags += list_tags(store, status="paused", limit=5)
        if not tags:
            return "No active or paused session tags."
        lines = ["Available session tags:\n"]
        for t in tags:
            extra = t.get("extra") or {}
            lines.append(f"  [{extra.get('session_status', '?')}] "
                         f"{extra.get('tag', t['title'])}: "
                         f"{extra.get('current_focus', '')[:60]}")
        return "\n".join(lines)

    return format_resume_context(store, name, max_tokens=tokens)


# ── Tasks ─────────────────────────────────────────────────────────────


@mcp.tool()
def task_add(text: str, priority: int = 3, due: str = "",
             scope: str = "contextual", link_to: str = "",
             effort: str = "") -> str:
    """Add a task to the knowledge graph.

    Tasks are graph-connected -- link them to concepts, projects, or other
    nodes so they surface contextually when you're working in related areas.
    Use link_to to connect tasks to existing graph nodes by ID or title.

    Args:
        text: Task title/description.
        priority: 1=urgent 2=high 3=normal 4=low 5=someday.
        due: Optional due date ('tomorrow', '2026-03-15', 'in 3 days').
        scope: 'global' (always visible) or 'contextual' (surfaces by proximity).
        link_to: Comma-separated node IDs or titles to link this task to.
        effort: Optional effort estimate (small, medium, large).
    """
    store, _ = _get_store()
    from .tasks import create_task
    links = [s.strip() for s in link_to.split(",") if s.strip()] if link_to else None
    task_id = create_task(
        store, text,
        priority=priority,
        due=due or None,
        scope=scope,
        effort=effort or None,
        link_to=links,
    )
    node = store.get_node(task_id)
    extra = node.get("extra", {}) if node else {}
    p_label = {1: "urgent", 2: "high", 3: "normal", 4: "low", 5: "someday"}.get(
        extra.get("priority", 3), "normal")
    due_info = f", due: {extra.get('due', '')}" if extra.get("due") else ""
    return f"Created task: {task_id} [{p_label}]{due_info} — {text}"


@mcp.tool()
def task_list(status: str = "open", scope: str = "",
              priority: str = "") -> str:
    """List tasks, optionally filtered.

    Args:
        status: Filter: open, in_progress, done, all. Default: open.
        scope: Filter: global, contextual, or empty for both.
        priority: Max priority level to show (1-5). Empty for all.
    """
    store, _ = _get_store()
    from .tasks import list_tasks, format_task_list
    tasks = list_tasks(
        store,
        status=status,
        scope=scope or None,
    )
    if priority:
        try:
            max_pri = int(priority)
            tasks = [t for t in tasks
                     if (t.get("extra") or {}).get("priority", 3) <= max_pri]
        except ValueError:
            pass
    if not tasks:
        return "No tasks found."
    return format_task_list(tasks)


@mcp.tool()
def task_done(id: str) -> str:
    """Mark a task as completed.

    Args:
        id: Task node ID.
    """
    store, _ = _get_store()
    from .tasks import complete_task
    result = complete_task(store, id)
    if result:
        return f"Completed: {result['title']} ({id})"
    return f"Task not found: {id}"


# ── Watches ──────────────────────────────────────────────────────────


@mcp.tool()
def watch_add(text: str, owner: str = "", expires: str = "",
              link_to: str = "") -> str:
    """Create a watch node for something needing periodic attention.

    Watches surface in every session's context. Use for:
    - Flaky tests, unstable APIs, known tech debt
    - Pending decisions, things to revisit
    - Anything Claude should keep an eye on

    Args:
        text: What to watch (becomes the node title).
        owner: Who owns this watch (person or team).
        expires: When this watch expires (YYYY-MM-DD). Auto-archived after expiry.
        link_to: Comma-separated node IDs/titles to link this watch to.
    """
    store, _ = _get_store()
    import os

    extra = {"watch_status": "active"}
    if owner:
        extra["owner"] = owner
    if expires:
        extra["expires"] = expires

    domains = []
    project_path = os.environ.get("PWD", "")
    if project_path:
        extra["project_path"] = project_path

    nid = store.add_node(
        title=text,
        content="",
        node_type="watch",
        domains=domains,
        prov_activity="mcp-watch-add",
        extra=extra,
    )

    # Link to specified nodes
    if link_to:
        for ref in link_to.split(","):
            ref = ref.strip()
            if not ref:
                continue
            target = store.get_node(ref) or store.get_node_by_title(ref)
            if target:
                store.add_edge(nid, target["id"], edge_type="relates_to",
                               weight=0.5, provenance="watch context")

    exp = f", expires {expires}" if expires else ""
    own = f", owner: {owner}" if owner else ""
    return f"Watch created: {text} (id={nid}{exp}{own})"


@mcp.tool()
def watch_list(status: str = "active") -> str:
    """List watch nodes.

    Args:
        status: Filter by status: active (default), archived, all.
    """
    store, _ = _get_store()
    if status == "all":
        watches = store.all_nodes(node_type="watch", limit=50)
    elif status == "archived":
        watches = store.all_nodes(node_type="watch", status="archived", limit=50)
    else:
        watches = store.active_watches()

    if not watches:
        return "No watches found."

    lines = [f"Watches ({len(watches)}):"]
    for w in watches:
        extra = w.get("extra") or {}
        parts = [f"- {w['title']} (id={w['id']})"]
        if extra.get("owner"):
            parts.append(f"@{extra['owner']}")
        if extra.get("expires"):
            parts.append(f"expires {extra['expires']}")
        lines.append(" ".join(parts))
    return "\n".join(lines)


@mcp.tool()
def watch_resolve(id: str, reason: str = "") -> str:
    """Resolve/archive a watch node.

    Args:
        id: Watch node ID.
        reason: Why this watch is being resolved.
    """
    store, _ = _get_store()
    node = store.get_node(id)
    if not node or node.get("type") != "watch":
        return f"Watch not found: {id}"

    extra = node.get("extra") or {}
    extra["resolved_reason"] = reason
    extra["watch_status"] = "resolved"
    store.update_node(id, status="archived", extra=extra, weight=0.01)
    return f"Resolved watch: {node['title']} ({id})"


# ── Reminders ─────────────────────────────────────────────────────────


@mcp.tool()
def remind_create(text: str, when: str, priority: str = "normal",
                  channels: str = "", action: str = "",
                  instructions: str = "") -> str:
    """Create a reminder with natural language time parsing.

    Args:
        text: What to be reminded about.
        when: When to fire: 'in 30 minutes', 'tomorrow at 3pm', 'every weekday at 9am'.
        priority: Priority level (low, normal, high, urgent).
        channels: Comma-separated notification channels (system, slack, email, claude).
        action: Shell command to execute when the reminder fires (optional).
        instructions: Natural language instructions for Claude to follow when due (optional).
    """
    store, config = _get_store()
    from .reminders import create_reminder
    channel_list = [c.strip() for c in channels.split(",") if c.strip()] if channels else None
    try:
        rid = create_reminder(
            store, text, when,
            priority=priority, channels=channel_list,
            action_command=action,
            action_instructions=instructions,
        )
        r = store.get_reminder(rid)
        action_info = ""
        if action or instructions:
            action_info = f", action: {'shell' if action and not instructions else 'claude'}"
        return f"Created reminder: {rid} (next due: {r['next_due']}{action_info})"
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def remind_list(status: str = "active", priority: str = "") -> str:
    """List reminders with optional filters.

    Args:
        status: Filter by status (active, snoozed, fired, all).
        priority: Filter by priority (low, normal, high, urgent).
    """
    store, _ = _get_store()
    from .reminders import format_reminder_list
    s = None if status == "all" else status
    p = priority or None
    reminders = store.list_reminders(status=s, priority=p)
    if not reminders:
        return "No reminders found."
    return format_reminder_list(reminders)


@mcp.tool()
def remind_snooze(id: str, duration: str = "") -> str:
    """Snooze a reminder.

    Args:
        id: Reminder ID.
        duration: Snooze duration (e.g. '15m', '1h'). Uses config default if empty.
    """
    store, config = _get_store()
    from .reminders import parse_duration, snooze_reminder
    dur = parse_duration(duration) if duration else None
    try:
        new_time = snooze_reminder(store, id, dur, config)
        return f"Snoozed until: {new_time}"
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def remind_done(id: str) -> str:
    """Mark a reminder as completed.

    Args:
        id: Reminder ID.
    """
    store, _ = _get_store()
    from .reminders import complete_reminder
    try:
        complete_reminder(store, id)
        return f"Completed reminder: {id}"
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def remind_check() -> str:
    """Check for due reminders and fire notifications.

    Runs the reminder check cycle: finds due reminders, sends notifications,
    handles auto-snooze for stale fired reminders.
    """
    store, config = _get_store()
    from .reminders import auto_snooze_stale, check_and_fire
    fired = check_and_fire(store, config)
    snoozed = auto_snooze_stale(store, config)
    if not fired and snoozed == 0:
        return "No due reminders."
    parts = []
    if fired:
        parts.append(f"{len(fired)} reminder(s) fired")
        for r in fired:
            parts.append(f"  - {r['title']} [{r.get('priority', 'normal')}]")
    if snoozed:
        parts.append(f"{snoozed} auto-snoozed")
    return "\n".join(parts)


@mcp.tool()
def remind_exec(id: str) -> str:
    """Manually trigger a reminder's action.

    Args:
        id: Reminder ID.
    """
    store, config = _get_store()
    from .actions import execute_action, has_action
    r = store.get_reminder(id)
    if not r:
        return f"Error: Reminder not found: {id}"
    if not has_action(r):
        return f"Error: Reminder {id} has no action defined."
    result = execute_action(store, r, config)
    return f"Action {result['status']}: {result.get('output', '')[:500]}"


# ── Modes ─────────────────────────────────────────────────────────────


@mcp.tool()
def mode_activate(name: str, session_context: str = "") -> str:
    """Activate a conversation mode. Returns the priming artifact to inject.

    Modes are state inductions, not instructions. They shift how you think,
    not what you think about. Based on research showing induced understanding
    outperforms direct instruction by 5.4x.

    Built-in modes: collaborate, code, create, research, chat.
    Custom modes can be created with mode_create.

    Args:
        name: Mode name (e.g. 'collaborate', 'code', 'create').
        session_context: Optional prior session context to resume from.
    """
    store, _ = _get_store()
    from .modes import activate_mode
    return activate_mode(store, name, session_context=session_context or None)


@mcp.tool()
def mode_list() -> str:
    """List available conversation modes (built-in and custom)."""
    store, _ = _get_store()
    from .modes import list_modes, format_mode_list, DEFAULT_MODES
    modes = list_modes(store)
    return format_mode_list(modes, defaults=DEFAULT_MODES)


@mcp.tool()
def mode_show(name: str) -> str:
    """Show details of a conversation mode including its primer, boundary, and permissions.

    Args:
        name: Mode name.
    """
    store, _ = _get_store()
    from .modes import get_mode, format_mode_detail, DEFAULT_MODES
    mode = get_mode(store, name)
    default = DEFAULT_MODES.get(name) if not mode else None
    return format_mode_detail(name, mode=mode, default=default)


@mcp.tool()
def mode_create(name: str, primer: str, boundary: str, permissions: str,
                description: str = "", link_to: str = "") -> str:
    """Create a custom conversation mode from a primer, boundary, and permissions.

    A primer is a state induction (~80 words) that establishes how to think.
    A boundary defines what quality means for this mode.
    Permissions state what's explicitly allowed (tangents, pushback, etc).

    Args:
        name: Mode name (short, lowercase, no spaces).
        primer: The mode-setting passage. Under 80 words. Not instructions.
        boundary: 2-3 sentences defining output quality for this mode.
        permissions: What's explicitly permitted to keep the conversation alive.
        description: Optional one-line description.
        link_to: Comma-separated node IDs/titles to link this mode to.
    """
    store, _ = _get_store()
    from .modes import create_mode
    links = [s.strip() for s in link_to.split(",") if s.strip()] if link_to else None
    mode_id = create_mode(
        store, name,
        primer=primer,
        boundary=boundary,
        permissions=permissions,
        description=description,
        link_to=links,
    )
    return f"Created mode: {name} ({mode_id})"


@mcp.tool()
def mode_export(name: str) -> str:
    """Export a mode as a portable, PII-free artifact (JSON).

    Args:
        name: Mode name to export.
    """
    store, _ = _get_store()
    from .modes import export_mode
    import json
    artifact = export_mode(store, name)
    if not artifact:
        return f"Mode not found: {name}"
    return json.dumps(artifact, indent=2)


@mcp.tool()
def mode_import(artifact_json: str) -> str:
    """Import a mode from a portable artifact (JSON string).

    Args:
        artifact_json: JSON string of the mode artifact.
    """
    store, _ = _get_store()
    from .modes import import_mode
    import json
    try:
        artifact = json.loads(artifact_json)
    except json.JSONDecodeError as e:
        return f"Invalid JSON: {e}"
    try:
        mode_id = import_mode(store, artifact)
        return f"Imported mode: {artifact.get('name', '?')} ({mode_id})"
    except ValueError as e:
        return f"Error: {e}"


@mcp.tool()
def mode_seed() -> str:
    """Seed the default conversation modes into the graph. Idempotent."""
    store, _ = _get_store()
    from .modes import seed_defaults
    created = seed_defaults(store)
    if created:
        return f"Seeded {len(created)} modes: {', '.join(created)}"
    return "All default modes already exist."


# ── Entry point ───────────────────────────────────────────────────────


def main():
    """Run the Kindex MCP server (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
