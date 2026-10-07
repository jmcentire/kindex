"""Review fixes: one input cap for every entry point, verification and inventory
checks against what was shown, optional rereads, and chat transcript roles."""

import json
from types import SimpleNamespace

import pytest

from kindex import answer as answer_mod
from kindex.config import AskConfig, Config, LLMConfig
from kindex.store import Store


def _usage():
    return SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def _reply(text):
    return SimpleNamespace(content=[SimpleNamespace(text=text)], usage=_usage())


@pytest.fixture
def store(tmp_path):
    s = Store(Config(data_dir=str(tmp_path)))
    s.add_node("user: My first kayak trip", content="user: My first kayak trip was on the river.",
               node_id="k1", node_type="document", prov_when="2024/01/05 (Fri) 18:30")
    yield s
    s.close()


def _config(tmp_path, **ask):
    cfg = Config(data_dir=str(tmp_path), llm=LLMConfig(enabled=True, provider="openai", model="m"))
    cfg.ask = cfg.ask.model_copy(update=ask)
    return cfg


def test_input_cap_is_the_lower_nonzero_limit():
    assert answer_mod.input_cap(AskConfig()) is None
    assert answer_mod.input_cap(AskConfig(max_input_tokens=20000)) == 20000
    assert answer_mod.input_cap(AskConfig(verify=True, verify_input_tokens=20000)) == 20000
    assert answer_mod.input_cap(AskConfig(verify=True, max_input_tokens=4000, verify_input_tokens=20000)) == 4000
    assert answer_mod.input_cap(AskConfig(verify=False, max_input_tokens=0, verify_input_tokens=9000)) is None


def test_verification_answers_within_the_lower_cap(store, tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(answer_mod, "get_client", lambda *a, **kw: SimpleNamespace(messages=None))
    monkeypatch.setattr(answer_mod, "_answer", lambda *a, **kw: seen.update(cap=answer_mod._INPUT_LIMIT.get()))
    cfg = _config(tmp_path, verify=True, max_input_tokens=4000, verify_input_tokens=20000)
    assert answer_mod.answer_question(store, "When was my first kayak trip?", cfg) is None
    assert seen["cap"] == 4000


def test_a_failed_reread_keeps_the_first_draft(store, tmp_path, monkeypatch):
    calls = []

    def create(**kw):
        calls.append(kw)
        if len(calls) == 1:
            return _reply("The records don't say where the first kayak trip was.")
        raise RuntimeError("OpenAI API deadline exceeded")

    monkeypatch.setattr(answer_mod, "get_client", lambda *a, **kw: SimpleNamespace(messages=SimpleNamespace(create=create)))
    monkeypatch.setattr(answer_mod, "gather", lambda *a, **kw: [store.get_node("k1")])
    cfg = _config(tmp_path, reread=True, max_input_tokens=10000)
    result = answer_mod.answer_question(store, "Where was my first kayak trip?", cfg, as_of="2024-03-15")
    assert len(calls) >= 2  # the second reading was attempted
    assert result is not None and result.answer.startswith("The records don't say")


def test_verification_quotes_are_checked_against_the_rendered_lines():
    shown = "[2024/03/10] Source r1: user: I went kayaking yesterday [= 2024-03-09] with Sam."
    sources = {"r1": {"content": "user: I went kayaking yesterday with Sam. And a long unshown tail.",
                      "_rendered": shown}}

    def check(quote):
        return {"evidence": [{"ref": "r1", "include": True, "quote": quote, "item": "kayaking", "value": "1"}]}

    assert answer_mod._checked_rows(check("yesterday [= 2024-03-09] with Sam"), sources) is not None
    assert answer_mod._checked_rows(check("And a long unshown tail"), sources) is None


def test_named_roles_keep_the_user_turn_a_reply_answers():
    reply = " ".join(f"Point {i} is about pacing your work." for i in range(40))
    text = ("user (Ann): I just signed a contract with my first client today.\n"
            f"assistant (Bot): Congratulations! {reply} Send the invoice within a week. {reply}\n"
            "user (Ann): Thanks, that helps.")
    out = answer_mod.excerpt(text, {answer_mod._stem("invoice")}, exchange_context=True)
    assert "[...]" in out and "Send the invoice" in out  # excerpted, not returned whole
    assert "signed a contract with my first client" in out


def test_a_leading_system_turn_does_not_hide_a_chat():
    text = "system: You are a helpful assistant.\nuser: I bought a red kayak.\nassistant: Nice choice."
    assert answer_mod._is_chat(text)
    assert not answer_mod._is_chat("system: notes only\nno roles here")
    assert answer_mod._is_chat("user: hi\nassistant: hello") and not answer_mod._is_chat("Ann: hi\nBen: hello")


def test_an_inventory_keeps_a_lower_bound_answer():
    sources = {"r1": "user: I adopted Rex and Bella, and a few others I won't name."}
    rows = [{"item": name, "ref": "r1", "quote": "I adopted Rex and Bella", "reason": "adopted", "status": "included"}
            for name in ("Rex", "Bella")]
    raw = json.dumps({"answer": "At least two dogs, with others unnamed.", "mode": "instances",
                      "unit": "dogs", "rows": rows})
    assert answer_mod.render_inventory(raw, sources) == "At least two dogs, with others unnamed."
    exact = json.dumps({"answer": "Two dogs.", "mode": "instances", "unit": "dogs", "rows": rows})
    assert answer_mod.render_inventory(exact, sources).startswith("2 dogs")


@pytest.fixture
def mcp(tmp_path, monkeypatch):
    pytest.importorskip("mcp", reason="mcp not installed")
    import kindex.mcp_server as server

    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    for i in range(40):
        s.add_node(f"user: kayak trip {i}", content=f"user: kayak trip {i} on the lake. " + "Paddle. " * 120,
                   node_id=f"k{i}", node_type="document", prov_when=f"2024/03/{i % 28 + 1:02d}")
    monkeypatch.setattr(server, "_store", s)
    monkeypatch.setattr(server, "_config", cfg)
    monkeypatch.setattr(server, "operation_now", lambda: "2024-03-30T12:00:00+00:00")
    cfg.llm.enabled, cfg.llm.provider, cfg.llm.model = True, "openai", "m"
    cfg.budget.daily = cfg.budget.weekly = cfg.budget.monthly = 100.0
    yield server, s, cfg
    s.close()


def test_mcp_answers_share_the_input_cap(mcp, monkeypatch):
    server, _, cfg = mcp
    calls = []

    def create(**kw):
        calls.append(kw)
        if (kw.get("json_schema") or {}).get("name") == "query_plan":
            return _reply(json.dumps({"intent": "aggregation", "queries": ["kayak"], "needs_all_instances": True}))
        return _reply("Forty trips.")

    monkeypatch.setattr(answer_mod, "get_client", lambda config, **kw: SimpleNamespace(messages=SimpleNamespace(create=create)))
    cfg.ask = cfg.ask.model_copy(update={"max_input_tokens": 4000})
    out = server.ask("How many kayak trips did I take?", answer=True)
    assert out.startswith("Forty trips.")
    total = sum(answer_mod.estimate_tokens(c.get("system") or "") + answer_mod.estimate_tokens(c["messages"][0]["content"])
                + 64 + (answer_mod.estimate_tokens(json.dumps(c["json_schema"])) if c.get("json_schema") else 0)
                for c in calls)
    assert total <= 4000


def test_mcp_verification_uses_the_kin_ask_pipeline(mcp, monkeypatch):
    server, _, cfg = mcp
    monkeypatch.setattr(answer_mod, "get_client", lambda config, **kw: SimpleNamespace(messages=None))
    monkeypatch.setattr(answer_mod, "answer_question",
                        lambda store, question, config, ledger=None, **kw: SimpleNamespace(
                            answer="Checked: forty trips.", context="## Verification reading\nsources"))
    cfg.ask = cfg.ask.model_copy(update={"verify": True})
    out = server.ask("How many kayak trips did I take?", answer=True, graph="project")
    assert out.startswith("Checked: forty trips.") and "## Verification reading" in out
