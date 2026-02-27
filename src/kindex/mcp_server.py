"""Kindex MCP Server — Claude Code plugin for the knowledge graph.

Exposes Kindex tools, resources, and prompts via the Model Context Protocol.
Run with: kin-mcp (stdio transport, for Claude Code integration)
"""

from __future__ import annotations

import atexit
import json
import os
from typing import Any

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    "kindex",
    instructions=(
        "Kindex is a persistent knowledge graph for AI-assisted workflows. "
        "You MUST use these tools proactively throughout every session. "
        "Use 'search' before starting work and before adding to avoid duplicates. "
        "Use 'add' to capture discoveries, decisions, key files, notable outputs, "
        "and new terms you encounter. Use 'link' to connect related concepts. "
        "Use 'learn' to bulk-extract knowledge from long text. "
        "Use 'tag_start'/'tag_update'/'tag_resume' to track session context. "
        "Use 'remind_create' with 'action'/'instructions' for deferred tasks. "
        "Nodes have types (concept, decision, skill, person, project, constraint, etc.) "
        "and are connected by weighted edges."
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
        lines.append(f"Domains: {', '.join(node['domains'])}")
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
def search(query: str, top_k: int = 10) -> str:
    """Search the knowledge graph with hybrid FTS5 + graph traversal.

    USE THIS: before starting work on a topic, before adding nodes (to avoid
    duplicates), and whenever you need context about a concept.

    Uses Reciprocal Rank Fusion to merge full-text and graph results.
    Returns ranked nodes with scores.
    """
    store, _ = _get_store()
    from .retrieve import hybrid_search

    results = hybrid_search(store, query, top_k=top_k)
    if not results:
        return "No results found."

    lines = [f"Found {len(results)} results for '{query}':\n"]
    for i, r in enumerate(results, 1):
        score = r.get("rrf_score", 0)
        lines.append(f"{i}. [{r.get('type', 'concept')}] {r.get('title', r['id'])} "
                      f"(score={score:.3f}, id={r['id']})")
        content = (r.get("content") or "")[:150]
        if content:
            lines.append(f"   {content}")
    return "\n".join(lines)


@mcp.tool()
def add(
    text: str,
    node_type: str = "concept",
    domains: str = "",
    audience: str = "private",
) -> str:
    """Add a knowledge node to the graph.

    USE THIS: when you discover something notable -- a pattern, decision,
    key file, surprising output, new term, or open question. Always search
    first to avoid duplicates.

    The text becomes the node's title and content. Auto-extracts concepts
    and creates links to existing nodes when possible.

    Args:
        text: The knowledge to capture (used as both title and content).
        node_type: Node type (concept, decision, question, skill, person, constraint, directive, watch).
        domains: Comma-separated domain tags (e.g. "engineering,python").
        audience: Visibility scope (private, team, org, public).
    """
    store, config = _get_store()
    from .extract import keyword_extract

    domain_list = [d.strip() for d in domains.split(",") if d.strip()] if domains else []

    # Create the node
    title = text[:60].strip()
    nid = store.add_node(
        title=title,
        content=text,
        node_type=node_type,
        domains=domain_list,
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
    """Create an edge between two nodes.

    Args:
        node_a: Source node ID or title.
        node_b: Target node ID or title.
        relationship: Edge type (relates_to, answers, contradicts, implements, depends_on, etc.).
        weight: Edge weight 0.0-1.0 (default 0.5).
        reason: Why this connection exists.
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
    limit: int = 50,
) -> str:
    """List nodes in the knowledge graph with optional filters.

    Args:
        node_type: Filter by type (concept, decision, skill, person, project, etc.).
        status: Filter by status (active, archived, deprecated).
        audience: Filter by audience (private, team, org, public).
        limit: Maximum number of nodes to return.
    """
    store, _ = _get_store()
    nodes = store.all_nodes(
        node_type=node_type or None,
        status=status or None,
        audience=audience or None,
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
        f"Nodes: {stats.get('node_count', 0)}",
        f"Edges: {stats.get('edge_count', 0)}",
        f"Orphans: {stats.get('orphan_count', 0)}",
    ]

    type_counts = stats.get("type_counts", {})
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
    extra: dict = {"config": cfg}
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


# ── Entry point ───────────────────────────────────────────────────────


def main():
    """Run the Kindex MCP server (stdio transport)."""
    mcp.run()


if __name__ == "__main__":
    main()
