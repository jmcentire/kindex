"""Tests for conversation-attention reminder injection."""

from __future__ import annotations

import json
from types import SimpleNamespace

from kindex.cli import build_parser
from kindex.attention import (
    estimate_message_window,
    extract_conversation_text,
    resolve_conversation_id,
    run_attention_check,
    runtime_status,
    select_candidates,
    set_runtime_enabled,
)
from kindex.budget import BudgetLedger
from kindex.config import AttentionConfig, BudgetConfig, Config, LLMConfig
from kindex.store import Store


class _MockMessages:
    def __init__(self, candidate_id: str):
        self.candidate_id = candidate_id
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        payload = {
            "inject": [{
                "id": self.candidate_id,
                "message": "Before deploying, verify tests, version, artifact, and live endpoint.",
                "reason": "The conversation is about deploying.",
                "confidence": 0.91,
            }]
        }
        return SimpleNamespace(
            content=[SimpleNamespace(text=json.dumps(payload))],
            usage=SimpleNamespace(
                input_tokens=120,
                output_tokens=35,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
        )


class _MockClient:
    def __init__(self, candidate_id: str):
        self.messages = _MockMessages(candidate_id)


def _config(tmp_path, *, enabled=True, tick_interval=1, check_budget=0.05):
    return Config(
        data_dir=str(tmp_path),
        llm=LLMConfig(enabled=True),
        budget=BudgetConfig(daily=1.0, weekly=5.0, monthly=10.0),
        attention=AttentionConfig(
            enabled=enabled,
            tick_interval=tick_interval,
            max_check_cost=check_budget,
            max_conversation_cost=0.50,
        ),
    )


def test_select_candidates_matches_explicit_trigger(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg)
    node_id = store.add_node(
        "Deploy checklist",
        node_type="directive",
        content="Any time you deploy, verify tests and live endpoint.",
        extra={"attention_triggers": ["deploy", "release", "go live"]},
    )

    candidates = select_candidates(store, "Let's deploy this now.", cfg)

    assert candidates
    assert candidates[0].id == f"node:{node_id}"
    assert "trigger:deploy" in candidates[0].reason
    store.close()


def test_select_candidates_matches_reminder_trigger(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg)
    rid = store.add_reminder(
        "Production release note",
        "2099-01-01T10:00:00",
        body="Remember to publish the release note.",
        extra={"attention_triggers": ["release", "ship"]},
    )

    candidates = select_candidates(store, "We are going to ship this.", cfg)

    assert any(c.id == f"reminder:{rid}" for c in candidates)
    store.close()


def test_select_candidates_scopes_reminders_by_conversation_id(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg)
    chat_a = store.add_reminder(
        "Chat A release note",
        "2099-01-01T10:00:00",
        extra={"attention_triggers": ["ship"], "conversation_id": "chat-a"},
    )
    chat_b = store.add_reminder(
        "Chat B release note",
        "2099-01-01T10:00:00",
        extra={"attention_triggers": ["ship"], "conversation_id": "chat-b"},
    )
    legacy = store.add_reminder(
        "Legacy release note",
        "2099-01-01T10:00:00",
        extra={"attention_triggers": ["ship"]},
    )
    global_id = store.add_reminder(
        "Global release note",
        "2099-01-01T10:00:00",
        extra={"attention_triggers": ["ship"], "reminder_scope": "global"},
    )

    candidates = select_candidates(
        store,
        "We are going to ship this.",
        cfg,
        conversation_id="chat-a",
    )
    ids = {c.id for c in candidates}

    assert f"reminder:{chat_a}" in ids
    assert f"reminder:{global_id}" in ids
    assert f"reminder:{chat_b}" not in ids
    assert f"reminder:{legacy}" not in ids
    store.close()


def test_resolve_conversation_id_accepts_client_aliases(monkeypatch):
    assert resolve_conversation_id(hook_payload={"chat_id": "chat-1"}) == "chat-1"
    assert resolve_conversation_id(hook_payload={"conversationId": "conv-1"}) == "conv-1"
    assert resolve_conversation_id(hook_payload={"session": {"id": "session-1"}}) == "session-1"

    monkeypatch.setenv("KIN_CHAT_ID", "env-chat")
    assert resolve_conversation_id(fallback_to_cwd=False) == "env-chat"


def test_resolve_conversation_id_can_disable_cwd_fallback(monkeypatch):
    for key in (
        "KIN_CHAT_ID",
        "KIN_CONVERSATION_ID",
        "CLAUDE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CODEX_CONVERSATION_ID",
        "OPENCODE_SESSION_ID",
        "OPENCODE_CHAT_ID",
        "CURSOR_SESSION_ID",
        "CURSOR_CHAT_ID",
    ):
        monkeypatch.delenv(key, raising=False)

    assert resolve_conversation_id(fallback_to_cwd=False) == ""


def test_candidate_trigger_derivation_uses_word_boundaries(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg)
    store.add_node(
        "Unrelated validation task",
        node_type="task",
        content="Fix validation in contract parsing after adoption runs.",
    )

    candidates = select_candidates(store, "Let's deploy Kindex now.", cfg)

    assert not any(c.reason == "trigger:in" for c in candidates)
    store.close()


def test_candidate_trigger_derivation_does_not_use_on_project_name(tmp_path):
    cfg = _config(tmp_path)
    store = Store(cfg)
    store.add_node(
        "Watch flagged on Kindex rust support",
        node_type="watch",
        content="This should not become a Kindex trigger just because the phrase says on Kindex.",
    )

    candidates = select_candidates(store, "Let's deploy Kindex now.", cfg)

    assert not any("trigger:kindex" in c.reason for c in candidates)
    store.close()


def test_attention_noops_without_llm_configured(tmp_path):
    cfg = Config(
        data_dir=str(tmp_path),
        llm=LLMConfig(enabled=False),
        attention=AttentionConfig(enabled=True, tick_interval=1),
    )
    store = Store(cfg)
    store.add_node(
        "Deploy checklist",
        node_type="directive",
        extra={"attention_triggers": ["deploy"]},
    )
    ledger = BudgetLedger(cfg.ledger_path, cfg.budget)

    result = run_attention_check(
        store,
        cfg,
        ledger,
        "deploy this",
        "conv-1",
        force=True,
        client=_MockClient("node:any"),
    )

    assert result["status"] == "llm_not_configured"
    assert result["injections"] == []
    assert ledger.entries == []
    store.close()


def test_attention_uses_ticks_and_records_conversation_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = _config(tmp_path, tick_interval=2)
    store = Store(cfg)
    node_id = store.add_node(
        "Deploy checklist",
        node_type="directive",
        content="Any time you deploy, verify tests, changelog, version, artifact, and live endpoint.",
        extra={"attention_triggers": ["deploy"]},
    )
    ledger = BudgetLedger(cfg.ledger_path, cfg.budget)
    client = _MockClient(f"node:{node_id}")

    first = run_attention_check(store, cfg, ledger, "deploy this", "conv-1", client=client)
    second = run_attention_check(store, cfg, ledger, "deploy this", "conv-1", client=client)

    assert first["status"] == "waiting_for_tick"
    assert second["status"] == "ok"
    assert second["injections"][0]["id"] == f"node:{node_id}"
    assert client.messages.calls == 1
    assert ledger.entries[0]["purpose"] == "attention"
    assert ledger.entries[0]["conversation_id"] == "conv-1"
    assert ledger.conversation_spend("conv-1", purpose="attention") > 0
    store.close()


def test_attention_cooldown_suppresses_repeat(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = _config(tmp_path)
    store = Store(cfg)
    node_id = store.add_node(
        "Deploy checklist",
        node_type="directive",
        extra={"attention_triggers": ["deploy"]},
    )
    ledger = BudgetLedger(cfg.ledger_path, cfg.budget)

    first_client = _MockClient(f"node:{node_id}")
    second_client = _MockClient(f"node:{node_id}")
    first = run_attention_check(
        store, cfg, ledger, "deploy this", "conv-1", force=True, client=first_client,
    )
    second = run_attention_check(
        store, cfg, ledger, "deploy this", "conv-1", force=True, client=second_client,
    )

    assert first["injections"]
    assert second["status"] == "no_candidates"
    assert second_client.messages.calls == 0
    store.close()


def test_attention_estimate_respects_check_budget(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    cfg = _config(tmp_path, check_budget=0.000001)
    store = Store(cfg)
    node_id = store.add_node(
        "Deploy checklist",
        node_type="directive",
        extra={"attention_triggers": ["deploy"]},
    )
    ledger = BudgetLedger(cfg.ledger_path, cfg.budget)
    client = _MockClient(f"node:{node_id}")

    result = run_attention_check(
        store,
        cfg,
        ledger,
        "deploy this",
        "conv-1",
        force=True,
        client=client,
    )

    assert result["status"] == "estimate_exceeds_check_budget"
    assert client.messages.calls == 0
    assert ledger.entries == []
    store.close()


def test_runtime_toggle_overrides_config_default(tmp_path):
    cfg = Config(data_dir=str(tmp_path), attention=AttentionConfig(enabled=False))
    store = Store(cfg)

    assert runtime_status(store, cfg)["enabled"] is False
    set_runtime_enabled(store, True)
    assert runtime_status(store, cfg)["enabled"] is True
    set_runtime_enabled(store, False, conversation_id="conv-1")
    status = runtime_status(store, cfg, "conv-1")
    assert status["enabled"] is False
    assert status["conversation_override"] is False
    store.close()


def test_estimate_message_window_projects_by_tick_interval(tmp_path):
    cfg = _config(tmp_path, tick_interval=4)

    estimate = estimate_message_window(cfg, messages=10)

    assert estimate["messages"] == 10
    assert estimate["tick_interval"] == 4
    assert estimate["estimated_llm_checks"] == 3
    assert estimate["window_estimate"] > 0


def test_estimate_message_window_includes_observed_projection(tmp_path):
    cfg = _config(tmp_path, tick_interval=5)

    estimate = estimate_message_window(
        cfg,
        messages=10,
        observed_entries=[
            {"purpose": "attention", "amount": 0.002},
            {"purpose": "search", "amount": 0.050},
            {"purpose": "attention", "amount": 0.004},
        ],
    )

    assert estimate["observed"]["checks"] == 2
    assert estimate["observed"]["window_projection"] == 0.006


def test_attention_parser_accepts_estimate_action():
    parser = build_parser()

    args = parser.parse_args(["attention", "estimate", "--messages", "1000"])

    assert args.attention_action == "estimate"
    assert args.messages == 1000


def test_extract_conversation_text_falls_back_to_tool_payload():
    text = extract_conversation_text(
        hook_payload={
            "tool_name": "Bash",
            "tool_input": {"command": "git commit -m test"},
        },
    )

    assert "tool_name: Bash" in text
    assert "git commit" in text


def test_prompt_check_parser_accepts_adapter():
    parser = build_parser()

    args = parser.parse_args(["prompt-check", "--adapter", "codex"])

    assert args.adapter == "codex"


def test_attention_hook_parser_accepts_client_adapter():
    parser = build_parser()

    args = parser.parse_args([
        "attention-hook",
        "--adapter",
        "claude",
        "--event",
        "PreToolUse",
    ])

    assert args.adapter == "claude"
    assert args.event == "PreToolUse"
