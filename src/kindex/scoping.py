"""Conversation/chat scoping helpers for hook-visible state."""

from __future__ import annotations

from typing import Any


CONVERSATION_ID_KEYS = (
    "conversation_id",
    "conversationId",
    "chat_id",
    "chatId",
    "session_id",
    "sessionId",
    "thread_id",
    "threadId",
)

SCOPE_KEYS = (
    "conversation_scope",
    "reminder_scope",
    "scope",
)

GLOBAL_SCOPES = {"global", "all"}
CHAT_SCOPES = {"chat", "conversation", "session", "thread"}


def extra_conversation_id(extra: dict[str, Any] | None) -> str:
    """Return the scoped conversation id carried in an item's extra JSON."""
    if not extra:
        return ""
    for key in CONVERSATION_ID_KEYS:
        value = extra.get(key)
        if value:
            return str(value)
    return ""


def extra_scope(extra: dict[str, Any] | None) -> str:
    """Return the explicit scope marker carried in an item's extra JSON."""
    if not extra:
        return ""
    for key in SCOPE_KEYS:
        value = extra.get(key)
        if value:
            return str(value).strip().lower()
    return ""


def extra_matches_conversation(
    extra: dict[str, Any] | None,
    conversation_id: str | None,
    *,
    include_global: bool = True,
    include_legacy: bool = False,
) -> bool:
    """Check whether scoped metadata belongs in the current conversation.

    Legacy items are records with neither an explicit conversation id nor an
    explicit global/chat scope. Hook injection callers should usually keep
    include_legacy=False to avoid cross-chat bleed from old data.
    """
    scoped_id = extra_conversation_id(extra)
    if scoped_id:
        return bool(conversation_id) and scoped_id == str(conversation_id)

    scope = extra_scope(extra)
    if scope in GLOBAL_SCOPES:
        return include_global
    if scope in CHAT_SCOPES:
        return False

    return include_legacy


def item_matches_conversation(
    item: dict[str, Any],
    conversation_id: str | None,
    *,
    include_global: bool = True,
    include_legacy: bool = False,
) -> bool:
    """Check an item dict with an ``extra`` payload against a conversation id."""
    return extra_matches_conversation(
        item.get("extra") or {},
        conversation_id,
        include_global=include_global,
        include_legacy=include_legacy,
    )
