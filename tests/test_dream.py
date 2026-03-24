"""Tests for dream module — knowledge consolidation."""

import datetime
import json
import os

import pytest

from kindex.config import Config
from kindex.store import Store


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=str(tmp_path), claude_dir=str(tmp_path / "claude"))


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


# ── Similarity functions ──────────────────────────────────────────────


class TestSimilarity:
    def test_title_similarity_identical(self):
        from kindex.dream import title_similarity
        assert title_similarity("hello world", "hello world") == 1.0

    def test_title_similarity_case_insensitive(self):
        from kindex.dream import title_similarity
        assert title_similarity("Hello World", "hello world") == 1.0

    def test_title_similarity_different(self):
        from kindex.dream import title_similarity
        score = title_similarity("alpha beta", "gamma delta")
        assert score < 0.8

    def test_title_similarity_close(self):
        from kindex.dream import title_similarity
        score = title_similarity("Pact testing patterns", "Pact testing pattern")
        assert score > 0.9

    def test_title_similarity_empty(self):
        from kindex.dream import title_similarity
        assert title_similarity("", "hello") == 0.0
        assert title_similarity("hello", "") == 0.0

    def test_content_overlap_identical(self):
        from kindex.dream import content_overlap
        assert content_overlap("some content here", "some content here") == 1.0

    def test_content_overlap_empty(self):
        from kindex.dream import content_overlap
        assert content_overlap("", "hello") == 0.0

    def test_combined_similarity(self):
        from kindex.dream import combined_similarity
        a = {"title": "Pact testing", "content": "Test patterns for Pact"}
        b = {"title": "Pact testing", "content": "Test patterns for Pact"}
        assert combined_similarity(a, b) == 1.0


# ── Find duplicates ──────────────────────────────────────────────────


class TestFindDuplicates:
    def test_finds_near_duplicate_titles(self, store):
        from kindex.dream import find_duplicates
        # Use distinct IDs but very similar titles/content to trigger dedup
        store.add_node("Pact testing patterns overview", node_id="dup-a",
                       content="Patterns for testing with Pact framework")
        store.add_node("Pact testing patterns overview guide", node_id="dup-b",
                       content="Patterns for testing with Pact framework details")

        result = find_duplicates(store)
        assert len(result["merge"]) > 0 or len(result["suggest"]) > 0

    def test_no_duplicates_in_different_nodes(self, store):
        from kindex.dream import find_duplicates
        store.add_node("Alpha concept", content="About alpha")
        store.add_node("Zeta concept", content="About zeta")

        result = find_duplicates(store)
        assert len(result["merge"]) == 0

    def test_skips_protected_types(self, store):
        from kindex.dream import find_duplicates
        store.add_node("Security rule A", content="Rule A", node_type="constraint")
        store.add_node("Security rule A", content="Rule A copy", node_type="constraint")

        result = find_duplicates(store)
        assert len(result["merge"]) == 0
        assert len(result["suggest"]) == 0

    def test_skips_short_titles(self, store):
        from kindex.dream import find_duplicates
        store.add_node("ABC", node_id="short-a", content="Short title")
        store.add_node("ABC", node_id="short-b", content="Another short")

        result = find_duplicates(store)
        # Titles < 4 chars are excluded from bucketing
        assert len(result["merge"]) == 0


# ── Merge nodes ──────────────────────────────────────────────────────


class TestMergeNodes:
    def test_merge_moves_edges(self, store):
        from kindex.dream import merge_nodes
        a = store.add_node("Source node", content="Source content")
        b = store.add_node("Target node", content="Target content")
        c = store.add_node("Connected node", content="Third")
        store.add_edge(a, c, edge_type="relates_to")

        merge_nodes(store, a, b)

        # Source should be archived
        source = store.get_node(a)
        assert source["status"] == "archived"

        # Target should now have edge to c
        edges = store.edges_from(b)
        to_ids = {e["to_id"] for e in edges}
        assert c in to_ids

    def test_merge_archives_source(self, store):
        from kindex.dream import merge_nodes
        a = store.add_node("Source", content="S")
        b = store.add_node("Target", content="T")

        merge_nodes(store, a, b)

        source = store.get_node(a)
        assert source["status"] == "archived"
        assert source["weight"] == 0.01

    def test_merge_combines_content(self, store):
        from kindex.dream import merge_nodes
        a = store.add_node("Source", content="Unique source info")
        b = store.add_node("Target", content="Target info")

        merge_nodes(store, a, b)

        target = store.get_node(b)
        assert "Unique source info" in target["content"]
        assert "Target info" in target["content"]

    def test_merge_boosts_weight(self, store):
        from kindex.dream import merge_nodes
        a = store.add_node("Source", content="S", weight=0.9)
        b = store.add_node("Target", content="T", weight=0.3)

        merge_nodes(store, a, b)

        target = store.get_node(b)
        assert target["weight"] >= 0.9

    def test_merge_nonexistent_returns_false(self, store):
        from kindex.dream import merge_nodes
        b = store.add_node("Target", content="T")
        assert merge_nodes(store, "nonexistent", b) is False


# ── Auto-apply suggestions ───────────────────────────────────────────


class TestAutoApplySuggestions:
    def test_applies_when_titles_similar(self, store):
        from kindex.dream import auto_apply_suggestions
        a = store.add_node("Kindex architecture overview")
        b = store.add_node("Kindex architecture overview details")
        store.add_suggestion(a, b, reason="test", source="test")

        count = auto_apply_suggestions(store)
        assert count >= 1

        # Edge should exist
        edges = store.edges_from(a)
        to_ids = {e["to_id"] for e in edges}
        assert b in to_ids

    def test_skips_when_titles_dissimilar(self, store):
        from kindex.dream import auto_apply_suggestions
        a = store.add_node("Alpha concept")
        b = store.add_node("Zeta completely different")
        store.add_suggestion(a, b, reason="test", source="test")

        count = auto_apply_suggestions(store)
        assert count == 0

    def test_skips_archived_nodes(self, store):
        from kindex.dream import auto_apply_suggestions
        a = store.add_node("Same title", status="archived")
        b = store.add_node("Same title nearby")
        store.add_suggestion(a, b, reason="test", source="test")

        count = auto_apply_suggestions(store)
        assert count == 0


# ── Dream cycles ─────────────────────────────────────────────────────


class TestDreamLightweight:
    def test_runs_on_empty_store(self, config, store):
        from kindex.dream import dream_lightweight
        results = dream_lightweight(config, store)
        assert results["merged"] == 0
        assert results["suggested"] == 0
        assert results["suggestions_applied"] == 0

    def test_dry_run_no_changes(self, config, store):
        from kindex.dream import dream_lightweight
        store.add_node("Duplicate concept here", content="Content A")
        store.add_node("Duplicate concept here", content="Content B")

        results = dream_lightweight(config, store, dry_run=True)
        # Dry run should count but not actually merge
        # Both nodes should still be active
        nodes = store.all_nodes(status="active")
        titles = [n["title"] for n in nodes]
        assert titles.count("Duplicate concept here") == 2


class TestDreamFull:
    def test_runs_on_empty_store(self, config, store):
        from kindex.dream import dream_full
        results = dream_full(config, store)
        assert results["merged"] == 0
        assert results["edges_strengthened"] == 0


class TestDreamCycle:
    def test_cycle_sets_meta(self, tmp_path):
        from kindex.dream import dream_cycle
        cfg = Config(data_dir=str(tmp_path))
        s = Store(cfg)

        results = dream_cycle(cfg, s, mode="lightweight")
        assert results["mode"] == "lightweight"
        assert "timestamp" in results

        last = s.get_meta("last_dream_run")
        assert last is not None

        s.close()

    def test_cycle_skips_when_locked(self, tmp_path):
        """Simulate lock contention."""
        import fcntl

        cfg = Config(data_dir=str(tmp_path))
        s = Store(cfg)

        # Acquire lock externally
        lock_file = cfg.data_path / "dream.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

        from kindex.dream import dream_cycle
        results = dream_cycle(cfg, s, mode="lightweight")
        assert results.get("skipped") == "locked"

        # Cleanup
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        s.close()


class TestDreamIdempotent:
    """CD004: Running dream N times produces same result as running once."""

    def test_double_merge_is_idempotent(self, tmp_path):
        from kindex.dream import dream_cycle
        cfg = Config(data_dir=str(tmp_path))
        s = Store(cfg)

        s.add_node("Identical concept", content="Same stuff")
        s.add_node("Identical concept", content="Same stuff too")

        r1 = dream_cycle(cfg, s, mode="lightweight")
        r2 = dream_cycle(cfg, s, mode="lightweight")

        # Second run should find nothing to merge (first was already archived)
        assert r2.get("merged", 0) == 0
        s.close()


class TestProtectedTypes:
    """CD008: Dream must never modify constraint/directive/checkpoint nodes."""

    def test_constraints_not_merged(self, config, store):
        from kindex.dream import find_duplicates
        store.add_node("Rule A", content="Same rule", node_type="constraint")
        store.add_node("Rule A", content="Same rule", node_type="constraint")

        result = find_duplicates(store)
        assert len(result["merge"]) == 0

    def test_directives_not_merged(self, config, store):
        from kindex.dream import find_duplicates
        store.add_node("Directive X", content="Same", node_type="directive")
        store.add_node("Directive X", content="Same", node_type="directive")

        result = find_duplicates(store)
        assert len(result["merge"]) == 0

    def test_checkpoints_not_merged(self, config, store):
        from kindex.dream import find_duplicates
        store.add_node("Checkpoint Z", content="Same", node_type="checkpoint")
        store.add_node("Checkpoint Z", content="Same", node_type="checkpoint")

        result = find_duplicates(store)
        assert len(result["merge"]) == 0


# ── Domain edge strengthening ────────────────────────────────────────


class TestDomainEdges:
    def test_creates_edges_for_shared_domain(self, store):
        from kindex.dream import strengthen_domain_edges
        store.add_node("Concept A", domains=["security"])
        store.add_node("Concept B", domains=["security"])

        created = strengthen_domain_edges(store)
        assert created >= 1

    def test_dry_run_counts_without_creating(self, store):
        from kindex.dream import strengthen_domain_edges
        store.add_node("Concept A", domains=["security"])
        store.add_node("Concept B", domains=["security"])

        created = strengthen_domain_edges(store, dry_run=True)
        assert created >= 1

        # No actual edges should exist
        nodes = store.all_nodes(status="active")
        for n in nodes:
            edges = store.edges_from(n["id"])
            # Filter to only dream-created edges
            dream_edges = [e for e in edges if "dream" in (e.get("provenance") or "")]
            assert len(dream_edges) == 0

    def test_skips_already_linked(self, store):
        from kindex.dream import strengthen_domain_edges
        a = store.add_node("Concept A", domains=["security"])
        b = store.add_node("Concept B", domains=["security"])
        store.add_edge(a, b, edge_type="relates_to")

        created = strengthen_domain_edges(store)
        assert created == 0
