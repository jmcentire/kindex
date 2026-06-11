"""Tests for advisory node locks and Store.atomic_extra_update."""

import threading
import time

import pytest

from kindex.config import Config
from kindex.locks import cleanup_expired_locks, lock_node, unlock_node
from kindex.store import LockHeldError, Store, active_lock


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


def _expired_lock(agent="ghost"):
    return {"agent": agent, "acquired_at": "2020-01-01T00:00:00",
            "expires_at": "2020-01-01T01:00:00", "note": ""}


# ── active_lock helper ─────────────────────────────────────────────────


class TestActiveLock:
    def test_no_lock(self):
        assert active_lock({"extra": {}}) is None
        assert active_lock({}) is None
        assert active_lock({"extra": {"lock": "garbage"}}) is None

    def test_active(self):
        lock = {"agent": "a", "expires_at": "2099-01-01T00:00:00"}
        assert active_lock({"extra": {"lock": lock}}) == lock

    def test_expired_treated_as_absent(self):
        assert active_lock({"extra": {"lock": _expired_lock()}}) is None

    def test_no_expiry_is_active(self):
        lock = {"agent": "a"}
        assert active_lock({"extra": {"lock": lock}}) == lock


# ── lock_node ──────────────────────────────────────────────────────────


class TestLockNode:
    def test_acquire(self, store):
        nid = store.add_node("L")
        lock = lock_node(store, nid, "agent-a", ttl_minutes=30, note="editing")
        assert lock["agent"] == "agent-a"
        assert lock["note"] == "editing"
        assert lock["expires_at"] > lock["acquired_at"]
        assert store.get_node(nid)["extra"]["lock"] == lock

    def test_conflict(self, store):
        nid = store.add_node("L")
        lock_node(store, nid, "agent-a")
        with pytest.raises(LockHeldError, match="agent-a"):
            lock_node(store, nid, "agent-b")

    def test_holder_refresh(self, store):
        nid = store.add_node("L")
        first = lock_node(store, nid, "agent-a", ttl_minutes=1)
        second = lock_node(store, nid, "agent-a", ttl_minutes=240, note="more")
        assert second["expires_at"] > first["expires_at"]
        assert store.get_node(nid)["extra"]["lock"]["note"] == "more"

    def test_force_takeover(self, store):
        nid = store.add_node("L")
        lock_node(store, nid, "agent-a")
        lock = lock_node(store, nid, "agent-b", force=True)
        assert lock["agent"] == "agent-b"
        assert store.get_node(nid)["extra"]["lock"]["agent"] == "agent-b"

    def test_expired_lock_reacquirable(self, store):
        nid = store.add_node("L")
        store.atomic_extra_update(
            nid, lambda e: e.update({"lock": _expired_lock()}) or None)
        lock = lock_node(store, nid, "agent-b")  # no force needed
        assert lock["agent"] == "agent-b"

    def test_blank_agent_rejected(self, store):
        nid = store.add_node("L")
        with pytest.raises(ValueError, match="Agent"):
            lock_node(store, nid, "  ")

    def test_missing_node(self, store):
        with pytest.raises(KeyError):
            lock_node(store, "nope", "agent-a")


# ── unlock_node ────────────────────────────────────────────────────────


class TestUnlockNode:
    def test_release_by_holder(self, store):
        nid = store.add_node("L")
        lock_node(store, nid, "agent-a")
        assert unlock_node(store, nid, "agent-a") is True
        assert "lock" not in store.get_node(nid)["extra"]

    def test_no_lock_returns_false(self, store):
        nid = store.add_node("L")
        assert unlock_node(store, nid, "agent-a") is False

    def test_foreign_lock_refused(self, store):
        nid = store.add_node("L")
        lock_node(store, nid, "agent-a")
        with pytest.raises(LockHeldError, match="agent-a"):
            unlock_node(store, nid, "agent-b")

    def test_foreign_lock_force(self, store):
        nid = store.add_node("L")
        lock_node(store, nid, "agent-a")
        assert unlock_node(store, nid, "agent-b", force=True) is True
        assert "lock" not in store.get_node(nid)["extra"]

    def test_anyone_clears_expired(self, store):
        nid = store.add_node("L")
        store.atomic_extra_update(
            nid, lambda e: e.update({"lock": _expired_lock()}) or None)
        assert unlock_node(store, nid, "agent-b") is True
        assert "lock" not in store.get_node(nid)["extra"]


# ── cleanup_expired_locks ──────────────────────────────────────────────


class TestCleanupSweep:
    def test_sweep(self, store):
        expired = store.add_node("Expired")
        store.atomic_extra_update(
            expired, lambda e: e.update({"lock": _expired_lock()}) or None)
        held = store.add_node("Held")
        lock_node(store, held, "agent-a")
        plain = store.add_node("Plain")

        assert cleanup_expired_locks(store) == 1
        assert "lock" not in store.get_node(expired)["extra"]
        assert store.get_node(held)["extra"]["lock"]["agent"] == "agent-a"
        assert "lock" not in store.get_node(plain)["extra"]

    def test_sweep_skips_no_expiry_locks(self, store):
        nid = store.add_node("Forever")
        store.atomic_extra_update(
            nid, lambda e: e.update({"lock": {"agent": "a"}}) or None)
        assert cleanup_expired_locks(store) == 0
        assert store.get_node(nid)["extra"]["lock"] == {"agent": "a"}

    def test_sweep_skips_archived_nodes(self, store):
        nid = store.add_node("Archived", status="archived")
        store.atomic_extra_update(
            nid, lambda e: e.update({"lock": _expired_lock()}) or None)
        assert cleanup_expired_locks(store) == 0

    def test_sweep_empty_graph(self, store):
        assert cleanup_expired_locks(store) == 0


# ── atomic_extra_update ────────────────────────────────────────────────


class TestAtomicExtraUpdate:
    def test_in_place_mutation(self, store):
        nid = store.add_node("N", extra={"a": 1})
        final = store.atomic_extra_update(
            nid, lambda e: e.update({"b": 2}) or None)
        assert final == {"a": 1, "b": 2}
        assert store.get_node(nid)["extra"] == {"a": 1, "b": 2}

    def test_replacement_return(self, store):
        nid = store.add_node("N", extra={"a": 1})
        final = store.atomic_extra_update(nid, lambda e: {"replaced": True})
        assert final == {"replaced": True}
        assert store.get_node(nid)["extra"] == {"replaced": True}

    def test_missing_node_keyerror(self, store):
        with pytest.raises(KeyError):
            store.atomic_extra_update("nope", lambda e: e)

    def test_mutator_exception_rolls_back(self, store):
        nid = store.add_node("N", extra={"a": 1})

        def boom(extra):
            extra["a"] = 999
            raise RuntimeError("abort")

        with pytest.raises(RuntimeError, match="abort"):
            store.atomic_extra_update(nid, boom)
        assert store.get_node(nid)["extra"] == {"a": 1}

    def test_interleaving_two_store_handles(self, tmp_path):
        """BEGIN IMMEDIATE serializes writers: no lost updates."""
        cfg_a = Config(data_dir=str(tmp_path))
        store_a = Store(cfg_a)
        nid = store_a.add_node("Shared", extra={})

        # Second handle: must only ever be touched from the worker thread
        # (sqlite3 connections are bound to their creating thread).
        store_b = Store(Config(data_dir=str(tmp_path)))
        b_done = threading.Event()

        def writer_b():
            store_b.atomic_extra_update(
                nid, lambda e: e.update({"b": 1}) or None)
            b_done.set()
            store_b.close()

        thread = threading.Thread(target=writer_b, daemon=True)

        def mutator_a(extra):
            thread.start()
            time.sleep(0.3)  # B must block on A's write lock
            assert not b_done.is_set(), "B committed inside A's transaction"
            extra["a"] = 1

        store_a.atomic_extra_update(nid, mutator_a)
        thread.join(timeout=10)
        assert not thread.is_alive()
        assert b_done.is_set()

        final = store_a.get_node(nid)["extra"]
        assert final.get("a") == 1, "A's write was lost"
        assert final.get("b") == 1, "B's write was lost"
        store_a.close()
