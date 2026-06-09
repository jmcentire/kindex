"""Tests for the Sim supervisory check-in (enqueue -> drain -> pickup, with
threshold gating, staleness drops, and the runtime kill switch)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kindex.config import BudgetConfig, Config, LLMConfig, SimConfig
from kindex.sim import (
    SIM_PENDING_META,
    SIM_QUEUE_META,
    clear_sim_override,
    drain_sim_queue,
    enqueue_sim_review,
    format_sim_injection,
    pop_pending_sim_injection,
    set_sim_enabled,
    sim_effective_enabled,
)
from kindex.store import Store


class _Messages:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=300, output_tokens=80,
                                  cache_creation_input_tokens=0,
                                  cache_read_input_tokens=0),
        )


class _Client:
    def __init__(self, payload: dict):
        self.messages = _Messages(payload)


def _config(tmp_path, **sim_kw):
    sim = SimConfig(enabled=True, tick_interval=6, threshold=0.7,
                    max_stale_ticks=4, min_overlap=0.18, **sim_kw)
    return Config(
        data_dir=str(tmp_path),
        llm=LLMConfig(enabled=True),
        budget=BudgetConfig(daily=1.0, weekly=5.0, monthly=10.0),
        sim=sim,
    )


_WINDOW = (
    "User: I think every microservice should own its own database, no exceptions. "
    "Agent: agreed, let's split the shared schema into seven per-service stores. "
    "User: yes and we drop foreign keys entirely since services can't share them."
)


def _meta_list(store, key):
    raw = store.get_meta(key)
    return json.loads(raw) if raw else []


# ── grounding: Sim reviews WITH the graph's context, not blind ──────────────

def test_build_sim_grounding_surfaces_constraints_and_concepts(tmp_path):
    from kindex.sim import build_sim_grounding
    cfg = _config(tmp_path)  # grounding_chars defaults to 1500
    store = Store(cfg)
    store.add_node("Microservice database ownership", content="each service owns its own store",
                   node_type="concept", node_id="msdb")
    store.add_node("No foreign keys across service boundaries", node_type="constraint",
                   node_id="fk", extra={"trigger": "schema-change", "action": "warn"})
    g = build_sim_grounding(store, _WINDOW, cfg)
    assert g  # non-empty
    assert "[constraint:warn]" in g  # active constraint surfaced via operational_summary
    store.close()


def test_build_sim_grounding_disabled_returns_empty(tmp_path):
    from kindex.sim import build_sim_grounding
    cfg = _config(tmp_path, grounding_chars=0)
    store = Store(cfg)
    store.add_node("Some concept", content="x", node_type="concept",
                   node_id="c", extra={"action": "warn"})
    assert build_sim_grounding(store, _WINDOW, cfg) == ""
    store.close()


def test_build_sim_grounding_respects_char_budget(tmp_path):
    from kindex.sim import build_sim_grounding
    cfg = _config(tmp_path, grounding_chars=120)
    store = Store(cfg)
    for i in range(12):
        store.add_node(f"Microservice database concept {i}",
                       content="microservice database foreign keys " * 10,
                       node_type="concept", node_id=f"c{i}")
    g = build_sim_grounding(store, _WINDOW, cfg)
    assert len(g) <= 120 + 200  # capped (allow a single trailing line's overshoot)
    store.close()


def test_supervisor_prompt_includes_grounding_only_when_present():
    from kindex.sim import build_supervisor_prompt
    with_g = build_supervisor_prompt("window text", 1000,
                                     grounding="- [constraint:warn] No FKs across services")
    assert "WHAT KINDEX ALREADY KNOWS" in with_g and "No FKs across services" in with_g
    without_g = build_supervisor_prompt("window text", 1000)
    assert "WHAT KINDEX ALREADY KNOWS" not in without_g


def test_drain_feeds_grounded_prompt_to_sim(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    store.add_node("No foreign keys across service boundaries", node_type="constraint",
                   node_id="fk", extra={"trigger": "schema-change", "action": "warn"})
    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)

    captured = {}

    class _CapMessages:
        def create(self, **kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps({"rating": 0.1, "note": "", "basis": ""}))],
                usage=SimpleNamespace(input_tokens=300, output_tokens=10,
                                      cache_creation_input_tokens=0, cache_read_input_tokens=0),
            )

    class _CapClient:
        messages = _CapMessages()

    drain_sim_queue(store, cfg, client=_CapClient())
    assert "WHAT KINDEX ALREADY KNOWS" in captured.get("prompt", "")
    assert "[constraint:warn]" in captured["prompt"]
    store.close()


def test_enqueue_gated_by_tick_interval(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg)
    # tick 5 is not a multiple of interval 6 -> no enqueue
    assert enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=5) is False
    assert _meta_list(store, SIM_QUEUE_META) == []
    # tick 6 enqueues
    assert enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6) is True
    queue = _meta_list(store, SIM_QUEUE_META)
    assert len(queue) == 1 and queue[0]["conversation_id"] == "c1"
    assert queue[0]["fingerprint"]  # tail fingerprint captured
    store.close()


def test_enqueue_dedups_by_conversation(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg)
    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)
    enqueue_sim_review(store, cfg, "c1", _WINDOW + " more", tick=12)
    queue = _meta_list(store, SIM_QUEUE_META)
    assert len(queue) == 1 and queue[0]["tick"] == 12  # fresher replaces staler
    store.close()


def test_drain_above_threshold_creates_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)
    client = _Client({"rating": 0.85, "note": "Dropping all FKs is a direction worth re-examining.",
                      "basis": "user is committing to no-FK across services"})
    res = drain_sim_queue(store, cfg, client=client)
    assert res["status"] == "ok" and res["reviewed"] == 1 and res["flagged"] == 1
    assert _meta_list(store, SIM_QUEUE_META) == []  # queue drained
    pending = _meta_list(store, SIM_PENDING_META)
    assert len(pending) == 1 and pending[0]["rating"] == 0.85
    store.close()


def test_drain_below_threshold_no_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)
    client = _Client({"rating": 0.3, "note": "minor nit", "basis": "x"})
    res = drain_sim_queue(store, cfg, client=client)
    assert res["reviewed"] == 1 and res["flagged"] == 0
    assert _meta_list(store, SIM_PENDING_META) == []
    store.close()


def test_drain_empty_note_no_pending(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)
    # high rating but no note -> nothing to say, no inject
    client = _Client({"rating": 0.9, "note": "", "basis": ""})
    drain_sim_queue(store, cfg, client=client)
    assert _meta_list(store, SIM_PENDING_META) == []
    store.close()


def test_pickup_fresh_window_injects(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)
    client = _Client({"rating": 0.85, "note": "Re-examine the no-FK call.", "basis": "b"})
    drain_sim_queue(store, cfg, client=client)
    inj = pop_pending_sim_injection(store, cfg, "c1", _WINDOW, tick=7)
    assert inj is not None and inj.message == "Re-examine the no-FK call."
    assert abs(inj.confidence - 0.85) < 1e-6
    # consumed — second pickup returns nothing
    assert pop_pending_sim_injection(store, cfg, "c1", _WINDOW, tick=8) is None
    store.close()


def test_pickup_dropped_when_stale_by_ticks(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)
    client = _Client({"rating": 0.85, "note": "n", "basis": "b"})
    drain_sim_queue(store, cfg, client=client)
    # tick 6 + max_stale_ticks 4 = 10; tick 11 is stale -> dropped, not shown
    inj = pop_pending_sim_injection(store, cfg, "c1", _WINDOW, tick=11)
    assert inj is None
    assert _meta_list(store, SIM_PENDING_META) == []  # consumed, never re-queued
    store.close()


def test_pickup_dropped_when_window_moved_on(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)
    client = _Client({"rating": 0.85, "note": "n", "basis": "b"})
    drain_sim_queue(store, cfg, client=client)
    moved = "Completely different topic about frontend button colors and CSS spacing tokens."
    inj = pop_pending_sim_injection(store, cfg, "c1", moved, tick=7)
    assert inj is None  # tail overlap below floor
    store.close()


def test_runtime_kill_switch_overrides_config(tmp_path):
    cfg = _config(tmp_path)  # config enabled
    store = Store(cfg)
    assert sim_effective_enabled(store, cfg) is True
    set_sim_enabled(store, False)
    assert sim_effective_enabled(store, cfg) is False
    # disabled at runtime -> enqueue is a no-op even though config is on
    assert enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6) is False
    clear_sim_override(store)
    assert sim_effective_enabled(store, cfg) is True
    store.close()


def test_command_path_invokes_subprocess(tmp_path):
    payload = '{"rating": 0.9, "note": "from command", "basis": "b"}'
    cfg = _config(tmp_path, command=f"printf '{payload}'")
    store = Store(cfg)
    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)
    res = drain_sim_queue(store, cfg)  # no client; uses the shell command
    assert res["flagged"] == 1
    inj = pop_pending_sim_injection(store, cfg, "c1", _WINDOW, tick=7)
    assert inj is not None and inj.message == "from command"
    store.close()


def test_format_sim_injection_modes():
    from kindex.attention import AttentionInjection
    assert format_sim_injection(None) == []
    inj = AttentionInjection(id="sim:c1", title="Sim (supervisory)",
                             message="reconsider X", reason="because Y", confidence=0.8)
    # minimal — a single bare user-facing line, no chrome
    assert format_sim_injection(inj, display="minimal") == ["Sim: reconsider X"]
    # quiet (default) — agent-facing act-or-escalate directive, invisible to user
    quiet = format_sim_injection(inj, display="quiet")
    assert len(quiet) == 1
    assert "reconsider X" in quiet[0]
    assert "act on it yourself" in quiet[0].lower()
    assert "the user does NOT see this" in quiet[0]
    # full — labelled block with basis + advisory footer
    full = format_sim_injection(inj, display="full")
    assert full[0] == "KINDEX · SIM"
    assert any("because Y" in ln for ln in full)
    assert any("kin sim disable" in ln for ln in full)


def test_guidance_set_get_clear(tmp_path):
    from kindex.sim import clear_sim_guidance, get_sim_guidance, set_sim_guidance
    cfg = _config(tmp_path)
    store = Store(cfg)
    assert get_sim_guidance(store) == ""
    assert clear_sim_guidance(store) is False  # nothing to clear
    set_sim_guidance(store, "  take this to production-grade compliance  ")
    assert get_sim_guidance(store) == "take this to production-grade compliance"
    assert clear_sim_guidance(store) is True
    assert get_sim_guidance(store) == ""
    store.close()


def test_guidance_steers_prompt_without_lowering_bar(tmp_path):
    from kindex.sim import build_supervisor_prompt
    base = build_supervisor_prompt(_WINDOW, 12000)
    guided = build_supervisor_prompt(_WINDOW, 12000, guidance="demand telemetry and runbooks")
    assert "demand telemetry and runbooks" in guided
    assert "demand telemetry and runbooks" not in base
    # the bar-preservation instruction rides along with the guidance
    assert "do NOT lower the bar" in guided
    # default-silent discipline is present in both
    assert "say NOTHING" in base and "say NOTHING" in guided


def test_drain_applies_guidance(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    from kindex.sim import set_sim_guidance
    set_sim_guidance(store, "watch for missing alerting")

    captured = {}

    class _CapMessages:
        def create(self, **kwargs):
            captured["prompt"] = kwargs["messages"][0]["content"]
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps({"rating": 0.2, "note": "", "basis": ""}))],
                usage=SimpleNamespace(input_tokens=100, output_tokens=20,
                                      cache_creation_input_tokens=0, cache_read_input_tokens=0))

    class _CapClient:
        messages = _CapMessages()

    enqueue_sim_review(store, cfg, "c1", _WINDOW, tick=6)
    drain_sim_queue(store, cfg, client=_CapClient())
    assert "watch for missing alerting" in captured["prompt"]
    store.close()
