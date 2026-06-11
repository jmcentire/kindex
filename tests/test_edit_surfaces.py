"""CLI surfaces for edit/supersede (subprocess matrix) + changelog diff rendering."""

import os
import subprocess
import sys

import pytest

from kindex.config import Config
from kindex.store import Store

PAST = "2020-01-01"
FUTURE = "2099-01-01"


def _run_cli(args, data_dir, home, cwd=None):
    env = dict(os.environ)
    env.pop("KIN_PROFILE", None)
    env["KIN_AGENT_ID"] = "cli-test-agent"
    env["HOME"] = str(home)
    return subprocess.run(
        [sys.executable, "-m", "kindex.cli", *args, "--data-dir", str(data_dir)],
        capture_output=True, text=True, timeout=60, env=env, cwd=str(cwd or home),
    )


@pytest.fixture
def cli_graph(tmp_path):
    """Isolated HOME + data dir seeded with one node per edit class."""
    home = tmp_path / "home"
    (home / ".config" / "kindex").mkdir(parents=True)
    data = tmp_path / "data"
    data.mkdir()

    store = Store(Config(data_dir=str(data)))
    ids = {
        "concept": store.add_node("Widget pattern", content="Original content",
                                  node_type="concept", domains=["arch"]),
        "decision": store.add_node("Use SQLite", content="Because it is simple",
                                   node_type="decision"),
        "task": store.add_node("Do the thing", content="task body",
                               node_type="task", extra={"task_status": "open"}),
    }
    store.close()
    return home, data, ids


def _reopen(data):
    return Store(Config(data_dir=str(data)))


# ── kin edit: policy matrix ────────────────────────────────────────────


class TestEditCLI:
    def test_editable_content(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["concept"], "--content", "Rewritten content"],
                     data, home)
        assert r.returncode == 0, r.stderr
        assert "Edited" in r.stdout
        store = _reopen(data)
        assert store.get_node(ids["concept"])["content"] == "Rewritten content"
        store.close()

    def test_editable_by_title(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["edit", "Widget pattern", "--title", "Gadget pattern"],
                     data, home)
        assert r.returncode == 0, r.stderr
        store = _reopen(data)
        assert store.get_node(ids["concept"])["title"] == "Gadget pattern"
        store.close()

    def test_editable_tags_and_expires(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["concept"], "--add-tags", "alpha,beta",
                      "--remove-tags", "arch", "--expires", FUTURE], data, home)
        assert r.returncode == 0, r.stderr
        store = _reopen(data)
        node = store.get_node(ids["concept"])
        assert sorted(node["domains"]) == ["alpha", "beta"]
        assert node["extra"]["expires"] == FUTURE
        store.close()

    def test_additive_append_allowed(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["decision"], "--append", "Revisited in Q3"],
                     data, home)
        assert r.returncode == 0, r.stderr
        store = _reopen(data)
        content = store.get_node(ids["decision"])["content"]
        assert "Because it is simple" in content
        assert "[addendum" in content
        assert "cli-test-agent" in content
        assert "Revisited in Q3" in content
        store.close()

    def test_additive_content_refused(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["decision"], "--content", "rewrite history"],
                     data, home)
        assert r.returncode == 1
        assert "additive" in r.stderr
        assert "append" in r.stderr  # names the allowed verbs
        store = _reopen(data)
        assert store.get_node(ids["decision"])["content"] == "Because it is simple"
        store.close()

    def test_managed_refused(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["task"], "--content", "nope"], data, home)
        assert r.returncode == 1
        assert "managed" in r.stderr

    def test_noop_exits_2(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["concept"]], data, home)
        assert r.returncode == 2
        assert "at least one field" in r.stderr

    def test_not_found_exits_1(self, cli_graph):
        home, data, _ = cli_graph
        r = _run_cli(["edit", "no-such-node-xyz", "--content", "x"], data, home)
        assert r.returncode == 1
        assert "not found" in r.stderr

    def test_bad_expires_exits_1(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["concept"], "--expires", "soonish"], data, home)
        assert r.returncode == 1
        assert "YYYY-MM-DD" in r.stderr

    def test_locked_node_refused_then_forced(self, cli_graph):
        home, data, ids = cli_graph
        store = _reopen(data)
        from kindex.locks import lock_node
        lock_node(store, ids["concept"], "someone-else", ttl_minutes=60)
        store.close()

        r = _run_cli(["edit", ids["concept"], "--content", "blocked"], data, home)
        assert r.returncode == 1
        assert "locked" in r.stderr

        r = _run_cli(["edit", ids["concept"], "--content", "forced through",
                      "--force"], data, home)
        assert r.returncode == 0, r.stderr
        store = _reopen(data)
        assert store.get_node(ids["concept"])["content"] == "forced through"
        store.close()


# ── kin supersede ──────────────────────────────────────────────────────


class TestSupersedeCLI:
    def test_supersede_prints_new_id(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["supersede", ids["decision"], "Use Postgres", "instead",
                      "--reason", "outgrew sqlite", "--expires", FUTURE],
                     data, home)
        assert r.returncode == 0, r.stderr
        assert "Superseded" in r.stdout
        new_id = r.stdout.strip().rsplit("-> ", 1)[1]

        store = _reopen(data)
        old = store.get_node(ids["decision"])
        new = store.get_node(new_id)
        assert old["status"] == "superseded"
        assert old["extra"]["superseded_by"] == new_id
        assert new["status"] == "active"
        assert new["type"] == "decision"
        assert "Use Postgres instead" in new["content"]
        assert new["extra"]["expires"] == FUTURE
        assert new["extra"]["supersede_reason"] == "outgrew sqlite"
        store.close()

    def test_supersede_by_title(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["supersede", "Use SQLite", "Use DuckDB for analytics"],
                     data, home)
        assert r.returncode == 0, r.stderr
        store = _reopen(data)
        assert store.get_node(ids["decision"])["status"] == "superseded"
        store.close()

    def test_supersede_not_found(self, cli_graph):
        home, data, _ = cli_graph
        r = _run_cli(["supersede", "no-such-node-xyz", "whatever"], data, home)
        assert r.returncode == 1
        assert "not found" in r.stderr

    def test_supersede_managed_refused(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["supersede", ids["task"], "replacement"], data, home)
        assert r.returncode == 1
        assert "managed" in r.stderr
        store = _reopen(data)
        assert store.get_node(ids["task"])["status"] == "active"
        store.close()

    def test_double_supersede_refused_names_successor(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["supersede", ids["decision"], "Use Postgres"], data, home)
        assert r.returncode == 0, r.stderr
        new_id = r.stdout.strip().rsplit("-> ", 1)[1]

        r2 = _run_cli(["supersede", ids["decision"], "Use MySQL"], data, home)
        assert r2.returncode == 1
        assert new_id in r2.stderr  # error names the successor
        store = _reopen(data)
        assert store.get_node(ids["decision"])["extra"]["superseded_by"] == new_id
        store.close()


# ── kin changelog: diff rendering ──────────────────────────────────────


class TestChangelogDiffCLI:
    def test_changelog_renders_edit_diffs(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["concept"], "--content", "Brand new body"],
                     data, home)
        assert r.returncode == 0, r.stderr

        r = _run_cli(["changelog", "--days", "1"], data, home)
        assert r.returncode == 0, r.stderr
        assert "## Edited" in r.stdout
        assert "content:" in r.stdout
        assert "Original content -> Brand new body" in r.stdout

    def test_changelog_groups_supersede(self, cli_graph):
        home, data, ids = cli_graph
        r = _run_cli(["supersede", ids["decision"], "Use Postgres"], data, home)
        assert r.returncode == 0, r.stderr

        r = _run_cli(["changelog", "--days", "1"], data, home)
        assert r.returncode == 0, r.stderr
        assert "## Superseded" in r.stdout

    def test_changelog_single_entry_per_edit(self, cli_graph):
        """One kin edit -> one changelog entry: under '## Edited' only, with
        the README-documented header format and [type] prefix."""
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["concept"], "--content", "One entry only"],
                     data, home)
        assert r.returncode == 0, r.stderr

        r = _run_cli(["changelog", "--days", "1"], data, home)
        assert r.returncode == 0, r.stderr
        assert "## Edited (1 nodes)" in r.stdout   # matches README example
        assert "[concept] Widget pattern" in r.stdout  # type prefix renders
        assert "## Updated" not in r.stdout  # no duplicate update_node entry

    def test_changelog_json_carries_diffs(self, cli_graph):
        import json as _json
        home, data, ids = cli_graph
        r = _run_cli(["edit", ids["concept"], "--title", "Widget pattern v2"],
                     data, home)
        assert r.returncode == 0, r.stderr

        r = _run_cli(["changelog", "--days", "1", "--json"], data, home)
        assert r.returncode == 0, r.stderr
        payload = _json.loads(r.stdout)
        edited = payload["groups"]["Edited"]
        diffs = edited[0]["details"]["diffs"]
        assert diffs["title"]["old"] == "Widget pattern"
        assert diffs["title"]["new"] == "Widget pattern v2"
