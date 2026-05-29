"""Short-lived coordination state for agents.

Coordination nodes are operational state, not durable knowledge. They live in
the same Store so MCP clients and CLI users can share them, but cleanup/end
paths clear message bodies and archive expired conversations.
"""

from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store

_DEFAULT_TTL_MINUTES = 240


def _now_dt() -> datetime.datetime:
    return datetime.datetime.now()


def _now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _expires_at(ttl_minutes: int | None) -> str:
    ttl = _DEFAULT_TTL_MINUTES if ttl_minutes is None else ttl_minutes
    return (_now_dt() + datetime.timedelta(minutes=ttl)).isoformat(timespec="seconds")


def _ttl_from_extra(extra: dict) -> int:
    ttl = extra.get("ttl_minutes")
    if isinstance(ttl, int):
        return ttl

    # Legacy conversations did not store ttl_minutes. Preserve the configured
    # lifetime when it can be inferred from the original timestamps.
    try:
        created = datetime.datetime.fromisoformat(extra.get("created_at", ""))
        expires = datetime.datetime.fromisoformat(extra.get("expires_at", ""))
        inferred = int((expires - created).total_seconds() / 60)
        if inferred > 0:
            return inferred
    except (TypeError, ValueError):
        pass
    return _DEFAULT_TTL_MINUTES


def _slug(name: str) -> str:
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    return name.strip("-")


def _is_expired(value: str | None) -> bool:
    if not value:
        return False
    try:
        return datetime.datetime.fromisoformat(value) <= _now_dt()
    except ValueError:
        return False


def create_conversation(
    store: Store,
    name: str,
    *,
    task_id: str | None = None,
    ttl_minutes: int = 240,
    project_path: str | None = None,
    created_by: str = "",
) -> str:
    """Create a short-lived coordination conversation."""
    conv_name = _slug(name)
    if not conv_name:
        raise ValueError("Conversation name cannot be empty")

    extra = {
        "coord_kind": "conversation",
        "coord_status": "active",
        "name": conv_name,
        "task_id": task_id or "",
        "project_path": project_path or "",
        "created_by": created_by,
        "created_at": _now(),
        "ttl_minutes": ttl_minutes,
        "expires_at": _expires_at(ttl_minutes),
        "messages": [],
    }
    conv_id = store.add_node(
        conv_name,
        node_type="coordination",
        content="Short-lived agent coordination state.",
        weight=0.01,
        prov_activity="coordination",
        prov_source=project_path or "",
        prov_who=[created_by] if created_by else [],
        extra=extra,
    )
    if task_id and store.get_node(task_id):
        store.add_edge(conv_id, task_id, "context_of", weight=0.4)
    return conv_id


def get_conversation(store: Store, ref: str) -> dict | None:
    """Find a coordination conversation by id or normalized name."""
    node = store.get_node(ref)
    if node and node.get("type") == "coordination":
        return node

    name = _slug(ref)
    rows = store.all_nodes(node_type="coordination", status="active", limit=500)
    for row in rows:
        extra = row.get("extra") or {}
        if (
            extra.get("coord_kind") == "conversation"
            and extra.get("name") == name
        ):
            return row
    return None


def list_conversations(
    store: Store,
    *,
    status: str = "active",
    project_path: str | None = None,
    task_id: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """List coordination conversations."""
    node_status = "active" if status == "active" else None
    rows = store.all_nodes(node_type="coordination", status=node_status, limit=500)
    result = []
    for row in rows:
        extra = row.get("extra") or {}
        if extra.get("coord_kind") != "conversation":
            continue
        if status != "all" and extra.get("coord_status") != status:
            continue
        if project_path and extra.get("project_path") != project_path:
            continue
        if task_id and extra.get("task_id") != task_id:
            continue
        result.append(row)
    result.sort(key=lambda r: (r.get("updated_at") or ""), reverse=True)
    return result[:limit]


def post_message(
    store: Store,
    conversation: str,
    author: str,
    body: str,
    *,
    ttl_minutes: int | None = None,
) -> dict:
    """Append a message to a live coordination conversation."""
    if not author.strip():
        raise ValueError("Message author is required")
    if not body.strip():
        raise ValueError("Message body is required")
    node = get_conversation(store, conversation)
    if not node:
        raise ValueError(f"Conversation not found: {conversation}")

    extra = dict(node.get("extra") or {})
    if extra.get("coord_status") != "active":
        raise ValueError(f"Conversation is not active: {conversation}")
    if _is_expired(extra.get("expires_at")):
        end_conversation(store, node["id"], summary="Expired before post")
        raise ValueError(f"Conversation expired: {conversation}")

    messages = list(extra.get("messages") or [])
    message = {
        "id": len(messages) + 1,
        "at": _now(),
        "author": author.strip(),
        "body": body.strip(),
    }
    messages.append(message)
    extra["messages"] = messages
    ttl = ttl_minutes if ttl_minutes is not None else _ttl_from_extra(extra)
    extra["ttl_minutes"] = ttl
    extra["expires_at"] = _expires_at(ttl)
    store.update_node(node["id"], extra=extra)
    return message


def read_messages(
    store: Store,
    conversation: str,
    *,
    since_id: int = 0,
    limit: int = 50,
) -> dict:
    """Read messages from a coordination conversation."""
    node = get_conversation(store, conversation)
    if not node:
        raise ValueError(f"Conversation not found: {conversation}")
    extra = node.get("extra") or {}
    messages = [
        m for m in extra.get("messages", [])
        if int(m.get("id", 0)) > since_id
    ]
    return {
        "id": node["id"],
        "name": extra.get("name", node["title"]),
        "status": extra.get("coord_status", "active"),
        "task_id": extra.get("task_id", ""),
        "expires_at": extra.get("expires_at", ""),
        "messages": messages[-limit:],
    }


def end_conversation(store: Store, conversation: str, *, summary: str = "") -> dict | None:
    """Archive a coordination conversation and clear transient message bodies."""
    node = get_conversation(store, conversation)
    if not node:
        return None
    extra = dict(node.get("extra") or {})
    extra["coord_status"] = "ended"
    extra["ended_at"] = _now()
    extra["message_count"] = len(extra.get("messages") or [])
    extra["messages"] = []
    content = summary or node.get("content") or "Ended coordination conversation."
    store.update_node(node["id"], status="archived", content=content, extra=extra)
    return store.get_node(node["id"])


def cleanup_expired_conversations(store: Store) -> int:
    """Archive expired coordination conversations and clear messages."""
    count = 0
    for node in list_conversations(store, status="active", limit=500):
        extra = node.get("extra") or {}
        if _is_expired(extra.get("expires_at")):
            end_conversation(store, node["id"], summary="Expired coordination conversation.")
            count += 1
    return count


def format_conversations(conversations: list[dict]) -> str:
    if not conversations:
        return "No coordination conversations found."
    lines = []
    for conv in conversations:
        extra = conv.get("extra") or {}
        messages = extra.get("messages") or []
        task = f" task:{extra.get('task_id')[:12]}" if extra.get("task_id") else ""
        lines.append(
            f"  [{extra.get('coord_status', '?')}] {extra.get('name', conv['title'])}"
            f"{task} messages:{len(messages)} expires:{extra.get('expires_at', '')}"
            f" {conv['id'][:12]}"
        )
    return "\n".join(lines)


def format_messages(payload: dict) -> str:
    messages = payload.get("messages") or []
    if not messages:
        return "No messages."
    lines = [
        f"Conversation: {payload.get('name')} ({payload.get('status')})",
        f"Expires: {payload.get('expires_at', '')}",
    ]
    for msg in messages:
        lines.append(
            f"  #{msg.get('id')} {msg.get('at')} {msg.get('author')}: {msg.get('body')}"
        )
    return "\n".join(lines)
