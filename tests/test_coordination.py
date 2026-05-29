"""Tests for short-lived agent coordination state."""

from __future__ import annotations

import datetime

import pytest

from kindex.config import Config
from kindex.store import Store


@pytest.fixture
def store(tmp_path):
    cfg = Config(data_dir=str(tmp_path))
    s = Store(cfg)
    yield s
    s.close()


def test_create_conversation(store):
    from kindex.coordination import create_conversation, get_conversation

    conv_id = create_conversation(store, "Build Plan", created_by="agent-a")
    conv = get_conversation(store, "build-plan")
    assert conv["id"] == conv_id
    assert conv["type"] == "coordination"
    assert conv["extra"]["coord_status"] == "active"
    assert conv["extra"]["messages"] == []


def test_conversation_links_task(store):
    from kindex.coordination import create_conversation
    from kindex.tasks import create_task

    task_id = create_task(store, "Shared task")
    conv_id = create_conversation(store, "Shared", task_id=task_id)
    edges = store.edges_from(conv_id)
    assert any(edge["to_id"] == task_id for edge in edges)


def test_post_and_read_messages(store):
    from kindex.coordination import create_conversation, post_message, read_messages

    create_conversation(store, "Chat")
    first = post_message(store, "chat", "agent-a", "I claimed parser work")
    second = post_message(store, "chat", "agent-b", "I will take tests")
    assert first["id"] == 1
    assert second["id"] == 2

    payload = read_messages(store, "chat", since_id=1)
    assert payload["name"] == "chat"
    assert [m["body"] for m in payload["messages"]] == ["I will take tests"]


def test_post_refreshes_expiry_from_last_message(store, monkeypatch):
    import kindex.coordination as coordination
    from kindex.coordination import create_conversation, get_conversation, post_message

    now = datetime.datetime(2026, 5, 29, 12, 0, 0)
    monkeypatch.setattr(coordination, "_now_dt", lambda: now)
    create_conversation(store, "Sliding", ttl_minutes=10)
    assert get_conversation(store, "sliding")["extra"]["expires_at"] == "2026-05-29T12:10:00"

    now = datetime.datetime(2026, 5, 29, 12, 9, 0)
    monkeypatch.setattr(coordination, "_now_dt", lambda: now)
    post_message(store, "sliding", "agent-a", "still active")
    assert get_conversation(store, "sliding")["extra"]["expires_at"] == "2026-05-29T12:19:00"


def test_end_conversation_clears_messages(store):
    from kindex.coordination import (
        create_conversation,
        end_conversation,
        get_conversation,
        post_message,
    )

    create_conversation(store, "Cleanup")
    post_message(store, "cleanup", "agent-a", "transient")
    result = end_conversation(store, "cleanup", summary="Done")
    assert result["status"] == "archived"
    assert result["content"] == "Done"
    assert result["extra"]["coord_status"] == "ended"
    assert result["extra"]["message_count"] == 1
    assert result["extra"]["messages"] == []
    assert get_conversation(store, "cleanup") is None


def test_cleanup_expired_conversations(store):
    from kindex.coordination import (
        cleanup_expired_conversations,
        create_conversation,
        post_message,
    )

    create_conversation(store, "Expired", ttl_minutes=-1)
    with pytest.raises(ValueError, match="expired"):
        post_message(store, "expired", "agent-a", "too late")
    assert cleanup_expired_conversations(store) == 0


def test_reject_blank_message(store):
    from kindex.coordination import create_conversation, post_message

    create_conversation(store, "Blanks")
    with pytest.raises(ValueError, match="body is required"):
        post_message(store, "blanks", "agent-a", " ")
