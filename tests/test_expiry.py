"""Expiry enforcement: prime_context sections, attention candidates, daemon sweep."""

import datetime

import pytest

from kindex.config import Config
from kindex.store import Store, node_expired

PAST = "2020-01-01"
FUTURE = "2099-01-01"
TODAY = datetime.date.today().isoformat()


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


# ── hooks.prime_context ────────────────────────────────────────────────


def _section(out: str, header: str) -> str:
    """Extract one '### <header>' section from prime output (empty if absent).

    The 'Recent activity' section legitimately mentions expired nodes (it is
    an audit of log entries, not a knowledge surface), so assertions must be
    section-scoped.
    """
    captured: list[str] = []
    inside = False
    for line in out.splitlines():
        if line.startswith("### "):
            inside = line.startswith(f"### {header}")
            continue
        if inside:
            captured.append(line)
    return "\n".join(captured)


class TestPrimeContextExpiry:
    def test_expired_concept_skipped_in_key_concepts(self, store):
        from kindex.hooks import prime_context

        store.add_node("Quantum live concept", content="quantum entanglement basics",
                       node_type="concept")
        store.add_node("Quantum stale concept", content="quantum decoherence notes",
                       node_type="concept", extra={"expires": PAST})

        section = _section(prime_context(store, topic="quantum"), "Key concepts")
        assert "Quantum live concept" in section
        assert "Quantum stale concept" not in section

    def test_expired_constraint_skipped(self, store):
        from kindex.hooks import prime_context

        store.add_node("Always run tests", node_type="constraint",
                       extra={"action": "block"})
        store.add_node("Old expired rule", node_type="constraint",
                       extra={"action": "block", "expires": PAST})

        section = _section(prime_context(store, topic="anything"),
                           "Active constraints")
        assert "Always run tests" in section
        assert "Old expired rule" not in section

    def test_expired_directive_skipped(self, store):
        from kindex.hooks import prime_context

        store.add_node("Prefer early returns", node_type="directive")
        store.add_node("Bygone style rule", node_type="directive",
                       extra={"expires": PAST})

        section = _section(prime_context(store, topic="anything"), "Directives")
        assert "Prefer early returns" in section
        assert "Bygone style rule" not in section

    def test_watch_expiring_today_still_surfaces(self, store):
        from kindex.hooks import prime_context

        store.add_node("Flaky CI job", node_type="watch",
                       extra={"expires": TODAY, "owner": "jeremy"})

        section = _section(prime_context(store, topic="anything"), "Watches")
        assert "Flaky CI job" in section  # today is not yet expired

    def test_expired_task_skipped(self, store):
        from kindex.hooks import prime_context
        from kindex.tasks import create_task

        create_task(store, "Live global task", scope="global")
        dead = create_task(store, "Stale global task", scope="global")
        extra = dict(store.get_node(dead)["extra"])
        extra["expires"] = PAST
        store.update_node(dead, extra=extra)

        section = _section(prime_context(store, topic="anything"), "Tasks")
        assert "Live global task" in section
        assert "Stale global task" not in section


# ── attention.select_candidates ───────────────────────────────────────


class TestAttentionExpiry:
    def test_expired_node_not_a_candidate(self, store, tmp_path):
        from kindex.attention import select_candidates

        cfg = Config(data_dir=str(tmp_path))
        live = store.add_node(
            "Deploy checklist", node_type="directive",
            content="Any time you deploy, verify tests and live endpoint.",
            extra={"attention_triggers": ["deploy"]},
        )
        dead = store.add_node(
            "Retired deploy rule", node_type="directive",
            content="Old guidance about deploy procedure.",
            extra={"attention_triggers": ["deploy"], "expires": PAST},
        )

        ids = {c.id for c in select_candidates(store, "Let's deploy this now.", cfg)}
        assert f"node:{live}" in ids
        assert f"node:{dead}" not in ids


# ── daemon sweep ──────────────────────────────────────────────────────


class TestDaemonExpirySweep:
    def test_archives_expired_non_watch(self, store):
        from kindex.daemon import _expire_nodes

        nid = store.add_node("Stale concept", node_type="concept",
                             extra={"expires": PAST})
        results = _expire_nodes(store)
        assert results["archived"] == 1

        node = store.get_node(nid)
        assert node["status"] == "archived"
        assert node["extra"]["expired_at"]  # stamped
        assert node["extra"]["expires"] == PAST  # preserved

    def test_keeps_unexpired_nodes(self, store):
        from kindex.daemon import _expire_nodes

        today_id = store.add_node("Expires today", extra={"expires": TODAY})
        future_id = store.add_node("Expires later", extra={"expires": FUTURE})

        results = _expire_nodes(store)
        assert results["archived"] == 0
        assert store.get_node(today_id)["status"] == "active"
        assert store.get_node(future_id)["status"] == "active"

    def test_watch_lifecycle_unchanged(self, store):
        from kindex.daemon import _check_watches, _expire_nodes

        wid = store.add_node("Overdue watch", node_type="watch",
                             extra={"expires": PAST})

        # The generic sweep leaves watches to their dedicated lifecycle
        results = _expire_nodes(store)
        assert results["archived"] == 0
        assert store.get_node(wid)["status"] == "active"

        # _check_watches archives it exactly as before (no expired_at stamp)
        watch_results = _check_watches(store)
        assert watch_results["expired"] == 1
        node = store.get_node(wid)
        assert node["status"] == "archived"
        assert "expired_at" not in (node["extra"] or {})

    def test_watch_near_expiry_boost_unchanged(self, store):
        from kindex.daemon import _check_watches

        soon = (datetime.date.today() + datetime.timedelta(days=2)).isoformat()
        wid = store.add_node("Closing window", node_type="watch",
                             weight=0.5, extra={"expires": soon})

        results = _check_watches(store)
        assert results["notified"] == 1
        assert store.get_node(wid)["weight"] == pytest.approx(0.9)

    def test_cron_run_reports_nodes_expired(self, tmp_path):
        from kindex.daemon import cron_run

        cfg = Config(
            data_dir=str(tmp_path),
            claude_dir=str(tmp_path / "claude"),
            project_dirs=[str(tmp_path / "projects")],
        )
        s = Store(cfg)
        s.add_node("Stale concept", extra={"expires": PAST})

        results = cron_run(cfg, s, verbose=False)
        assert results["nodes_expired"] == 1
        s.close()


# ── node_expired semantics guard ──────────────────────────────────────


class TestNodeExpiredSemantics:
    def test_today_is_live_past_is_dead(self):
        assert not node_expired({"extra": {"expires": TODAY}})
        assert node_expired({"extra": {"expires": PAST}})
        assert not node_expired({"extra": {"expires": FUTURE}})
        assert not node_expired({"extra": {}})
        assert not node_expired({})
