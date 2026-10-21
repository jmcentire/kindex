"""Conversation claim lifecycle and explicit-memory isolation regressions.

All sources and provider responses are invented. Covers expiry changes and
removal, identical reingest after retraction, unrelated conversation preservation,
and explicit-memory reads that neither activate claims nor admit unverified,
unscoped generated claims.
"""
import copy
import hashlib
import json
import socket
from types import SimpleNamespace

import pytest

AT = "2030-01-02T12:00:00Z"
DATE = "2026-01-01T00:00:00Z"
BODY = "claims-audit-token The fictional user prefers amber kayaks."
MESSAGES = [{"role": "user", "content": BODY}]


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    from kindex import conversations, llm
    from kindex.config import Config
    from kindex.store import Store

    def reject_network(*args, **kwargs):
        raise AssertionError("Audit attempted a real network operation")

    monkeypatch.setattr(socket.socket, "connect", reject_network)
    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setenv("KIN_NO_SCHEDULER_WRITES", "1")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    cfg = Config(data_dir=str(tmp_path / "graph"), user="invented-audit-owner",
        llm={"enabled": True, "provider": "openai", "model": "invented-fake-model"},
        embedding={"provider": "none"}, secret_mask=None,
        **{name: str(tmp_path / "absent-history") for name in (
            "claude_dir", "codex_dir", "gemini_dir", "antigravity_dir", "antigravity_cli_dir",
            "opencode_dir", "cursor_dir", "muse_dir")})
    cfg._stamp_on_open = False
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        payload = {"claims": [{"m": 1, "kind": "preference", "subject": "user", "date": "", "text": BODY}]}
        usage = SimpleNamespace(input_tokens=1, output_tokens=1, cache_creation_input_tokens=0, cache_read_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))], usage=usage)

    client = SimpleNamespace(messages=SimpleNamespace(create=create))
    monkeypatch.setattr(llm, "get_client", lambda *args, **kwargs: client)
    store = Store(cfg, manage_project_storage=False)
    yield SimpleNamespace(store=store, cfg=cfg, conv=conversations, calls=calls, root=tmp_path)
    store.close()


def generated_claims(fixture):
    return [node for node in fixture.store.all_nodes(node_type="document", limit=100)
            if node.get("extra", {}).get("turn_claim")]


def seed_claim(fixture, *, expires=None):
    fixture.conv.ingest_conversation(fixture.store, "invented-claims-audit", MESSAGES, DATE, expires=expires)
    assert fixture.conv.backfill_claims(fixture.store, fixture.cfg) == 1
    claims = generated_claims(fixture)
    assert len(claims) == 1
    return claims[0]


@pytest.mark.parametrize("next_expiry", ["2032-01-01", None], ids=["changed", "removed"])
def test_expiry_only_revision_refreshes_generated_claim(fixture, next_expiry):
    first_expiry = "2031-01-01"
    first = seed_claim(fixture, expires=first_expiry)
    assert first["extra"]["expires"] == first_expiry
    calls_before = len(fixture.calls)
    changed = fixture.conv.ingest_conversation(fixture.store, "invented-claims-audit", MESSAGES, DATE,
                                               expires=next_expiry)
    assert changed, "Expiry revision fixture did not update its source chunk"
    processed = fixture.conv.backfill_claims(fixture.store, fixture.cfg)
    current = generated_claims(fixture)
    assert len(current) == 1
    assert (processed, len(fixture.calls) - calls_before, current[0]["extra"].get("expires")) == (1, 1, next_expiry)


def test_retracted_identical_conversation_can_regenerate_missing_claim(fixture):
    seed_claim(fixture)
    fixture.conv.retract_conversation(fixture.store, "invented-claims-audit")
    assert generated_claims(fixture) == []
    fixture.conv.ingest_conversation(fixture.store, "invented-claims-audit", MESSAGES, DATE)
    processed = fixture.conv.backfill_claims(fixture.store, fixture.cfg)
    assert (processed, len(generated_claims(fixture))) == (1, 1)


def test_explicit_reads_do_not_activate_claims_or_admit_actual_generated_unscoped_claim(fixture, monkeypatch):
    from kindex import correction_references as references, query_context, llm
    from kindex.sessions import start_tag
    from kindex.store import Store

    assert fixture.cfg.conversations.claims is False
    claim = seed_claim(fixture)
    assert claim["prov_activity"] == "conversation-digest"
    assert not claim.get("verified_at")
    assert not claim["extra"].get("project_path")
    project = str((fixture.root / "invented-project").resolve())
    session = start_tag(fixture.store, "audit-owner", project_path=project)
    allowed = fixture.store.add_node("claims-audit-token verified source", content="claims-audit-token verified local evidence",
        extra={"project_path": project}, queue_embedding=False)
    fixture.store.verify_node(allowed, verified_by="invented-audit-reviewer", prov_method="synthetic-fixture", verified_at=DATE)
    partitions = [{"partition_id": "current", "kind": "repo", "project_path": project, "kinbase": None}]
    policy = {"policy_id": "audit-policy", "graph_id": fixture.store.graph_id, "profile": "", "session_id": session,
              "current_project": project, "partitions": partitions, "allowed_actor_ids": ["invented-audit-owner"]}
    digest = hashlib.sha256(json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    context = references.ReadContext(graph_id=fixture.store.graph_id, profile="", client="codex", session_id=session,
        current_project=project, partitions=partitions,
        correction_policy={"policy_id": "audit-policy", "policy_sha256": digest, "allowed_actor_ids": ["invented-audit-owner"]})
    cfg_before = fixture.cfg.model_dump()
    database_before = tuple(fixture.store.conn.iterdump())
    calls_before = len(fixture.calls)

    def forbidden(*args, **kwargs):
        raise AssertionError("Explicit read attempted claims/digest/model work")

    for name in ("digest_conversation", "backfill_digests", "backfill_claims"):
        monkeypatch.setattr(fixture.conv, name, forbidden)
    monkeypatch.setattr(llm, "get_client", forbidden)
    reader = Store(fixture.cfg.model_copy(deep=True), read_only=True, migrate=False, manage_project_storage=False)
    try:
        accepted = references.capture_reference(reader, allowed, context=context, evaluation_time=AT)
        assert accepted["reference"]["node_id"] == allowed
        with pytest.raises(references.ReferenceError):
            references.capture_reference(reader, claim["id"], context=context, evaluation_time=AT)
        result = query_context.prepare_query_context(reader, query="claims-audit-token", project_path=project,
            client_scope=query_context.TrustedClientScope(mode="client", client="codex"),
            retrieval_max_bytes=4096, top_k=6, evaluation_time=AT)
        serialized = json.dumps(result)
        assert "verified local evidence" in result["retrieval"]["text"]
        assert BODY not in serialized and claim["id"] not in serialized
    finally:
        reader.close()
    assert fixture.cfg.conversations.claims is False
    assert fixture.cfg.model_dump() == cfg_before
    assert tuple(fixture.store.conn.iterdump()) == database_before
    assert len(fixture.calls) == calls_before


def test_retract_preserves_unrelated_conversation_and_its_claim_bookkeeping(fixture):
    seed_claim(fixture)
    other_id = "invented-unrelated-conversation"
    fixture.conv.ingest_conversation(fixture.store, other_id,
        [{"role": "user", "content": "A separate fictional preference about garden paths."}], DATE,
        expires="2034-01-01")
    assert fixture.conv.backfill_claims(fixture.store, fixture.cfg) == 1

    def other_nodes():
        return {node["id"]: copy.deepcopy(node)
                for node in fixture.store.all_nodes(node_type="document", limit=100)
                if node.get("extra", {}).get("conversation_id") == other_id}

    unrelated_before = other_nodes()
    assert len(unrelated_before) >= 2, "Fixture needs both an unrelated chunk and a derived claim"
    claim_records = json.loads(fixture.store.get_meta("conversation_claims"))
    assert "invented-claims-audit" in claim_records and other_id in claim_records
    unrelated_edges = {node_id: copy.deepcopy(fixture.store.edges_from(node_id)) for node_id in unrelated_before}
    removed = fixture.conv.retract_conversation(fixture.store, "invented-claims-audit")
    assert removed > 0
    assert other_nodes() == unrelated_before
    assert {node_id: fixture.store.edges_from(node_id) for node_id in unrelated_before} == unrelated_edges
    remaining_records = json.loads(fixture.store.get_meta("conversation_claims") or "{}")
    assert remaining_records == {other_id: claim_records[other_id]}, "Retraction must forget only its own claim revision"
    calls_before = len(fixture.calls)
    assert fixture.conv.backfill_claims(fixture.store, fixture.cfg) == 0
    assert len(fixture.calls) == calls_before
