"""Tests for activity logging and audit trail."""

import subprocess
import sys

import pytest

from kindex.config import Config
from kindex.store import Store


def run(*args, data_dir=None):
    cmd = [sys.executable, "-m", "kindex.cli", *args]
    if data_dir:
        cmd.extend(["--data-dir", data_dir])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


class TestActivityLog:
    def test_add_node_logged(self, store):
        store.add_node("Test concept", node_id="test1", prov_who=["jeremy"])
        entries = store.recent_activity(limit=5)
        assert len(entries) >= 1
        assert entries[0]["action"] == "add_node"
        assert entries[0]["target_id"] == "test1"
        assert entries[0]["target_title"] == "Test concept"

    def test_update_node_logged(self, store):
        store.add_node("Original", node_id="orig")
        store.update_node("orig", title="Updated")
        entries = store.recent_activity(limit=5)
        update_entries = [e for e in entries if e["action"] == "update_node"]
        assert len(update_entries) >= 1
        assert "title" in update_entries[0]["details"]["fields"]

    def test_delete_node_logged(self, store):
        store.add_node("To delete", node_id="del")
        store.delete_node("del")
        entries = store.recent_activity(limit=5)
        delete_entries = [e for e in entries if e["action"] == "delete_node"]
        assert len(delete_entries) >= 1
        assert delete_entries[0]["target_title"] == "To delete"

    def test_add_edge_logged(self, store):
        store.add_node("A", node_id="a")
        store.add_node("B", node_id="b")
        store.add_edge("a", "b", edge_type="relates_to", weight=0.8)
        entries = store.recent_activity(limit=10)
        edge_entries = [e for e in entries if e["action"] == "add_edge"]
        assert len(edge_entries) >= 1
        assert "a->b" in edge_entries[0]["target_id"]

    def test_log_limit(self, store):
        for i in range(10):
            store.add_node(f"Node {i}", node_id=f"n{i}")
        entries = store.recent_activity(limit=3)
        assert len(entries) == 3

    def test_log_empty(self, store):
        entries = store.recent_activity()
        # May have entries from fixture creation, but shouldn't crash
        assert isinstance(entries, list)


class TestLogCLI:
    def test_log_command(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        run("add", "Test knowledge", data_dir=d)
        r = run("log", data_dir=d)
        assert r.returncode == 0
        assert "add_node" in r.stdout

    def test_log_json(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        run("add", "Something interesting", data_dir=d)
        r = run("log", "--json", data_dir=d)
        assert r.returncode == 0
        import json
        data = json.loads(r.stdout)
        assert isinstance(data, list)
        assert len(data) > 0

    def test_log_empty(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        r = run("log", data_dir=d)
        assert r.returncode == 0
