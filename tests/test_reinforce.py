"""Tests for session-end stigmergic reinforcement (arm A observed + arm B counterfactual)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kindex.attention import _save_state
from kindex.config import AttentionConfig, BudgetConfig, Config, LLMConfig
from kindex.reinforce import reinforce_session
from kindex.store import Store


class _Messages:
    def __init__(self, payload: dict):
        self.payload = payload
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(self.payload))],
            usage=SimpleNamespace(input_tokens=200, output_tokens=60,
                                  cache_creation_input_tokens=0,
                                  cache_read_input_tokens=0),
        )


class _Client:
    def __init__(self, payload: dict):
        self.messages = _Messages(payload)


def _config(tmp_path):
    return Config(
        data_dir=str(tmp_path),
        llm=LLMConfig(enabled=True),
        budget=BudgetConfig(daily=1.0, weekly=5.0, monthly=10.0),
        attention=AttentionConfig(enabled=True),
    )


def _phero(store, node_id, context=""):
    return store.conn.execute(
        "SELECT strength, deposits, reinforcements, missed FROM injection_pheromone "
        "WHERE node_id = ? AND context = ?", (node_id, context),
    ).fetchone()


def test_observed_use_reinforces_injected_node(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    nid = store.add_node("Always switch gh identity", node_type="directive",
                         content="before gh ops, gh auth switch")
    _save_state(store, "conv-1", {
        "conversation_id": "conv-1", "ticks": 1,
        "injected": {f"node:{nid}": "2026-06-02T00:00:00"},
        "pheromone_deposits": {f"node:{nid}": {"at": "x", "context": ""}},
    })
    client = _Client({"observed": [
        {"id": nid, "category": "used", "confidence": 0.9, "evidence": "agent ran gh auth switch"}
    ], "missed": []})

    res = reinforce_session(store, cfg, "conv-1", "trace…", client=client)
    assert res["status"] == "ok"
    row = _phero(store, nid)
    assert row["reinforcements"] == 1
    assert row["strength"] == pytest.approx(cfg.attention.pheromone_reinforce, abs=0.01)
    store.close()


def test_user_correction_weighs_heaviest(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    nid = store.add_node("Identity rule", node_type="constraint", content="never use wrong account")
    _save_state(store, "conv-1", {
        "conversation_id": "conv-1", "ticks": 1,
        "injected": {f"node:{nid}": "t"}, "pheromone_deposits": {f"node:{nid}": {"context": ""}},
    })
    client = _Client({"observed": [
        {"id": nid, "category": "user_correction", "confidence": 0.95,
         "evidence": "user: you used the wrong account again"}
    ], "missed": []})

    reinforce_session(store, cfg, "conv-1", "trace", client=client)
    row = _phero(store, nid)
    # correction weight (4.0) > plain reinforce weight (3.0)
    assert row["strength"] == pytest.approx(cfg.attention.pheromone_correction, abs=0.01)
    assert cfg.attention.pheromone_correction > cfg.attention.pheromone_reinforce
    store.close()


def test_counterfactual_deposits_on_unmatched_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    # A node that was NOT injected this session but would have helped.
    missed_node = store.add_node(
        "Pact validation coercion bug", node_type="concept",
        content="LLM contracts return stringified JSON where Pydantic expects objects")
    _save_state(store, "conv-1", {
        "conversation_id": "conv-1", "ticks": 1, "injected": {}, "pheromone_deposits": {},
    })
    client = _Client({"observed": [], "missed": [
        {"category": "agent_admission", "confidence": 0.8,
         "query": "Pact validation coercion stringified JSON Pydantic",
         "evidence": "agent: I should have coerced the JSON types first"}
    ]})

    res = reinforce_session(store, cfg, "conv-1", "trace", client=client)
    row = _phero(store, missed_node)
    assert row is not None
    assert row["missed"] == 1
    assert row["reinforcements"] == 0
    assert row["strength"] == pytest.approx(cfg.attention.pheromone_counterfactual, abs=0.01)
    assert any(not o["injected"] for o in res["outcomes"])
    store.close()


def test_missed_need_with_no_match_logs_knowledge_gap(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    _save_state(store, "conv-1", {"conversation_id": "conv-1", "ticks": 1,
                                  "injected": {}, "pheromone_deposits": {}})
    client = _Client({"observed": [], "missed": [
        {"category": "inferred", "confidence": 0.7,
         "query": "zzzznonexistenttopic quux flibbertigibbet",
         "evidence": "agent hit an error no knowledge covers"}
    ]})

    res = reinforce_session(store, cfg, "conv-1", "trace", client=client)
    assert res["gaps"] == ["zzzznonexistenttopic quux flibbertigibbet"]
    pending = store.pending_suggestions()
    assert any("zzzznonexistenttopic" in s["concept_a"] for s in pending)
    store.close()


def test_low_confidence_findings_are_dropped(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    nid = store.add_node("Some rule", node_type="directive")
    _save_state(store, "conv-1", {"conversation_id": "conv-1", "ticks": 1,
                                  "injected": {f"node:{nid}": "t"},
                                  "pheromone_deposits": {f"node:{nid}": {"context": ""}}})
    client = _Client({"observed": [
        {"id": nid, "category": "used", "confidence": 0.2, "evidence": "weak"}
    ], "missed": []})

    res = reinforce_session(store, cfg, "conv-1", "trace", client=client)
    assert _phero(store, nid) is None  # below reinforce_min_confidence -> no deposit
    assert res["outcomes"] == []
    store.close()


def test_reinforcement_is_idempotent_per_session(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = _config(tmp_path)
    store = Store(cfg)
    nid = store.add_node("Rule", node_type="directive")
    _save_state(store, "conv-1", {"conversation_id": "conv-1", "ticks": 1,
                                  "injected": {f"node:{nid}": "t"},
                                  "pheromone_deposits": {f"node:{nid}": {"context": ""}}})
    client = _Client({"observed": [
        {"id": nid, "category": "used", "confidence": 0.9, "evidence": "used it"}
    ], "missed": []})

    first = reinforce_session(store, cfg, "conv-1", "trace", client=client)
    second = reinforce_session(store, cfg, "conv-1", "trace", client=client)
    assert first["status"] == "ok"
    assert second["status"] == "already_reinforced"
    assert client.messages.calls == 1  # not re-graded
    assert _phero(store, nid)["reinforcements"] == 1  # not double-counted
    store.close()


def test_enqueue_is_cheap_and_dedupes_preferring_transcript(tmp_path):
    from kindex.reinforce import enqueue_reinforce, REINFORCE_QUEUE_META
    cfg = _config(tmp_path)
    store = Store(cfg)
    # inline trace first (e.g. tag-end), then a transcript path (e.g. Stop hook)
    assert enqueue_reinforce(store, "conv-1", trace="summary text") is True
    assert enqueue_reinforce(store, "conv-1", transcript_path="/tmp/t.jsonl") is True
    queue = json.loads(store.get_meta(REINFORCE_QUEUE_META))
    assert len(queue) == 1                       # deduped by conversation_id
    assert queue[0]["transcript_path"] == "/tmp/t.jsonl"
    assert queue[0]["trace"] == "summary text"   # richest fields merged
    store.close()


def test_drain_grades_queue_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    from kindex.reinforce import enqueue_reinforce, drain_reinforce_queue, REINFORCE_QUEUE_META
    cfg = _config(tmp_path)
    store = Store(cfg)
    nid = store.add_node("Rule", node_type="directive")
    _save_state(store, "conv-1", {"conversation_id": "conv-1", "ticks": 1,
                                  "injected": {f"node:{nid}": "t"},
                                  "pheromone_deposits": {f"node:{nid}": {"context": ""}}})
    enqueue_reinforce(store, "conv-1", trace="the agent used the rule and it worked")
    client = _Client({"observed": [
        {"id": nid, "category": "used", "confidence": 0.9, "evidence": "used it"}
    ], "missed": []})

    res = drain_reinforce_queue(store, cfg, client=client)
    assert res["graded"] == 1
    assert json.loads(store.get_meta(REINFORCE_QUEUE_META)) == []  # queue cleared
    assert _phero(store, nid)["reinforcements"] == 1

    # Re-enqueue same conversation -> drain is a no-op (already graded), queue clears.
    enqueue_reinforce(store, "conv-1", trace="again")
    res2 = drain_reinforce_queue(store, cfg, client=client)
    assert res2["graded"] == 0
    assert _phero(store, nid)["reinforcements"] == 1  # not double-counted
    store.close()


def test_drain_reads_bounded_transcript_tail(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    from kindex.reinforce import enqueue_reinforce, drain_reinforce_queue
    cfg = _config(tmp_path)
    store = Store(cfg)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("HEADMARKER" + ("x" * 1000) + "TAILMARKER")
    _save_state(store, "conv-1", {"conversation_id": "conv-1", "ticks": 1,
                                  "injected": {}, "pheromone_deposits": {}})
    enqueue_reinforce(store, "conv-1", transcript_path=str(transcript))
    captured = {}

    class _CapClient:
        class messages:
            @staticmethod
            def create(**kwargs):
                captured["prompt"] = kwargs["messages"][0]["content"]
                return SimpleNamespace(
                    content=[SimpleNamespace(text=json.dumps({"observed": [], "missed": []}))],
                    usage=SimpleNamespace(input_tokens=50, output_tokens=10,
                                          cache_creation_input_tokens=0, cache_read_input_tokens=0))

    res = drain_reinforce_queue(store, cfg, client=_CapClient(), max_trace_chars=20)
    assert res["status"] == "ok"
    assert "TAILMARKER" in captured["prompt"]      # tail read
    assert "HEADMARKER" not in captured["prompt"]  # head dropped
    store.close()
