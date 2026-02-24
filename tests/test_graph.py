"""Tests for graph algorithms."""

from kindex.graph import (
    build_nx, centrality, communities, graph_stats,
    ppr, text_search, weighted_bfs,
)


class TestWeightedBFS:
    def test_basic(self, sample_vault):
        results = weighted_bfs(sample_vault, "alpha", max_hops=1, min_weight=0.0)
        slugs = [r.slug for r in results]
        assert "alpha" == slugs[0]
        assert "beta" in slugs

    def test_cumulative_decay(self, sample_vault):
        results = weighted_bfs(sample_vault, "alpha", max_hops=2, min_weight=0.0)
        by_slug = {r.slug: r for r in results}
        assert by_slug["beta"].cumulative_weight == 0.9

    def test_min_weight_filter(self, sample_vault):
        results = weighted_bfs(sample_vault, "alpha", max_hops=1, min_weight=0.8)
        slugs = [r.slug for r in results]
        assert "beta" in slugs
        assert "delta" not in slugs

    def test_no_cycles(self, sample_vault):
        results = weighted_bfs(sample_vault, "alpha", max_hops=10, min_weight=0.0)
        slugs = [r.slug for r in results]
        assert len(slugs) == len(set(slugs))

    def test_missing(self, sample_vault):
        assert weighted_bfs(sample_vault, "nope") == []

    def test_depth_0(self, sample_vault):
        results = weighted_bfs(sample_vault, "alpha", max_hops=0)
        assert len(results) == 1


class TestTextSearch:
    def test_finds_by_slug(self, sample_vault):
        results = text_search(sample_vault, "alpha")
        slugs = [s for s, _ in results]
        assert "alpha" in slugs

    def test_finds_by_title(self, sample_vault):
        results = text_search(sample_vault, "Python Development")
        slugs = [s for s, _ in results]
        assert "python" in slugs

    def test_no_results(self, sample_vault):
        results = text_search(sample_vault, "zzzznonexistent")
        assert results == []


class TestPPR:
    def test_basic(self, sample_vault):
        results = ppr(sample_vault, "alpha")
        assert results[0][0] == "alpha"

    def test_missing(self, sample_vault):
        assert ppr(sample_vault, "nope") == []


class TestGraphStats:
    def test_counts(self, sample_vault):
        stats = graph_stats(sample_vault)
        assert stats.node_count == 5  # 4 topics + 1 skill
        assert stats.edge_count > 0


class TestCentrality:
    def test_betweenness(self, sample_vault):
        ranked = centrality(sample_vault)
        assert len(ranked) == 5


class TestCommunities:
    def test_finds_groups(self, sample_vault):
        groups = communities(sample_vault)
        assert len(groups) >= 1
        all_nodes = set()
        for g in groups:
            all_nodes.update(g)
        assert "alpha" in all_nodes


class TestBuildNX:
    def test_includes_skills(self, sample_vault):
        G = build_nx(sample_vault)
        assert G.nodes["python"]["kind"] == "skill"
        assert G.nodes["alpha"]["kind"] == "topic"
