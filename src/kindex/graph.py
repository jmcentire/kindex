"""Graph algorithms — BFS, PPR, centrality, search."""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from .vault import Vault


@dataclass
class TraversalResult:
    slug: str
    depth: int
    reason: str | None
    edge_weight: float
    cumulative_weight: float


@dataclass
class GraphStats:
    node_count: int = 0
    edge_count: int = 0
    density: float = 0.0
    components: int = 0
    avg_degree: float = 0.0
    max_degree_node: str = ""
    max_degree: int = 0


def build_nx(vault: Vault) -> nx.DiGraph:
    """Build a NetworkX directed graph from all vault nodes."""
    G = nx.DiGraph()

    for slug, node in vault.topics.items():
        G.add_node(slug, kind="topic", title=node.title,
                   weight=node.weight, domains=node.domains)

    for slug, node in vault.skills.items():
        G.add_node(slug, kind="skill", title=node.title,
                   level=node.level, domains=node.domains)

    for slug in list(vault.topics) + list(vault.skills):
        for edge in vault.edges_from(slug):
            if edge.target in vault.topics or edge.target in vault.skills:
                G.add_edge(slug, edge.target, weight=edge.weight, reason=edge.reason)

    return G


def weighted_bfs(vault: Vault, start: str, max_hops: int = 2,
                 min_weight: float = 0.1) -> list[TraversalResult]:
    """BFS with cumulative weight decay (product of edge weights along path)."""
    if start not in vault.topics and start not in vault.skills:
        return []

    visited = {start}
    result = [TraversalResult(start, 0, None, 1.0, 1.0)]
    frontier = [(start, 0, 1.0)]

    while frontier:
        current, depth, cum = frontier.pop(0)
        if depth >= max_hops:
            continue

        node = vault.get(current)
        if node is None:
            continue

        edges = sorted(node.connects_to, key=lambda e: e.weight, reverse=True)
        for edge in edges:
            if edge.target in visited:
                continue
            if vault.get(edge.target) is None:
                continue

            new_cum = cum * edge.weight
            if new_cum < min_weight:
                continue

            visited.add(edge.target)
            result.append(TraversalResult(
                edge.target, depth + 1, edge.reason, edge.weight, new_cum))
            frontier.append((edge.target, depth + 1, new_cum))

    return result


def text_search(vault: Vault, query: str, top_k: int = 10) -> list[tuple[str, float]]:
    """Simple keyword search across titles, bodies, tags, and domains.

    Returns (slug, score) sorted by score descending. No LLM needed.
    """
    query_terms = query.lower().split()
    scores: dict[str, float] = {}

    for slug in vault.all_slugs():
        node = vault.get(slug)
        if node is None:
            continue

        score = 0.0
        title = (node.title or "").lower()
        body = (node.body or "").lower()
        domains = " ".join(getattr(node, "domains", [])).lower()
        tags = " ".join(getattr(node, "tags", [])).lower()
        slug_text = slug.replace("-", " ").lower()

        for term in query_terms:
            if term in slug_text:
                score += 3.0  # slug match is strong signal
            if term in title:
                score += 2.0
            if term in domains or term in tags:
                score += 1.5
            if term in body:
                score += 0.5
                # Bonus for frequency
                score += min(body.count(term) * 0.1, 1.0)

        if score > 0:
            # Boost by node weight
            node_weight = getattr(node, "weight", 0.5) or 0.5
            scores[slug] = score * (0.5 + 0.5 * node_weight)

    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]


def ppr(vault: Vault, start: str, top_k: int = 10,
        alpha: float = 0.85) -> list[tuple[str, float]]:
    """Personalized PageRank seeded from a node."""
    G = build_nx(vault)
    if start not in G:
        return []

    personalization = {n: 0.0 for n in G.nodes()}
    personalization[start] = 1.0
    scores = nx.pagerank(G, alpha=alpha, personalization=personalization, weight="weight")
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]


def graph_stats(vault: Vault) -> GraphStats:
    G = build_nx(vault)
    if not G:
        return GraphStats()
    degrees = dict(G.degree())
    mx = max(degrees, key=degrees.get) if degrees else ""
    return GraphStats(
        node_count=G.number_of_nodes(),
        edge_count=G.number_of_edges(),
        density=nx.density(G),
        components=nx.number_weakly_connected_components(G),
        avg_degree=sum(degrees.values()) / len(degrees) if degrees else 0,
        max_degree_node=mx,
        max_degree=degrees.get(mx, 0),
    )


def centrality(vault: Vault, method: str = "betweenness") -> list[tuple[str, float]]:
    G = build_nx(vault)
    if not G:
        return []
    fn = {
        "betweenness": lambda: nx.betweenness_centrality(G, weight="weight"),
        "degree": lambda: nx.degree_centrality(G),
        "closeness": lambda: nx.closeness_centrality(G),
    }
    scores = fn.get(method, fn["betweenness"])()
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


def communities(vault: Vault) -> list[set[str]]:
    G = build_nx(vault)
    if not G:
        return []
    U = G.to_undirected()
    return list(nx.community.greedy_modularity_communities(U, weight="weight"))
