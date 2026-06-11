"""Advisory node locks for multi-agent collaboration.

A lock lives in nodes.extra['lock'] = {agent, acquired_at, expires_at, note}.
Expiry is lazy: ``store.active_lock`` treats an expired lock as absent, so a
stale lock never blocks anyone. ``cleanup_expired_locks`` (run from the daemon
cron pass) clears expired locks from storage. All mutations go through
``Store.atomic_extra_update`` so concurrent agents cannot lose updates.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .store import LockHeldError, active_lock

if TYPE_CHECKING:
    from .store import Store


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _expires_at(ttl_minutes: int) -> str:
    return (datetime.now() + timedelta(minutes=ttl_minutes)).isoformat(
        timespec="seconds")


def lock_node(store: Store, node_id: str, agent: str, ttl_minutes: int = 60,
              note: str = "", force: bool = False) -> dict:
    """Acquire (or refresh) an advisory lock on a node.

    The holding agent may re-lock to extend the TTL or update the note.
    A foreign active lock raises LockHeldError unless force. Returns the
    lock dict.
    """
    agent = (agent or "").strip()
    if not agent:
        raise ValueError("Agent is required to lock a node")
    acquired: dict = {}

    def _mutate(extra: dict) -> None:
        current = active_lock({"extra": extra})
        if current is not None and current.get("agent") != agent and not force:
            raise LockHeldError(
                f"Node {node_id} is locked by '{current.get('agent', 'unknown')}'"
                f" until {current.get('expires_at', '?')} — pass force to override"
            )
        lock = {
            "agent": agent,
            "acquired_at": _now(),
            "expires_at": _expires_at(ttl_minutes),
            "note": note,
        }
        extra["lock"] = lock
        acquired["lock"] = lock

    store.atomic_extra_update(node_id, _mutate)
    return acquired["lock"]


def unlock_node(store: Store, node_id: str, agent: str,
                force: bool = False) -> bool:
    """Release a node lock. Returns True if a lock was cleared.

    Expired locks may be cleared by anyone. An active lock held by another
    agent raises LockHeldError unless force.
    """
    agent = (agent or "").strip()
    cleared = {"done": False}

    def _mutate(extra: dict) -> None:
        lock = extra.get("lock")
        if not isinstance(lock, dict):
            return
        current = active_lock({"extra": extra})
        if current is not None and current.get("agent") != agent and not force:
            raise LockHeldError(
                f"Node {node_id} is locked by '{current.get('agent', 'unknown')}'"
                f" — pass force to override"
            )
        extra.pop("lock", None)
        cleared["done"] = True

    store.atomic_extra_update(node_id, _mutate)
    return cleared["done"]


def cleanup_expired_locks(store: Store) -> int:
    """Sweep expired locks off all active nodes. Returns the count cleared.

    Locks without an expires_at never expire and are left alone; active
    (unexpired) locks are kept. Each clear re-checks state inside the
    atomic update so a lock refreshed mid-sweep is not dropped.
    """
    rows = store.conn.execute(
        "SELECT id, extra FROM nodes "
        "WHERE status = 'active' AND extra LIKE '%\"lock\"%'"
    ).fetchall()
    count = 0
    for row in rows:
        try:
            extra = json.loads(row["extra"] or "{}")
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(extra, dict):
            continue
        lock = extra.get("lock")
        if not isinstance(lock, dict) or not lock.get("expires_at"):
            continue
        if active_lock({"extra": extra}) is not None:
            continue  # still held

        cleared = {"done": False}

        def _mutate(e: dict, _flag: dict = cleared) -> None:
            current = e.get("lock")
            if (isinstance(current, dict) and current.get("expires_at")
                    and active_lock({"extra": e}) is None):
                e.pop("lock", None)
                _flag["done"] = True

        store.atomic_extra_update(row["id"], _mutate)
        if cleared["done"]:
            count += 1
    return count
