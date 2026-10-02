"""Tests for the answer pipeline (kindex.answer)."""

import json
from types import SimpleNamespace

import pytest

from kindex import answer as answer_mod
from kindex.config import Config, LLMConfig
from kindex.store import Store


class FakeMessages:
    """Records calls; replies with a plan to the planner and numbered answers otherwise."""

    def __init__(self, plan=None):
        self.calls = []
        self.plan = plan or {"intent": "aggregation", "queries": ["kayak trips"], "needs_all_instances": True}

    def create(self, **kw):
        self.calls.append(kw)
        if kw.get("json_schema", {}) and kw["json_schema"].get("name") == "query_plan":
            text = json.dumps(self.plan)
        else:
            text = f"answer {len(self.calls)}"
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(text=text)], usage=usage)


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    s.add_node("user: I went kayaking on the lake", content="user: I went kayaking on the lake with Sam.",
               node_id="k2", node_type="document", prov_when="2024/03/10 (Sun) 09:00")
    s.add_node("user: My first kayak trip", content="user: My first kayak trip was on the river.",
               node_id="k1", node_type="document", prov_when="2024/01/05 (Fri) 18:30")
    s.add_node("Always answer in metric units", node_id="d1", node_type="directive")
    yield s
    s.close()


def _config(tmp_path, **ask):
    cfg = Config(data_dir=str(tmp_path), llm=LLMConfig(enabled=True, provider="openai", model="m"))
    cfg.ask = cfg.ask.model_copy(update=ask)
    return cfg


def test_node_date_parses_conversation_formats():
    assert answer_mod.node_date({"prov_when": "2023/05/20 (Sat) 02:21"}).isoformat() == "2023-05-20T02:21:00"
    assert answer_mod.node_date({"prov_when": "1:56 pm on 8 May, 2023"}).date().isoformat() == "2023-05-08"
    assert answer_mod.node_date({"prov_when": "", "created_at": "2024-02-01T10:00:00"}).year == 2024
    assert answer_mod.node_date({}) is None


def test_assemble_shows_chosen_nodes_oldest_first_within_budget():
    nodes = [
        {"id": "b", "title": "", "content": "second " * 10, "prov_when": "2024-03-10"},
        {"id": "a", "title": "", "content": "first", "prov_when": "2024-01-05"},
        {"id": "c", "title": "", "content": "x" * 4000, "prov_when": "2024-02-01"},
    ]
    text, used, chosen = answer_mod.assemble(nodes, [], budget_tokens=100)
    assert [n["id"] for n in chosen] == ["a", "b"]  # c does not fit; rank order picks, dates order
    assert text.index("[2024-01-05] first") < text.index("[2024-03-10] second")
    assert used <= 120


def test_assemble_lists_standing_directives():
    text, _, _ = answer_mod.assemble([{"id": "a", "content": "x", "prov_when": "2024-01-01"}],
                                     [{"title": "Always answer in metric units", "content": ""}], 1000)
    assert text.startswith("## Standing directives\n\n- Always answer in metric units")


def test_answer_question_without_llm_returns_none(store, tmp_path):
    assert answer_mod.answer_question(store, "kayak?", Config(data_dir=str(tmp_path))) is None


def test_answer_question_plans_searches_and_dates_evidence(store, tmp_path, monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(answer_mod, "get_client", lambda config, timeout=None: SimpleNamespace(messages=fake))
    result = answer_mod.answer_question(store, "How many kayak trips did I take?", _config(tmp_path),
                                        as_of="2024-03-15")
    assert result.answer == "answer 2"
    assert result.intent == "aggregation"
    assert result.queries == ["How many kayak trips did I take?", "kayak trips"]
    final = fake.calls[-1]
    user = final["messages"][0]["content"]
    assert user.startswith("Today's date: 2024-03-15")
    assert "[2024/01/05 (Fri) 18:30] user: My first kayak trip" in user
    assert user.index("2024/01/05") < user.index("2024/03/10")
    assert "- Always answer in metric units" in user
    assert final["system"] == answer_mod.ANSWER_SYSTEM
    assert final["reasoning_effort"] == "high"


def test_samples_are_adjudicated(store, tmp_path, monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(answer_mod, "get_client", lambda config, timeout=None: SimpleNamespace(messages=fake))
    result = answer_mod.answer_question(store, "kayak trips?", _config(tmp_path, samples=3, plan=False))
    assert [c["sample"] for c in fake.calls[:3]] == [0, 1, 2]
    assert "Several candidate answers" in fake.calls[3]["messages"][0]["content"]
    assert result.answer == "answer 4"


def test_anthropic_calls_take_no_openai_options(store, tmp_path, monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(answer_mod, "get_client", lambda config, timeout=None: SimpleNamespace(messages=fake))
    cfg = Config(data_dir=str(tmp_path), llm=LLMConfig(enabled=True, provider="anthropic", model="m"))
    answer_mod.answer_question(store, "kayak trips?", cfg)
    assert all("reasoning_effort" not in c and "sample" not in c for c in fake.calls)


def test_openai_request_carries_instructions_effort_and_schema(monkeypatch):
    """The OpenAI adapter maps the new options onto the Responses API."""
    from kindex import llm

    sent = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"output_text": "ok", "usage": {"input_tokens": 3, "output_tokens": 1}}).encode()

    def urlopen(request, timeout=None):
        sent.update(json.loads(request.data))
        return Response()

    monkeypatch.setattr(llm.urllib.request, "urlopen", urlopen)
    out = llm._OpenAIResponsesMessages("key").create(
        model="m", max_tokens=100, messages=[{"role": "user", "content": "hi"}], system="be brief",
        reasoning_effort="high", json_schema={"name": "x", "schema": {"type": "object"}}, sample=2)
    assert out.content[0].text == "ok"
    assert sent["instructions"] == "be brief"
    assert sent["reasoning"] == {"effort": "high"}
    assert sent["text"]["format"]["name"] == "x"
    assert "sample" not in sent


def test_team_knowledge_is_listed_and_counted(store, tmp_path, monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(answer_mod, "get_client", lambda config, timeout=None: SimpleNamespace(messages=fake))
    result = answer_mod.answer_question(store, "kayak trips?", _config(tmp_path, plan=False),
                                        team=["Kayak rentals must be booked through the shared calendar."])
    user = fake.calls[-1]["messages"][0]["content"]
    assert "## Team knowledge" in user
    assert "- Kayak rentals must be booked through the shared calendar." in user
    assert user.index("## Team knowledge") < user.index("## Evidence, oldest first")
    assert result.context_tokens > 0


def test_without_team_knowledge_the_prompt_is_unchanged(store, tmp_path, monkeypatch):
    fake = FakeMessages()
    monkeypatch.setattr(answer_mod, "get_client", lambda config, timeout=None: SimpleNamespace(messages=fake))
    answer_mod.answer_question(store, "kayak trips?", _config(tmp_path, plan=False))
    assert "Team knowledge" not in fake.calls[-1]["messages"][0]["content"]


def test_facts_are_listed_by_their_own_date_before_the_excerpts():
    nodes = [
        {"id": "x", "content": "user: we went last Saturday", "prov_when": "2024-03-10"},
        {"id": "f2", "content": "The user went kayaking on 2024-03-09.", "prov_when": "2024-03-10",
         "extra": {"kind": "conversation-fact", "fact_date": "2024-03-09"}},
        {"id": "f1", "content": "The user bought a kayak.", "prov_when": "2024-03-10",
         "extra": {"kind": "conversation-fact", "fact_date": "2024-01-02"}},
    ]
    text, _, _ = answer_mod.assemble(nodes, [], 1000)
    assert text.index("2024-01-02: The user bought a kayak.") < text.index("2024-03-09: The user went kayaking")
    assert text.index("## Facts recorded") < text.index("## Evidence, oldest first")
    assert "user: we went last Saturday" in text.split("## Evidence, oldest first")[1]
