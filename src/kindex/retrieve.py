"""Hybrid retrieval engine — FTS5 + graph traversal + Reciprocal Rank Fusion.

Supports five context tiers, each optimized for a different token budget:

  full        ~4000 tokens — everything: all nodes, edges, provenance, open questions
  abridged    ~1500 tokens — key nodes, trimmed content, edges preserved
  summarized  ~750 tokens  — paragraph-form synthesized narrative per domain cluster
  executive   ~200 tokens  — 2-3 sentences per active thread
  index       ~100 tokens  — node titles and edge types only, no content

Auto-selects based on estimated available token budget when level is not specified.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store

# Context tier token budgets (approximate)
TIER_BUDGETS = {
    "full": 4000,
    "abridged": 1500,
    "summarized": 750,
    "executive": 200,
    "index": 100,
}

TIER_ORDER = ["full", "abridged", "summarized", "executive", "index"]

_FRONTMATTER_RE = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.DOTALL)


def _strip_frontmatter(text: str) -> str:
    """Remove YAML frontmatter (---...---) from content."""
    return _FRONTMATTER_RE.sub("", text).lstrip()


def _rrf_merge(*ranked_lists: list[tuple[str, float]], k: int = 60) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion across multiple ranked result lists.

    Each input is [(node_id, score), ...] in descending score order.
    Returns merged [(node_id, rrf_score)] sorted by rrf_score descending.
    """
    scores: dict[str, float] = defaultdict(float)

    for ranked in ranked_lists:
        for rank, (nid, _) in enumerate(ranked):
            scores[nid] += 1.0 / (k + rank + 1)

    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def hybrid_search(
    store: Store,
    query: str,
    top_k: int = 10,
    expand_graph: bool = True,
    graph_hops: int = 1,
) -> list[dict]:
    """Hybrid search combining FTS5 + graph expansion.

    1. FTS5 full-text search (BM25)
    2. Graph traversal from FTS hits (if expand_graph=True)
    3. Merge via Reciprocal Rank Fusion
    4. Return enriched node dicts with edges

    Returns list of node dicts with added 'rrf_score' key.
    """
    # Mode 1: FTS5 search
    fts_results = store.fts_search(query, limit=top_k * 3)
    fts_ranked = [(r["id"], abs(r.get("rank", 0)) + r.get("weight", 0))
                  for r in fts_results]

    # Mode 2: Graph expansion from FTS hits
    graph_ranked: list[tuple[str, float]] = []
    if expand_graph and fts_ranked:
        seen = {nid for nid, _ in fts_ranked}
        for nid, fts_score in fts_ranked[:5]:  # expand top 5 FTS hits
            edges = store.edges_from(nid)
            for edge in edges:
                target = edge["to_id"]
                if target not in seen:
                    seen.add(target)
                    graph_ranked.append((target, edge["weight"] * fts_score))

    # Mode 3: Vector search (if available)
    vec_ranked: list[tuple[str, float]] = []
    try:
        from .vectors import is_available, vector_search
        if is_available():
            vec_results = vector_search(store, query, top_k=top_k)
            vec_ranked = [(r["id"], 1.0 / (1.0 + r.get("vec_distance", 1.0)))
                          for r in vec_results]
    except Exception:
        pass

    # Merge via RRF
    ranked_lists = [fts_ranked]
    if graph_ranked:
        ranked_lists.append(graph_ranked)
    if vec_ranked:
        ranked_lists.append(vec_ranked)

    if len(ranked_lists) > 1:
        merged = _rrf_merge(*ranked_lists)
    else:
        merged = fts_ranked

    # Fetch full nodes for top results
    results = []
    for nid, rrf_score in merged[:top_k]:
        node = store.get_node(nid)
        if node:
            node["rrf_score"] = round(rrf_score, 6)
            node["edges_out"] = store.edges_from(nid)[:5]
            results.append(node)

    return results


def auto_select_tier(available_tokens: int | None = None) -> str:
    """Select the best context tier for the given token budget.

    If available_tokens is None, defaults to 'abridged' (safe middle ground).
    """
    if available_tokens is None:
        return "abridged"
    for tier in TIER_ORDER:
        if TIER_BUDGETS[tier] <= available_tokens:
            return tier
    return "index"


def format_context_block(
    store: Store,
    results: list[dict],
    query: str = "",
    level: str | None = None,
    max_tokens_approx: int | None = None,
) -> str:
    """Format search results as a context block for CLAUDE.md injection.

    Supports five tiers: full, abridged, summarized, executive, index.
    Auto-selects tier based on max_tokens_approx if level is not specified.
    """
    if not results:
        return "## Kindex: No relevant context found.\n"

    if level is None:
        level = auto_select_tier(max_tokens_approx)

    formatter = _TIER_FORMATTERS.get(level, _format_abridged)
    return formatter(store, results, query)


def _gather_domains(results: list[dict]) -> set[str]:
    domains: set[str] = set()
    for r in results:
        for d in (r.get("domains") or []):
            domains.add(d)
    return domains


def _append_operational(store: Store, lines: list[str], verbose: bool = False) -> None:
    """Append active operational nodes (constraints, watches, etc.) to output."""
    ops = store.operational_summary()

    if ops["constraints"]:
        lines.append("\n### Active constraints")
        for c in ops["constraints"][:5 if verbose else 3]:
            extra = c.get("extra") or {}
            action = extra.get("action", "warn")
            lines.append(f"- [{action}] {c['title']}")
            if verbose and extra.get("trigger"):
                lines.append(f"  trigger: {extra['trigger']}")

    if ops["watches"]:
        lines.append("\n### Watches")
        for w in ops["watches"][:5 if verbose else 3]:
            extra = w.get("extra") or {}
            parts = [f"! {w['title']}"]
            if extra.get("owner"):
                parts.append(f"@{extra['owner']}")
            if extra.get("expires"):
                parts.append(f"(expires {extra['expires']})")
            lines.append(f"- {' '.join(parts)}")

    if verbose and ops["checkpoints"]:
        lines.append("\n### Checkpoints")
        for cp in ops["checkpoints"][:5]:
            trig = (cp.get("extra") or {}).get("trigger", "")
            lines.append(f"- [ ] {cp['title']}" + (f" (trigger: {trig})" if trig else ""))

    if verbose and ops["directives"]:
        lines.append("\n### Directives")
        for d in ops["directives"][:5]:
            scope = (d.get("extra") or {}).get("scope", "")
            lines.append(f"- {d['title']}" + (f" [scope: {scope}]" if scope else ""))


# ── Full tier ─────────────────────────────────────────────────────────

def _format_full(store: Store, results: list[dict], query: str) -> str:
    """Full context — everything Kindex knows about the active domain."""
    all_domains = _gather_domains(results)

    lines = [
        "## Relevant Context (Kindex — auto-loaded)",
        f"**Level:** full | **Query:** {query}",
        f"**Active domains:** [{', '.join(sorted(all_domains)[:8])}]",
        "",
        "### Key concepts",
    ]

    for r in results:
        title = r.get("title", r["id"])
        node_type = r.get("type", "concept")
        content = _strip_frontmatter(r.get("content") or "")[:600]
        weight = r.get("weight", 0)
        edges_out = r.get("edges_out", [])

        lines.append(f"\n#### [{node_type}] {title} (w={weight:.2f})")
        if content:
            lines.append(content)

        # Provenance
        prov = []
        if r.get("prov_source"):
            prov.append(f"source: {r['prov_source']}")
        if r.get("prov_when"):
            prov.append(f"when: {r['prov_when'][:10]}")
        if r.get("prov_activity"):
            prov.append(f"via: {r['prov_activity']}")
        if prov:
            lines.append(f"*Provenance: {', '.join(prov)}*")

        if r.get("aka"):
            lines.append(f"*AKA: {', '.join(r['aka'])}*")

        if edges_out:
            connected = [f"{e.get('to_title', e['to_id'])} [{e['type']}]" for e in edges_out[:8]]
            lines.append(f"*Connects: {', '.join(connected)}*")

    # Open questions
    questions = store.all_nodes(node_type="question", status="active", limit=5)
    if questions:
        lines.append("\n### Open questions")
        for q in questions:
            lines.append(f"- {q['title']}")
            if q.get("content"):
                lines.append(f"  Context: {_strip_frontmatter(q['content'])[:200]}")

    # Recent decisions
    decisions = store.all_nodes(node_type="decision", limit=5)
    if decisions:
        lines.append("\n### Recent decisions")
        for d in decisions:
            when = d.get("prov_when", "")[:10]
            lines.append(f"- {when}: {d['title']}")
            if d.get("content"):
                lines.append(f"  Rationale: {_strip_frontmatter(d['content'])[:200]}")

    # Operational nodes
    _append_operational(store, lines, verbose=True)

    return "\n".join(lines) + "\n"


# ── Abridged tier ─────────────────────────────────────────────────────

def _format_abridged(store: Store, results: list[dict], query: str) -> str:
    """Abridged — key nodes, trimmed content, edges preserved."""
    all_domains = _gather_domains(results)

    lines = [
        "## Relevant Context (Kindex — auto-loaded)",
        f"**Level:** abridged | **Active domains:** [{', '.join(sorted(all_domains)[:8])}]",
        "",
        "### Key concepts",
    ]

    char_budget = 6000  # ~1500 tokens
    used = sum(len(l) for l in lines)

    for r in results:
        title = r.get("title", r["id"])
        node_type = r.get("type", "concept")
        content_preview = _strip_frontmatter(r.get("content") or "")[:200]
        edges_out = r.get("edges_out", [])
        connected = ", ".join(e.get("to_title", e["to_id"]) for e in edges_out[:3])

        block = f"- **{title}** ({node_type}): {content_preview}"
        if connected:
            block += f"\n  *Connected to: {connected}*"
        block += "\n"

        if used + len(block) > char_budget:
            break
        lines.append(block)
        used += len(block)

    # Open questions (brief)
    questions = store.all_nodes(node_type="question", status="active", limit=3)
    if questions:
        lines.append("\n### Open questions")
        for q in questions:
            lines.append(f"- {q['title']}")

    # Recent decisions (brief)
    decisions = store.all_nodes(node_type="decision", limit=3)
    if decisions:
        lines.append("\n### Recent decisions")
        for d in decisions:
            when = d.get("prov_when", "")[:10]
            lines.append(f"- {when}: {d['title']}")

    # Active constraints and watches (brief)
    _append_operational(store, lines, verbose=False)

    return "\n".join(lines) + "\n"


# ── Summarized tier ───────────────────────────────────────────────────

def _format_summarized(store: Store, results: list[dict], query: str) -> str:
    """Summarized — paragraph-form narrative per domain cluster."""
    all_domains = _gather_domains(results)

    lines = [
        "## Kindex Context (summarized)",
        f"**Domains:** {', '.join(sorted(all_domains)[:6])}",
        "",
    ]

    # Group results by domain
    domain_groups: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        domains = r.get("domains") or ["general"]
        for d in domains[:1]:  # primary domain only
            domain_groups[d].append(r)

    for domain, nodes in domain_groups.items():
        titles = [n.get("title", n["id"]) for n in nodes[:5]]
        # Build a synthesized sentence about this cluster
        summaries = []
        for n in nodes[:3]:
            content = _strip_frontmatter(n.get("content") or "")[:150]
            if content:
                summaries.append(f"{n['title']}: {content}")

        lines.append(f"**{domain}:** {'; '.join(summaries)}")
        lines.append("")

    # Open questions as a single line
    questions = store.all_nodes(node_type="question", status="active", limit=2)
    if questions:
        q_titles = [q["title"] for q in questions]
        lines.append(f"**Open questions:** {'; '.join(q_titles)}")

    return "\n".join(lines) + "\n"


# ── Executive tier ────────────────────────────────────────────────────

def _format_executive(store: Store, results: list[dict], query: str) -> str:
    """Executive — 2-3 sentences per active thread. Minimum to orient."""
    all_domains = _gather_domains(results)
    domain_str = ", ".join(sorted(all_domains)[:4])

    # One sentence per top result
    summaries = []
    for r in results[:5]:
        title = r.get("title", r["id"])
        content = _strip_frontmatter(r.get("content") or "")[:80]
        if content:
            summaries.append(f"{title} — {content}")
        else:
            summaries.append(title)

    block = f"Kindex [{domain_str}]: {'. '.join(summaries)}."

    questions = store.all_nodes(node_type="question", status="active", limit=1)
    if questions:
        block += f" Open: {questions[0]['title']}"

    return block + "\n"


# ── Index tier ────────────────────────────────────────────────────────

def _format_index(store: Store, results: list[dict], query: str) -> str:
    """Index — node titles and edge types only. Just the map."""
    titles = []
    for r in results:
        title = r.get("title", r["id"])
        node_type = r.get("type", "concept")
        edges = r.get("edges_out", [])
        if edges:
            edge_types = set(e["type"] for e in edges[:3])
            titles.append(f"{title}({node_type})→[{','.join(edge_types)}]")
        else:
            titles.append(f"{title}({node_type})")
    return f"Kindex index: {' | '.join(titles)}\n"


_TIER_FORMATTERS = {
    "full": _format_full,
    "abridged": _format_abridged,
    "summarized": _format_summarized,
    "executive": _format_executive,
    "index": _format_index,
}


def generate_codebook(store: Store, min_weight: float = 0.5) -> tuple[str, str]:
    """Generate deterministic codebook of high-value nodes.

    Returns (text, sha256_hash). Sorted by node ID for prefix cache stability.
    Excludes session nodes. Includes: index, truncated ID, type, weight, domains, title.
    """
    import hashlib

    nodes = store.all_nodes(limit=5000)
    eligible = [n for n in nodes
                if n.get("type") != "session" and (n.get("weight") or 0) >= min_weight]
    eligible.sort(key=lambda n: n["id"])

    lines = []
    for i, n in enumerate(eligible, 1):
        domains = ",".join(n.get("domains") or [])[:40]
        title = (n.get("title") or n["id"])[:80]
        lines.append(
            f"#{i:03d} id:{n['id'][:8]} type:{n.get('type', 'concept')} "
            f"w:{n.get('weight', 0):.2f} domains:[{domains}] \"{title}\""
        )

    header = f"[CODEBOOK v1 | {len(eligible)} entries]"
    text = header + "\n" + "\n".join(lines)
    h = hashlib.sha256(text.encode()).hexdigest()[:16]
    return text, h


def build_codebook_index(codebook_text: str) -> dict[str, str]:
    """Parse codebook text into {truncated_id: entry_number} mapping."""
    index: dict[str, str] = {}
    for line in codebook_text.split("\n"):
        if not line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            entry_num = parts[0]  # e.g. "#042"
            for part in parts:
                if part.startswith("id:"):
                    index[part[3:]] = entry_num
                    break
    return index


def predict_tier2(
    store: Store,
    query: str,
    search_results: list[dict],
    top_k: int = 8,
) -> list[dict]:
    """Expand search results with graph-predicted neighbors.

    Uses 1-hop edges from top hits to predict related nodes the user
    might ask about next. Returns merged list sorted by node ID for
    deterministic prefix ordering.
    """
    hit_ids = {r["id"] for r in search_results}
    predicted: dict[str, dict] = {}

    for hit in search_results[:3]:
        for edge in store.edges_from(hit["id"])[:5]:
            tid = edge["to_id"]
            if tid not in hit_ids and tid not in predicted:
                node = store.get_node(tid)
                if node and node.get("type") != "session":
                    predicted[tid] = node

    merged = list(search_results[:top_k])
    for node in sorted(predicted.values(), key=lambda n: n["id"]):
        if len(merged) >= top_k:
            break
        merged.append(node)
    return merged


def format_tier2(
    results: list[dict],
    codebook_index: dict[str, str],
    max_tokens: int = 4000,
) -> str:
    """Format tier 2 context with codebook back-references.

    Results sorted by node ID for deterministic prefix ordering.
    Content trimmed to fit within max_tokens budget.
    """
    results_sorted = sorted(results, key=lambda r: r["id"])
    char_budget = max_tokens * 4
    lines: list[str] = ["## Relevant Context\n"]
    used = 0

    for r in results_sorted:
        entry = codebook_index.get(r["id"][:8], "?")
        title = r.get("title") or r["id"]
        content = _strip_frontmatter(r.get("content") or "")[:1000]
        edges = r.get("edges_out") or []

        block_lines = [f"### {entry} {title}"]
        if content:
            block_lines.append(content)
        if edges:
            refs = []
            for e in edges[:5]:
                t_entry = codebook_index.get(e["to_id"][:8], "?")
                refs.append(f"{t_entry} {e.get('to_title', e['to_id'])} (w={e['weight']:.1f})")
            block_lines.append(f"Connects: {', '.join(refs)}")
        block_lines.append("")

        block = "\n".join(block_lines)
        if used + len(block) > char_budget:
            break
        lines.append(block)
        used += len(block)

    return "\n".join(lines)


def detect_domain_from_path(store: Store, cwd: str) -> list[str]:
    """Given a working directory, find relevant domain nodes.

    Searches for nodes whose prov_source matches the path.
    """
    # Search for nodes referencing this path
    results = store.fts_search(cwd, limit=5)
    domains: set[str] = set()
    for r in results:
        for d in (r.get("domains") or []):
            domains.add(d)
    return sorted(domains)
