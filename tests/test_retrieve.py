"""Tests for hybrid retrieval."""

import pytest

from kindex.config import Config
from kindex.retrieve import format_context_block, hybrid_search
from kindex.store import Store


@pytest.fixture
def populated_store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    s.add_node("Stigmergy", content="Coordination through environmental traces",
               node_id="stig", domains=["systems", "coordination"], weight=1.0)
    s.add_node("Emergence Architecture", content="Stigmergic task coordination",
               node_id="emerge", domains=["systems", "engineering"], weight=0.9)
    s.add_node("Patent Filing", content="ASD mesh patent for organizational health",
               node_id="patent", domains=["ip", "research"], weight=1.0)
    s.add_node("Database Design", content="Schema normalization and indexes",
               node_id="db", domains=["engineering"], weight=0.5)

    s.add_edge("stig", "emerge", weight=0.9, provenance="same principles")
    s.add_edge("patent", "stig", weight=1.0, provenance="ASD uses stigmergy")
    s.add_edge("patent", "emerge", weight=0.8, provenance="both coordination")

    yield s
    s.close()


class TestHybridSearch:
    def test_finds_by_keyword(self, populated_store):
        results = hybrid_search(populated_store, "stigmergy")
        assert len(results) >= 1
        ids = [r["id"] for r in results]
        assert "stig" in ids

    def test_graph_expansion(self, populated_store):
        results = hybrid_search(populated_store, "stigmergy", expand_graph=True)
        ids = [r["id"] for r in results]
        # Should find emerge via graph edge from stig
        assert "emerge" in ids or "patent" in ids

    def test_no_results(self, populated_store):
        results = hybrid_search(populated_store, "zzzz nothing")
        assert results == []


class TestContextBlock:
    def test_format(self, populated_store):
        results = hybrid_search(populated_store, "stigmergy", top_k=3)
        block = format_context_block(populated_store, results, query="stigmergy")
        assert "Kindex" in block
        assert "Stigmergy" in block
        assert "Active domains:" in block

    def test_empty(self, populated_store):
        block = format_context_block(populated_store, [], query="nothing")
        assert "No relevant context" in block
