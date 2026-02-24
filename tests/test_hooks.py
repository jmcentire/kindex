"""Tests for hooks module — prime_context, capture_session_end, write_inbox_item, generate_session_directive."""

import datetime
import os
import sys

import pytest

from kindex.config import Config
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=str(tmp_path))


@pytest.fixture
def ledger(tmp_path):
    from kindex.budget import BudgetLedger
    cfg = Config(data_dir=str(tmp_path))
    return BudgetLedger(cfg.ledger_path, cfg.budget)


class TestPrimeContext:
    def test_prime_context_basic(self, store):
        """Creates nodes, calls prime_context, verifies output contains node titles."""
        from kindex.hooks import prime_context

        store.add_node("Stigmergy Coordination", content="Agents communicate through environment",
                        node_type="concept", node_id="stig")
        store.add_node("Graph Theory Basics", content="Study of nodes and edges",
                        node_type="concept", node_id="graph")
        store.add_edge("stig", "graph", provenance="test")

        output = prime_context(store, topic="stigmergy", max_tokens=750)

        assert "Kindex Context" in output
        # Should contain at least one of the node titles (found via FTS)
        assert "Stigmergy" in output or "Graph Theory" in output

    def test_prime_context_with_ops(self, store):
        """Creates constraints/watches, verifies they appear in output."""
        from kindex.hooks import prime_context

        # Add some searchable content first
        store.add_node("Test Domain Concept", content="Test domain content",
                        node_type="concept", node_id="test-concept")

        # Add operational nodes
        store.add_node("Never deploy on Friday", node_type="constraint",
                        node_id="c1", extra={"trigger": "pre-deploy", "action": "block"})
        store.add_node("Monitor API latency", node_type="watch",
                        node_id="w1", extra={"owner": "alice", "expires": "2026-12-31"})

        output = prime_context(store, topic="test", max_tokens=1500)

        # Operational nodes should appear
        assert "constraint" in output.lower() or "Friday" in output
        assert "watch" in output.lower() or "Monitor" in output or "latency" in output

    def test_prime_context_empty_store(self, store):
        """Prime context on empty store should not crash."""
        from kindex.hooks import prime_context

        output = prime_context(store, topic="anything")
        assert "Kindex Context" in output

    def test_prime_context_respects_token_limit(self, store):
        """Output should stay within approximate token budget."""
        from kindex.hooks import prime_context

        for i in range(20):
            store.add_node(f"Concept {i}", content=f"Detailed content about concept {i} " * 20,
                            node_type="concept")

        output = prime_context(store, topic="concept", max_tokens=200)
        # 200 tokens ~600 chars; output should be roughly in that range
        # Allow some overhead for headers
        assert len(output) < 3000  # generous upper bound for 200 token budget


class TestCaptureSessionEnd:
    def test_capture_session_end(self, store, config, ledger):
        """Provides session text, verifies nodes/edges are created."""
        from kindex.hooks import capture_session_end

        session_text = (
            "We learned that Graph Neural Networks can be applied to knowledge graphs. "
            "We decided to use PyTorch Geometric because it has good documentation. "
            "How does attention mechanism work in transformers? "
            "This is similar to the Self Attention Pattern we discussed earlier."
        )

        count = capture_session_end(store, config, ledger, session_text=session_text)

        # Should have created at least some nodes
        assert count >= 1

        # Check that nodes were actually created in the store
        nodes = store.all_nodes()
        assert len(nodes) >= 1

    def test_capture_session_end_empty(self, store, config, ledger):
        """Empty session text returns 0."""
        from kindex.hooks import capture_session_end

        count = capture_session_end(store, config, ledger, session_text="")
        assert count == 0

    def test_capture_session_end_too_short(self, store, config, ledger):
        """Very short text returns 0."""
        from kindex.hooks import capture_session_end

        count = capture_session_end(store, config, ledger, session_text="hello")
        assert count == 0

    def test_capture_session_end_with_existing_nodes(self, store, config, ledger):
        """Should link to existing nodes rather than duplicate."""
        from kindex.hooks import capture_session_end

        # Pre-populate store
        store.add_node("Graph Neural Networks", content="ML on graphs",
                        node_type="concept", node_id="gnn")

        session_text = (
            "We explored how Graph Neural Networks can improve knowledge graph completion. "
            "The Bridge Pattern connects two unrelated domains effectively. "
            "We decided to use a message passing approach because it handles heterogeneous graphs."
        )

        count = capture_session_end(store, config, ledger, session_text=session_text)

        # The pre-existing "Graph Neural Networks" should not be duplicated
        gnn_nodes = [n for n in store.all_nodes() if "graph neural" in n["title"].lower()]
        assert len(gnn_nodes) == 1  # should not create a duplicate


class TestWriteInboxItem:
    def test_write_inbox_item(self, config):
        """Writes an inbox item, verifies file exists with correct content."""
        from kindex.hooks import write_inbox_item

        path = write_inbox_item(
            config, content="This is a test inbox item.",
            source="test", topic_hint="testing"
        )

        assert path.exists()
        text = path.read_text()
        assert "This is a test inbox item." in text
        assert "source: test" in text
        assert "topic_hint: testing" in text
        assert "processed: false" in text

    def test_write_inbox_item_no_optional(self, config):
        """Inbox item without optional fields."""
        from kindex.hooks import write_inbox_item

        path = write_inbox_item(config, content="Minimal item.")

        assert path.exists()
        text = path.read_text()
        assert "Minimal item." in text
        assert "---" in text

    def test_write_inbox_item_unique_names(self, config):
        """Multiple items should have unique filenames."""
        from kindex.hooks import write_inbox_item

        path1 = write_inbox_item(config, content="Item 1")
        path2 = write_inbox_item(config, content="Item 2")

        assert path1 != path2
        assert path1.exists()
        assert path2.exists()


class TestGenerateSessionDirective:
    def test_generate_session_directive(self, store):
        """Verifies directive contains kin commands."""
        from kindex.hooks import generate_session_directive

        output = generate_session_directive(store)

        assert "kin add" in output
        assert "kin link" in output
        assert "Knowledge Capture" in output
        assert "concept" in output.lower()
        assert "decision" in output.lower()

    def test_generate_session_directive_with_nodes(self, store):
        """Directive shows graph stats when nodes exist."""
        from kindex.hooks import generate_session_directive

        store.add_node("Test Node A", node_id="a")
        store.add_node("Test Node B", node_id="b")
        store.add_edge("a", "b")

        output = generate_session_directive(store)

        # Should mention current graph stats
        assert "nodes" in output.lower() or "2 nodes" in output

    def test_generate_session_directive_with_suggestions(self, store):
        """Directive mentions pending suggestions if any exist."""
        from kindex.hooks import generate_session_directive

        store.add_node("Alpha", node_id="alpha")
        store.add_node("Beta", node_id="beta")
        store.add_edge("alpha", "beta")
        store.add_suggestion("Alpha", "Beta", reason="test bridge")

        output = generate_session_directive(store)

        assert "suggest" in output.lower()
