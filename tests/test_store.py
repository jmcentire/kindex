"""Tests for SQLite store."""

import pytest

from kindex.config import Config
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


class TestNodeOperations:
    def test_add_and_get(self, store):
        nid = store.add_node("Test Topic", content="Some content", node_type="concept")
        node = store.get_node(nid)
        assert node["title"] == "Test Topic"
        assert node["content"] == "Some content"
        assert node["type"] == "concept"

    def test_add_with_domains(self, store):
        nid = store.add_node("D", domains=["eng", "research"])
        node = store.get_node(nid)
        assert node["domains"] == ["eng", "research"]

    def test_get_by_title(self, store):
        store.add_node("Unique Title", node_id="ut1")
        node = store.get_node_by_title("Unique Title")
        assert node["id"] == "ut1"
        assert store.get_node_by_title("unique title") is not None  # case insensitive

    def test_update_node(self, store):
        nid = store.add_node("Original")
        store.update_node(nid, title="Updated", weight=0.9)
        node = store.get_node(nid)
        assert node["title"] == "Updated"
        assert node["weight"] == 0.9

    def test_delete_node(self, store):
        nid = store.add_node("Doomed")
        store.delete_node(nid)
        assert store.get_node(nid) is None

    def test_all_nodes(self, store):
        store.add_node("A", node_type="concept")
        store.add_node("B", node_type="skill")
        store.add_node("C", node_type="concept")
        assert len(store.all_nodes()) == 3
        assert len(store.all_nodes(node_type="concept")) == 2

    def test_recent_nodes(self, store):
        store.add_node("Old")
        store.add_node("New")
        recent = store.recent_nodes(n=1)
        assert len(recent) == 1
        assert recent[0]["title"] == "New"

    def test_node_ids(self, store):
        store.add_node("A", node_id="a1")
        store.add_node("B", node_id="b2")
        ids = store.node_ids()
        assert "a1" in ids
        assert "b2" in ids


class TestEdgeOperations:
    def test_add_edge_bidirectional(self, store):
        store.add_node("A", node_id="a")
        store.add_node("B", node_id="b")
        store.add_edge("a", "b", provenance="test")
        assert len(store.edges_from("a")) == 1
        assert len(store.edges_to("a")) == 1  # bidirectional creates reverse

    def test_edges_from(self, store):
        store.add_node("X", node_id="x")
        store.add_node("Y", node_id="y")
        store.add_edge("x", "y", edge_type="implements", weight=0.9)
        edges = store.edges_from("x")
        assert edges[0]["to_id"] == "y"
        assert edges[0]["type"] == "implements"
        assert edges[0]["weight"] == 0.9

    def test_orphans(self, store):
        store.add_node("Lonely", node_id="lonely")
        store.add_node("Connected", node_id="conn")
        store.add_node("Also Connected", node_id="also")
        store.add_edge("conn", "also")
        orphans = store.orphans()
        assert len(orphans) == 1
        assert orphans[0]["id"] == "lonely"


class TestFTS:
    def test_fts_search(self, store):
        store.add_node("Stigmergy Coordination", content="Agents communicate indirectly",
                        node_id="stig")
        store.add_node("Database Design", content="Schema normalization", node_id="db")
        results = store.fts_search("stigmergy")
        assert len(results) >= 1
        assert results[0]["id"] == "stig"

    def test_fts_no_results(self, store):
        store.add_node("Something", content="content")
        results = store.fts_search("zzzznonexistent")
        assert results == []


class TestStats:
    def test_stats(self, store):
        store.add_node("A", node_id="a")
        store.add_node("B", node_id="b")
        store.add_edge("a", "b")
        s = store.stats()
        assert s["nodes"] == 2
        assert s["edges"] >= 1
