"""Tests for AKA/synonym resolution."""

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


class TestAKAStore:
    def test_get_node_by_alias(self, store):
        store.add_node("Stigmergy", node_id="stig", aka=["environmental coordination", "indirect collaboration"])
        # Should find by exact title
        node = store.get_node_by_title("Stigmergy")
        assert node is not None
        assert node["id"] == "stig"

        # Should find by alias
        node2 = store.get_node_by_title("environmental coordination")
        assert node2 is not None
        assert node2["id"] == "stig"

    def test_alias_case_insensitive(self, store):
        store.add_node("Python", node_id="py", aka=["python3", "CPython"])
        node = store.get_node_by_title("cpython")
        assert node is not None
        assert node["id"] == "py"

    def test_no_alias_returns_none(self, store):
        store.add_node("Go", node_id="go")
        assert store.get_node_by_title("golang") is None

    def test_fts_searches_aka(self, store):
        store.add_node("ASD Patent", node_id="asd",
                       content="Patent filing",
                       aka=["Adaptive Sound Design", "sound patent"])
        results = store.fts_search("Adaptive Sound Design")
        assert any(r["id"] == "asd" for r in results)


class TestAliasCLI:
    def test_alias_add(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        run("add", "Stigmergy is coordination", data_dir=d)
        # Get node ID
        r = run("list", data_dir=d)
        nid = r.stdout.strip().split()[-1]
        # Add alias
        r2 = run("alias", nid, "add", "environmental coordination", data_dir=d)
        assert r2.returncode == 0
        assert "Added alias" in r2.stdout

    def test_alias_list(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        run("add", "Stigmergy is coordination", data_dir=d)
        r = run("list", data_dir=d)
        nid = r.stdout.strip().split()[-1]
        run("alias", nid, "add", "swarm intelligence", data_dir=d)
        r2 = run("alias", nid, "list", data_dir=d)
        assert r2.returncode == 0
        assert "swarm intelligence" in r2.stdout

    def test_alias_remove(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        run("add", "Python is great", data_dir=d)
        r = run("list", data_dir=d)
        nid = r.stdout.strip().split()[-1]
        run("alias", nid, "add", "py3", data_dir=d)
        r2 = run("alias", nid, "remove", "py3", data_dir=d)
        assert r2.returncode == 0
        assert "Removed alias" in r2.stdout


class TestWhoami:
    def test_whoami(self):
        r = run("whoami")
        assert r.returncode == 0
        assert len(r.stdout.strip()) > 0
