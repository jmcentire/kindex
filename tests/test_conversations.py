"""Tests for conversation ingest and digests (kindex.conversations)."""

import json
from types import SimpleNamespace

import pytest

from kindex import conversations as conv
from kindex.config import Config, LLMConfig
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(Config(data_dir=str(tmp_path)))
    yield s
    s.close()


MESSAGES = [
    {"role": "user", "content": "I bought a red kayak for $450."},
    {"role": "assistant", "content": "Nice! " + "Paddle safely. " * 300},
    {"role": "user", "content": "Always give distances in kilometres."},
]


def test_pack_keeps_whole_messages_and_splits_long_ones():
    chunks = conv.pack(["a" * 10, "b" * 25, "c" * 5], limit=20)
    assert chunks == ["a" * 10, "b" * 20, "b" * 5 + "\n" + "c" * 5]
    assert all(len(c) <= 20 for c in chunks)


def test_ingest_is_lossless_dated_linked_and_idempotent(store):
    ids = conv.ingest_conversation(store, "c1", MESSAGES, "2024/03/10 (Sun) 09:00")
    assert len(ids) == 3  # the 4,500-character reply is split across two nodes
    text = "\n".join(store.get_node(i)["content"] for i in ids)
    for m in MESSAGES:
        assert m["content"].strip() in text.replace("\n", "")
    first = store.get_node(ids[0])
    assert first["prov_when"] == "2024/03/10 (Sun) 09:00"
    assert first["type"] == "document"
    assert [e["to_id"] for e in store.edges_from(ids[0])] == [ids[1]]
    assert conv.ingest_conversation(store, "c1", MESSAGES, "2024/03/10 (Sun) 09:00") == []


def test_ingest_directory_reads_json_and_jsonl(store, tmp_path):
    d = tmp_path / "convs"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"id": "a", "date": "2024-01-01", "messages": MESSAGES[:1]}))
    (d / "b.jsonl").write_text(json.dumps({"id": "b", "messages": MESSAGES[2:]}) + "\n")
    assert conv.ingest_directory(store, d) == 2


def test_conversations_adapter_is_discovered():
    from kindex.adapters.registry import discover

    assert "conversations" in discover()


def _fake_client(payload):
    calls = []

    def create(**kw):
        calls.append(kw)
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0,
                                cache_read_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))], usage=usage)

    return SimpleNamespace(messages=SimpleNamespace(create=create)), calls


def test_digest_records_directives_once_and_a_linked_summary(store, tmp_path, monkeypatch):
    from kindex import llm

    client, calls = _fake_client({"directives": ["Always give distances in kilometres."],
                                  "summary": "The user bought a red kayak for $450 on 10 March 2024."})
    monkeypatch.setattr(llm, "get_client", lambda config, timeout=None: client)
    cfg = Config(data_dir=str(tmp_path), llm=LLMConfig(enabled=True, provider="openai", model="m"))
    monkeypatch.setattr(conv, "SUMMARY_MIN_TOKENS", 0)
    ids = conv.ingest_conversation(store, "c1", MESSAGES, "2024-03-10")
    assert conv.backfill_digests(store, cfg) == 1
    directives = store.all_nodes(node_type="directive")
    assert [d["title"] for d in directives] == ["Always give distances in kilometres."]
    summary = [n for n in store.all_nodes(node_type="document") if n["id"].startswith("convsum-")][0]
    assert "$450" in summary["content"] and summary["prov_when"] == "2024-03-10"
    assert {e["to_id"] for e in store.edges_from(summary["id"])} == set(ids)
    assert calls[0]["json_schema"]["name"] == "conversation_digest"
    # Already digested: no second call, no duplicate directive.
    assert conv.backfill_digests(store, cfg) == 0
    assert len(calls) == 1


def test_short_conversations_get_directives_but_no_summary(store, tmp_path, monkeypatch):
    from kindex import llm

    client, calls = _fake_client({"directives": [], "summary": "should not be stored"})
    monkeypatch.setattr(llm, "get_client", lambda config, timeout=None: client)
    cfg = Config(data_dir=str(tmp_path), llm=LLMConfig(enabled=True, provider="openai", model="m"))
    conv.ingest_conversation(store, "c1", MESSAGES[:1], "2024-03-10")
    assert conv.backfill_digests(store, cfg) == 1
    assert calls[0]["json_schema"]["schema"]["required"] == ["directives"]
    assert not [n for n in store.all_nodes(node_type="document") if n["id"].startswith("convsum-")]


def test_user_messages_keeps_only_the_users_side():
    text = "user: line one\ncontinues here\nassistant: a long reply\nuser: second"
    assert conv.user_messages(text) == "user: line one\ncontinues here\nuser: second"
    assert conv.user_messages("Caroline: hi\nMelanie: hello") == ""
