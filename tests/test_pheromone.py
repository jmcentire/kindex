"""Tests for stigmergic injection pheromone (deposit / reinforce / decay / blend)."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from kindex.config import Config
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


def _backdate(store: Store, node_id: str, context: str, days: float) -> None:
    """Push a trail's last_decay into the past so decay is observable."""
    past = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    store.conn.execute(
        "UPDATE injection_pheromone SET last_decay = ? WHERE node_id = ? AND context = ?",
        (past, node_id, context),
    )
    store.conn.commit()


def test_deposit_accumulates_and_counts(store):
    nid = store.add_node("Node A")
    assert store.deposit_pheromone(nid, amount=1.0) == 1.0
    assert store.deposit_pheromone(nid, amount=1.0) == 2.0

    row = store.conn.execute(
        "SELECT deposits, reinforcements FROM injection_pheromone "
        "WHERE node_id = ? AND context = ''", (nid,),
    ).fetchone()
    assert row["deposits"] == 2
    assert row["reinforcements"] == 0


def test_reinforce_outweighs_bare_deposit(store):
    bare = store.add_node("Bare")
    used = store.add_node("Used")
    store.deposit_pheromone(bare, amount=1.0)
    store.deposit_pheromone(used, amount=1.0)
    store.deposit_pheromone(used, amount=3.0, reinforce=True)  # confirmed useful

    scores = dict(store.pheromone_scores({bare, used}))
    assert scores[used] > scores[bare]
    assert scores[used] == pytest.approx(4.0, abs=0.01)

    row = store.conn.execute(
        "SELECT deposits, reinforcements FROM injection_pheromone "
        "WHERE node_id = ? AND context = ''", (used,),
    ).fetchone()
    assert row["deposits"] == 1 and row["reinforcements"] == 1


def test_ignored_trail_decays_away(store):
    nid = store.add_node("Ignored")
    store.deposit_pheromone(nid, amount=1.0, half_life_days=14.0)
    _backdate(store, nid, "", days=28)  # two half-lives

    scores = dict(store.pheromone_scores({nid}, half_life_days=14.0))
    assert scores[nid] == pytest.approx(0.25, abs=0.02)  # 1.0 * 0.5^2


def test_conditioned_trail_overrides_global_only_when_warm(store):
    nid = store.add_node("Ctx node")
    # Strong global trail, weak-but-present conditioned trail (below min_deposits).
    for _ in range(4):
        store.deposit_pheromone(nid, context="", amount=1.0)
    store.deposit_pheromone(nid, context="projX", amount=0.5)

    # min_deposits=5: conditioned trail (1 deposit) is ignored -> global wins.
    scores = dict(store.pheromone_scores({nid}, context="projX", min_deposits=5))
    assert scores[nid] == pytest.approx(4.0, abs=0.01)

    # Once the conditioned trail clears min_deposits, it takes over.
    for _ in range(5):
        store.deposit_pheromone(nid, context="projX", amount=0.5)
    scores = dict(store.pheromone_scores({nid}, context="projX", min_deposits=5))
    assert scores[nid] == pytest.approx(3.0, abs=0.01)  # 6 * 0.5


def test_decay_pheromone_prunes_dead_trails(store):
    alive = store.add_node("Alive")
    dead = store.add_node("Dead")
    store.deposit_pheromone(alive, amount=4.0, half_life_days=14.0)
    store.deposit_pheromone(dead, amount=1.0, half_life_days=14.0)
    _backdate(store, dead, "", days=140)  # ten half-lives -> ~0.001

    pruned = store.decay_pheromone(half_life_days=14.0, floor=0.02)
    assert pruned == 1
    remaining = store.conn.execute(
        "SELECT node_id FROM injection_pheromone"
    ).fetchall()
    assert [r["node_id"] for r in remaining] == [alive]


def test_pheromone_weight_opt_in_default_off():
    cfg = Config()
    assert cfg.ranking.pheromone_weight == 0.0
    assert "pheromone" not in cfg.ranking.ensemble_weights  # inert by default

    cfg.ranking.pheromone_weight = 0.1
    assert cfg.ranking.ensemble_weights["pheromone"] == 0.1


def _graded(store, node_id, amount=3.0):
    """Lay a graded (reinforcement) deposit — the only kind that counts toward maturity."""
    store.deposit_pheromone(node_id, amount=amount, reinforce=True)


def test_autoramp_stays_zero_until_mature(tmp_path):
    from kindex.reinforce import auto_ramp_pheromone_weight, learned_pheromone_weight
    cfg = Config(data_dir=str(tmp_path))
    store = Store(cfg)
    # A few graded nodes — below min_nodes (8) -> still inert.
    for i in range(3):
        _graded(store, store.add_node(f"n{i}"))
    ramp = auto_ramp_pheromone_weight(store, cfg)
    assert ramp["weight"] == 0.0
    assert "immature" in ramp["reason"]
    assert learned_pheromone_weight(store) == 0.0
    store.close()


def test_autoramp_lifts_weight_once_warm(tmp_path):
    from kindex.reinforce import auto_ramp_pheromone_weight, learned_pheromone_weight
    cfg = Config(data_dir=str(tmp_path))
    store = Store(cfg)
    # 8 graded nodes * 3.0 = 24 warm signal: past min (12), partway to full (60).
    for i in range(8):
        _graded(store, store.add_node(f"n{i}"), amount=3.0)
    ramp = auto_ramp_pheromone_weight(store, cfg)
    assert 0 < ramp["weight"] < cfg.attention.pheromone_target_weight  # partial ramp
    assert learned_pheromone_weight(store) == ramp["weight"]

    # Bare deposits (popularity) must NOT count toward maturity.
    store2 = Store(Config(data_dir=str(tmp_path / "b")))
    for i in range(20):
        store2.deposit_pheromone(store2.add_node(f"m{i}"), amount=1.0)  # bare
    assert auto_ramp_pheromone_weight(store2, cfg)["weight"] == 0.0
    store2.close()
    store.close()


def test_autoramp_ramps_back_down_when_trails_cool(tmp_path):
    from kindex.reinforce import auto_ramp_pheromone_weight
    cfg = Config(data_dir=str(tmp_path))
    store = Store(cfg)
    ids = [store.add_node(f"n{i}") for i in range(8)]
    for nid in ids:
        _graded(store, nid, amount=4.0)
    assert auto_ramp_pheromone_weight(store, cfg)["weight"] > 0

    # Trails cool (work moved on): decayed strength falls below warm_floor.
    for nid in ids:
        _backdate(store, nid, "", days=200)
    ramp = auto_ramp_pheromone_weight(store, cfg)
    assert ramp["weight"] == 0.0  # self-disables, no manual bit-flip
    store.close()


# ── deposits redirect through supersession chains (idx 31) ────────────
# Repro: supersede migrates trails exactly once; a deposit landing later
# (deferred session-end reinforcement) re-created rows under the dead id,
# boosting the stale node above its successor in ranking.


def test_deposit_on_superseded_node_lands_on_successor(store):
    old = store.add_node("Old decision text", node_type="concept")
    store.deposit_pheromone(old, amount=1.0)

    new = store.supersede_node(old, "New decision text")["id"]
    # Migration moved the existing trail.
    assert store.conn.execute(
        "SELECT COUNT(*) c FROM injection_pheromone WHERE node_id = ?",
        (old,)).fetchone()["c"] == 0

    # A late deposit against the OLD id must follow the chain.
    store.deposit_pheromone(old, amount=3.0, reinforce=True)
    assert store.conn.execute(
        "SELECT COUNT(*) c FROM injection_pheromone WHERE node_id = ?",
        (old,)).fetchone()["c"] == 0
    row = store.conn.execute(
        "SELECT strength, reinforcements FROM injection_pheromone "
        "WHERE node_id = ? AND context = ''", (new,)).fetchone()
    assert row is not None
    assert row["reinforcements"] == 1
    assert row["strength"] == pytest.approx(4.0, abs=0.01)


def test_deposit_follows_multi_hop_chain(store):
    a = store.add_node("Chain start node")
    b = store.supersede_node(a, "Chain middle node")["id"]
    c = store.supersede_node(b, "Chain end node")["id"]

    store.deposit_pheromone(a, amount=1.0)
    rows = store.conn.execute(
        "SELECT node_id FROM injection_pheromone").fetchall()
    assert [r["node_id"] for r in rows] == [c]


def test_deposit_redirect_is_cycle_safe(store):
    a = store.add_node("Cycle node a")
    b = store.add_node("Cycle node b")
    # Malformed graph: a <-> b supersession cycle (hand-crafted).
    store.update_node(a, extra={"superseded_by": b})
    store.update_node(b, extra={"superseded_by": a})

    # Must terminate and deposit somewhere on the chain without hanging.
    strength = store.deposit_pheromone(a, amount=1.0)
    assert strength == pytest.approx(1.0)


def test_deposit_on_live_node_unchanged(store):
    nid = store.add_node("Live node plain")
    assert store.deposit_pheromone(nid, amount=1.0) == 1.0
    assert store.conn.execute(
        "SELECT node_id FROM injection_pheromone").fetchone()["node_id"] == nid
