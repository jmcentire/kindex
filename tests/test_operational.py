"""Tests for operational node types: constraint, directive, checkpoint, watch."""

import subprocess
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


def run(*args, data_dir=None):
    cmd = [sys.executable, "-m", "kindex.cli", *args]
    if data_dir:
        cmd.extend(["--data-dir", data_dir])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


class TestConstraintNodes:
    def test_create_constraint(self, store):
        nid = store.add_node(
            "src/api must never have circular imports",
            node_type="constraint",
            extra={"trigger": "pre-commit", "action": "block"},
        )
        node = store.get_node(nid)
        assert node["type"] == "constraint"
        assert node["extra"]["trigger"] == "pre-commit"
        assert node["extra"]["action"] == "block"

    def test_query_by_trigger(self, store):
        store.add_node("Lint before commit", node_type="constraint",
                       extra={"trigger": "pre-commit", "action": "verify"})
        store.add_node("Run tests before deploy", node_type="constraint",
                       extra={"trigger": "pre-deploy", "action": "block"})
        store.add_node("Check migrations", node_type="checkpoint",
                       extra={"trigger": "pre-deploy"})

        pre_deploy = store.nodes_by_trigger("pre-deploy")
        assert len(pre_deploy) == 2
        titles = [n["title"] for n in pre_deploy]
        assert "Run tests before deploy" in titles
        assert "Check migrations" in titles

    def test_active_constraints(self, store):
        store.add_node("Active rule", node_type="constraint", status="active")
        store.add_node("Old rule", node_type="constraint", status="archived")
        active = store.active_constraints()
        assert len(active) == 1
        assert active[0]["title"] == "Active rule"


class TestDirectiveNodes:
    def test_create_directive(self, store):
        nid = store.add_node(
            "One f-bomb per customer per week",
            node_type="directive",
            extra={"scope": "customer-communications", "resets": "monday"},
        )
        node = store.get_node(nid)
        assert node["type"] == "directive"
        assert node["extra"]["scope"] == "customer-communications"

    def test_directive_in_summary(self, store):
        store.add_node("Be professional in PRs", node_type="directive",
                       extra={"scope": "code-review"})
        ops = store.operational_summary()
        assert len(ops["directives"]) == 1


class TestCheckpointNodes:
    def test_create_checkpoint(self, store):
        nid = store.add_node(
            "Always lint this.file before commit",
            node_type="checkpoint",
            extra={"trigger": "pre-commit"},
        )
        node = store.get_node(nid)
        assert node["type"] == "checkpoint"

    def test_active_checkpoints_by_trigger(self, store):
        store.add_node("Lint", node_type="checkpoint",
                       extra={"trigger": "pre-commit"})
        store.add_node("Run integration tests", node_type="checkpoint",
                       extra={"trigger": "pre-deploy"})

        pre_commit = store.active_checkpoints(trigger="pre-commit")
        assert len(pre_commit) == 1
        assert pre_commit[0]["title"] == "Lint"


class TestWatchNodes:
    def test_create_watch(self, store):
        nid = store.add_node(
            "Erik's auth refactor may affect session handling",
            node_type="watch",
            extra={"owner": "jeremy", "expires": "2026-03-15"},
        )
        node = store.get_node(nid)
        assert node["type"] == "watch"
        assert node["extra"]["owner"] == "jeremy"

    def test_active_watches_excludes_expired(self, store):
        store.add_node("Future watch", node_type="watch",
                       extra={"expires": "2099-01-01"})
        store.add_node("Expired watch", node_type="watch",
                       extra={"expires": "2020-01-01"})
        store.add_node("No expiry watch", node_type="watch")

        watches = store.active_watches()
        titles = [w["title"] for w in watches]
        assert "Future watch" in titles
        assert "No expiry watch" in titles
        assert "Expired watch" not in titles

    def test_filter_by_owner(self, store):
        store.add_node("Jeremy's watch", node_type="watch",
                       extra={"owner": "jeremy"})
        store.add_node("Erik's watch", node_type="watch",
                       extra={"owner": "erik"})

        jeremy = store.nodes_by_owner("jeremy")
        assert len(jeremy) == 1
        assert jeremy[0]["title"] == "Jeremy's watch"


class TestOperationalSummary:
    def test_full_summary(self, store):
        store.add_node("Constraint A", node_type="constraint")
        store.add_node("Directive B", node_type="directive")
        store.add_node("Checkpoint C", node_type="checkpoint")
        store.add_node("Watch D", node_type="watch",
                       extra={"expires": "2099-01-01"})

        ops = store.operational_summary()
        assert len(ops["constraints"]) == 1
        assert len(ops["directives"]) == 1
        assert len(ops["checkpoints"]) == 1
        assert len(ops["watches"]) == 1

    def test_summary_with_owner_filter(self, store):
        store.add_node("My watch", node_type="watch",
                       extra={"owner": "me", "expires": "2099-01-01"})
        store.add_node("Other watch", node_type="watch",
                       extra={"owner": "other", "expires": "2099-01-01"})

        ops = store.operational_summary(owner="me")
        assert len(ops["watches"]) == 1
        assert ops["watches"][0]["title"] == "My watch"

    def test_summary_with_trigger_filter(self, store):
        store.add_node("Deploy check", node_type="constraint",
                       extra={"trigger": "pre-deploy"})
        store.add_node("Commit check", node_type="constraint",
                       extra={"trigger": "pre-commit"})

        ops = store.operational_summary(trigger="pre-deploy")
        assert len(ops["constraints"]) == 1
        assert ops["constraints"][0]["title"] == "Deploy check"


class TestOperationalCLI:
    def test_add_constraint(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        r = run("add", "Never break the API contract",
                "--type", "constraint", "--trigger", "pre-deploy",
                "--action", "block", data_dir=d)
        assert r.returncode == 0
        assert "Constraint" in r.stdout

    def test_add_watch(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        r = run("add", "Erik auth refactor",
                "--type", "watch", "--owner", "jeremy",
                "--expires", "2026-03-15", data_dir=d)
        assert r.returncode == 0
        assert "Watch" in r.stdout

    def test_add_directive(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        r = run("add", "Be professional in code reviews",
                "--type", "directive", "--scope", "code-review",
                data_dir=d)
        assert r.returncode == 0
        assert "Directive" in r.stdout

    def test_status_with_trigger(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        run("add", "Run full test suite", "--type", "checkpoint",
            "--trigger", "pre-deploy", data_dir=d)
        run("add", "Check API contracts", "--type", "constraint",
            "--trigger", "pre-deploy", "--action", "block", data_dir=d)

        r = run("status", "--trigger", "pre-deploy", data_dir=d)
        assert r.returncode == 0
        assert "Constraints" in r.stdout or "Checkpoints" in r.stdout

    def test_status_shows_operational_count(self, tmp_path):
        d = str(tmp_path)
        run("init", data_dir=d)
        run("add", "No circular imports", "--type", "constraint", data_dir=d)
        run("add", "Watch the auth refactor", "--type", "watch", data_dir=d)

        r = run("status", data_dir=d)
        assert r.returncode == 0
        assert "constraint" in r.stdout.lower() or "watch" in r.stdout.lower()


class TestOperationalInContext:
    def test_constraints_appear_in_full_context(self, tmp_path):
        cfg = Config(data_dir=str(tmp_path))
        store = Store(cfg)
        store.add_node("Stigmergy", content="Coordination", node_id="stig", weight=1.0)
        store.add_node("Never break API", node_type="constraint",
                       extra={"trigger": "pre-deploy", "action": "block"})

        from kindex.retrieve import format_context_block, hybrid_search
        results = hybrid_search(store, "stigmergy", top_k=5)
        block = format_context_block(store, results, query="stigmergy", level="full")
        assert "Active constraints" in block
        assert "Never break API" in block
        store.close()

    def test_watches_appear_in_abridged_context(self, tmp_path):
        cfg = Config(data_dir=str(tmp_path))
        store = Store(cfg)
        store.add_node("Stigmergy", content="Coordination", node_id="stig", weight=1.0)
        store.add_node("Auth refactor watch", node_type="watch",
                       extra={"owner": "jeremy", "expires": "2099-01-01"})

        from kindex.retrieve import format_context_block, hybrid_search
        results = hybrid_search(store, "stigmergy", top_k=5)
        block = format_context_block(store, results, query="stigmergy", level="abridged")
        assert "Watches" in block
        assert "Auth refactor watch" in block
        store.close()
