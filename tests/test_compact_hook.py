"""Tests for compact-hook envelope handling (issue #14).

Claude Code's PreCompact hook pipes a JSON envelope ({session_id,
transcript_path, cwd, hook_event_name, ...}) on stdin. transcript_path
is a file path, not conversation text — extracting from the envelope
itself minted one blank node per JSON field.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys

import pytest

from kindex.config import Config
from kindex.store import Store

_SESSION_ID = "0f9b3a52-1111-2222-3333-444455556666"


def _env(tmp_path):
    env = dict(os.environ)
    env.pop("KIN_PROFILE", None)
    env.pop("ANTHROPIC_API_KEY", None)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    return env


def _run_hook(tmp_path, input_text):
    cmd = [sys.executable, "-m", "kindex.cli", "compact-hook",
           "--data-dir", str(tmp_path / "data")]
    return subprocess.run(cmd, input=input_text, capture_output=True,
                          text=True, timeout=60, env=_env(tmp_path),
                          cwd=str(tmp_path))


def _write_transcript(tmp_path, texts):
    transcript = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": text}]},
        })
        for text in texts
    ]
    transcript.write_text("\n".join(lines) + "\n")
    return transcript


def _envelope(tmp_path, transcript=None):
    payload = {
        "session_id": _SESSION_ID,
        "cwd": str(tmp_path),
        "hook_event_name": "PreCompact",
        "trigger": "auto",
        "custom_instructions": "",
    }
    if transcript is not None:
        payload["transcript_path"] = str(transcript)
    return json.dumps(payload)


def _nodes(tmp_path):
    cfg = Config(data_dir=str(tmp_path / "data"))
    store = Store(cfg)
    try:
        return {n["title"]: n for n in store.all_nodes(limit=500)}
    finally:
        store.close()


def _candidates(tmp_path):
    cfg = Config(data_dir=str(tmp_path / "data"))
    store = Store(cfg)
    try:
        return [
            store.get_capture_candidate(row["id"])
            for row in store.list_capture_candidates(limit=500)
        ]
    finally:
        store.close()


def _reinforce_queue(tmp_path):
    cfg = Config(data_dir=str(tmp_path / "data"))
    store = Store(cfg)
    try:
        raw = store.get_meta("reinforce.queue")
        return json.loads(raw) if raw else []
    finally:
        store.close()


@pytest.mark.red_now
def test_envelope_fields_do_not_become_nodes(tmp_path):
    """P2.1/P2.2: a real envelope reaches compact-hook; durable nodes and
    envelope metadata in candidate payload/source are forbidden, while complete
    transcript-derived candidates are demanded. Restoring direct node minting
    is the smallest reversion that turns red.
    """
    transcript = _write_transcript(tmp_path, [
        "We learned that the cache invalidation bug was caused by a stale "
        "TTL configuration in the deploy pipeline.",
    ])
    r = _run_hook(tmp_path, _envelope(tmp_path, transcript))
    assert r.returncode == 0, r.stderr

    assert _nodes(tmp_path) == {}
    candidates = _candidates(tmp_path)
    assert candidates
    for junk in ("session_id", "transcript_path", "cwd", "hook_event_name",
                 "PreCompact", "auto", _SESSION_ID, str(transcript)):
        assert all(junk not in candidate["title"] for candidate in candidates)
        assert all(junk not in candidate["content"] for candidate in candidates)
    for candidate in candidates:
        assert candidate["status"] == "pending"
        assert candidate["content"].strip()
        assert re.fullmatch(r"[0-9a-f]{64}", candidate["source_digest"])
        assert candidate["source_digest"] != str(transcript)


@pytest.mark.red_now
def test_envelope_extracts_from_transcript_text(tmp_path):
    """P2.1/T5.1: transcript extraction reaches compact-hook; a durable node
    is forbidden and a content-bearing pending cache-invalidation candidate is
    demanded. Replacing staging with add_node turns this red.
    """
    transcript = _write_transcript(tmp_path, [
        "We learned that the cache invalidation bug was caused by a stale "
        "TTL configuration in the deploy pipeline.",
    ])
    r = _run_hook(tmp_path, _envelope(tmp_path, transcript))
    assert r.returncode == 0, r.stderr

    assert _nodes(tmp_path) == {}
    assert any(
        "cache invalidation" in candidate["title"]
        for candidate in _candidates(tmp_path)
    )


@pytest.mark.red_now
def test_envelope_without_transcript_creates_nothing(tmp_path):
    """P2.1/T5.2: a parseable envelope without transcript reaches the hook;
    node/candidate creation is forbidden and both stores must remain empty.
    Falling back to envelope text is the smallest mutation that turns red.
    """
    r = _run_hook(tmp_path, _envelope(tmp_path))
    assert r.returncode == 0, r.stderr
    assert _nodes(tmp_path) == {}
    assert _candidates(tmp_path) == []


def test_envelope_still_enqueues_reinforce(tmp_path):
    transcript = _write_transcript(tmp_path, ["Short."])
    r = _run_hook(tmp_path, _envelope(tmp_path, transcript))
    assert r.returncode == 0, r.stderr

    queue = _reinforce_queue(tmp_path)
    assert any(job.get("conversation_id") == _SESSION_ID for job in queue)
    assert any(job.get("transcript_path") == str(transcript) for job in queue)


@pytest.mark.red_now
def test_plain_text_still_extracts(tmp_path):
    """P2.1/T5.2: non-envelope stdin reaches explicit text extraction;
    durable minting is forbidden and one retry-logic candidate is demanded.
    Dropping the non-envelope fallback is the smallest mutation that turns red.
    """
    text = ("During this refactor we learned that the retry logic never "
            "honored the backoff ceiling configured for the API client.")
    r = _run_hook(tmp_path, text)
    assert r.returncode == 0, r.stderr

    assert _nodes(tmp_path) == {}
    candidates = _candidates(tmp_path)
    assert any("retry logic" in candidate["title"] for candidate in candidates)
    assert all(candidate["content"].strip() for candidate in candidates)


@pytest.mark.red_now
def test_title_only_keyword_concepts_are_not_minted(tmp_path):
    """P2.1/T5.4: title-only keyword hints reach automatic extraction;
    candidate and node creation are both forbidden. Removing the content-bearing
    gate is the smallest mutation that turns red.
    """
    # Quoted terms produce title-only concepts in the keyword fallback;
    # those are linking hints, not knowledge worth minting as blank nodes.
    text = ('The "flux capacitor" and the "warp drive" subsystems were '
            "mentioned repeatedly during the discussion today.")
    r = _run_hook(tmp_path, text)
    assert r.returncode == 0, r.stderr
    assert _nodes(tmp_path) == {}
    assert _candidates(tmp_path) == []


@pytest.mark.red_now
def test_short_plain_text_keeps_original_floor(tmp_path):
    """P2.1/T5.2: 33-character non-envelope text reaches the legacy floor;
    durable nodes are forbidden and a caches-auth-tokens candidate is demanded.
    Applying the envelope length floor globally is the smallest mutation red.
    """
    # 33 chars — below the 50-char envelope floor, above the plain-text 10
    r = _run_hook(tmp_path, "learned that X caches auth tokens")
    assert r.returncode == 0, r.stderr
    assert _nodes(tmp_path) == {}
    assert any(
        "caches auth tokens" in candidate["title"]
        for candidate in _candidates(tmp_path)
    )


def test_transcript_with_non_object_json_lines_does_not_crash(tmp_path):
    from kindex.ingest import _extract_session_text

    transcript = tmp_path / "mixed.jsonl"
    transcript.write_text(
        "null\n42\n[1, 2]\n\"str\"\n"
        + json.dumps({
            "type": "assistant",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": "Real insight."},
                                    {"type": "text", "text": None}]},
        }) + "\n"
    )
    assert "Real insight." in _extract_session_text(transcript)


def test_extract_session_text_handles_nested_message_format(tmp_path):
    from kindex.ingest import _extract_session_text

    transcript = _write_transcript(tmp_path, ["First insight.", "Second insight."])
    text = _extract_session_text(transcript)
    assert "First insight." in text
    assert "Second insight." in text


def test_extract_session_text_handles_legacy_format(tmp_path):
    from kindex.ingest import _extract_session_text

    transcript = tmp_path / "legacy.jsonl"
    transcript.write_text(
        json.dumps({"role": "assistant", "content": "Legacy text."}) + "\n"
    )
    assert "Legacy text." in _extract_session_text(transcript)


def test_capture_session_end_skips_title_only_concepts(tmp_path):
    from kindex.budget import BudgetLedger
    from kindex.hooks import capture_session_end

    cfg = Config(data_dir=str(tmp_path / "data"))
    store = Store(cfg)
    try:
        ledger = BudgetLedger(cfg.ledger_path, cfg.budget)
        text = ('The "flux capacitor" and the "warp drive" subsystems were '
                "mentioned repeatedly during the discussion today.")
        capture_session_end(store, cfg, ledger, session_text=text)
        titles = {n["title"] for n in store.all_nodes(limit=100)}
        assert "flux capacitor" not in titles
        assert "warp drive" not in titles
    finally:
        store.close()
