"""Session tag management — named work context handles for resumable sessions.

Provides lifecycle management for session tags: start, update, segment,
pause, resume, complete. Session tags are stored as session-type nodes
with structured metadata in the extra JSON field.
"""

from __future__ import annotations

import datetime
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .store import Store


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _normalize_tag(name: str) -> str:
    """Normalize a tag name: lowercase, hyphens for spaces, strip special chars."""
    name = name.strip().lower()
    name = re.sub(r"[^a-z0-9\s-]", "", name)
    name = re.sub(r"[\s_]+", "-", name)
    return name.strip("-")


def get_tag(store: Store, name: str) -> dict | None:
    """Look up a session tag by name. Returns the node dict or None."""
    tag = store.get_session_tag_by_name(_normalize_tag(name))
    if tag:
        return tag
    # Also try unnormalized (in case title was stored differently)
    return store.get_session_tag_by_name(name)


def get_active_tag(store: Store, project_path: str | None = None) -> dict | None:
    """Find the currently active session tag, optionally scoped to a project path."""
    tags = store.get_session_tags(status="active", project_path=project_path, limit=1)
    return tags[0] if tags else None


def list_tags(
    store: Store,
    status: str | None = None,
    project_path: str | None = None,
    limit: int = 20,
) -> list[dict]:
    """List session tags with optional filters."""
    return store.get_session_tags(
        status=status, project_path=project_path, limit=limit
    )


def start_tag(
    store: Store,
    name: str,
    *,
    description: str = "",
    focus: str = "",
    remaining: list[str] | None = None,
    project_path: str | None = None,
    prov_who: list[str] | None = None,
) -> str:
    """Create a new session tag. Returns the node ID.

    Raises ValueError if a tag with that name already exists and is active.
    """
    tag_name = _normalize_tag(name)
    if not tag_name:
        raise ValueError("Tag name cannot be empty")

    existing = get_tag(store, tag_name)
    if existing:
        extra = existing.get("extra") or {}
        if extra.get("session_status") == "active":
            raise ValueError(f"Active tag already exists: {tag_name}")

    now = _now()
    segments = []
    if focus:
        segments.append(
            {
                "focus": focus,
                "started_at": now,
                "ended_at": None,
                "summary": "",
                "decisions": [],
                "artifacts": [],
            }
        )

    extra = {
        "tag": tag_name,
        "session_status": "active",
        "project_path": project_path or "",
        "started_at": now,
        "paused_at": None,
        "completed_at": None,
        "current_focus": focus,
        "remaining": remaining or [],
        "segments": segments,
        "linked_nodes": [],
    }

    nid = store.add_node(
        title=tag_name,
        content=description,
        node_type="session",
        prov_activity="session-tag",
        prov_source=project_path or "",
        prov_who=prov_who or [],
        extra=extra,
    )
    return nid


def update_tag(
    store: Store,
    name: str,
    *,
    description: str | None = None,
    focus: str | None = None,
    remaining: list[str] | None = None,
    append_remaining: list[str] | None = None,
    remove_remaining: list[str] | None = None,
) -> None:
    """Update the current state of a session tag.

    The shared tag node is mutated by every agent in a project, so the
    extra changes go through Store.atomic_extra_update (fresh read inside
    BEGIN IMMEDIATE — no lost updates from stale snapshots).
    """
    tag = get_tag(store, name)
    if not tag:
        raise ValueError(f"Tag not found: {name}")

    def _mutate(extra: dict) -> None:
        if focus is not None:
            extra["current_focus"] = focus
            # Also update the current open segment's focus
            segments = extra.get("segments", [])
            if segments:
                current = [s for s in segments if not s.get("ended_at")]
                if current:
                    current[-1]["focus"] = focus

        if remaining is not None:
            extra["remaining"] = remaining

        if append_remaining:
            extra["remaining"] = extra.get("remaining", []) + append_remaining

        if remove_remaining:
            extra["remaining"] = [
                r for r in extra.get("remaining", [])
                if r not in remove_remaining
            ]

    store.atomic_extra_update(tag["id"], _mutate)
    if description is not None:
        store.update_node(tag["id"], content=description)


def add_segment(
    store: Store,
    name: str,
    *,
    new_focus: str,
    summary: str = "",
    decisions: list[str] | None = None,
) -> None:
    """Close the current segment and start a new one.

    Runs inside Store.atomic_extra_update: a node linked (or a focus set)
    by another agent between this caller's read and write must survive.
    """
    tag = get_tag(store, name)
    if not tag:
        raise ValueError(f"Tag not found: {name}")

    now = _now()

    def _mutate(extra: dict) -> None:
        segments = extra.setdefault("segments", [])

        # Close the current open segment
        for seg in segments:
            if not seg.get("ended_at"):
                seg["ended_at"] = now
                if summary:
                    seg["summary"] = summary
                if decisions:
                    seg["decisions"] = seg.get("decisions", []) + decisions

        # Start new segment
        segments.append(
            {
                "focus": new_focus,
                "started_at": now,
                "ended_at": None,
                "summary": "",
                "decisions": [],
                "artifacts": [],
            }
        )

        extra["current_focus"] = new_focus

    store.atomic_extra_update(tag["id"], _mutate)


def link_node_to_tag(store: Store, tag_name: str, node_id: str) -> None:
    """Associate a knowledge node with a session tag.

    Hooks auto-link every captured node to the project's active tag, so
    multiple agents race on this node: the mutation runs inside
    Store.atomic_extra_update to avoid losing concurrent links/segments.
    """
    tag = get_tag(store, tag_name)
    if not tag:
        return

    def _mutate(extra: dict) -> None:
        linked = extra.setdefault("linked_nodes", [])
        if node_id in linked:
            return
        linked.append(node_id)

        # Also update current segment's artifacts
        for seg in extra.get("segments", []):
            if not seg.get("ended_at"):
                artifacts = seg.setdefault("artifacts", [])
                if node_id not in artifacts:
                    artifacts.append(node_id)

    store.atomic_extra_update(tag["id"], _mutate)

    # Create a context_of edge from the node to the session tag
    try:
        store.add_edge(node_id, tag["id"], edge_type="context_of", provenance="session-tag")
    except Exception:
        pass  # Edge may already exist


def pause_tag(store: Store, name: str, *, summary: str = "") -> None:
    """Pause a session tag, marking it as suspended."""
    tag = get_tag(store, name)
    if not tag:
        raise ValueError(f"Tag not found: {name}")

    def _mutate(extra: dict) -> None:
        extra["session_status"] = "paused"
        extra["paused_at"] = _now()

        if summary:
            # Update current segment summary
            for seg in extra.get("segments", []):
                if not seg.get("ended_at"):
                    seg["summary"] = summary

    store.atomic_extra_update(tag["id"], _mutate)


def complete_tag(store: Store, name: str, *, summary: str = "") -> None:
    """Mark a session tag as completed."""
    tag = get_tag(store, name)
    if not tag:
        raise ValueError(f"Tag not found: {name}")

    now = _now()

    def _mutate(extra: dict) -> None:
        extra["session_status"] = "completed"
        extra["completed_at"] = now

        # Close any open segments
        for seg in extra.get("segments", []):
            if not seg.get("ended_at"):
                seg["ended_at"] = now
                if summary:
                    seg["summary"] = summary

    store.atomic_extra_update(tag["id"], _mutate)


def format_resume_context(
    store: Store,
    name: str,
    max_tokens: int = 1500,
) -> str:
    """Generate a context block for resuming a session tag.

    Returns a markdown string suitable for injection into a new session.
    """
    tag = get_tag(store, name)
    if not tag:
        return f"Session tag not found: {name}"

    extra = tag.get("extra") or {}
    tag_name = extra.get("tag", tag["title"])
    status = extra.get("session_status", "unknown")
    focus = extra.get("current_focus", "")
    remaining = extra.get("remaining", [])
    segments = extra.get("segments", [])
    linked_nodes = extra.get("linked_nodes", [])
    description = tag.get("content", "")
    project_path = extra.get("project_path", "")

    lines = [f"## Session: {tag_name}"]
    lines.append(f"**Status:** {status}")
    if project_path:
        lines.append(f"**Project:** {project_path}")
    if description:
        lines.append(f"**Description:** {description}")
    if focus:
        lines.append(f"**Current focus:** {focus}")
    lines.append("")

    if remaining:
        lines.append("### Remaining")
        for item in remaining:
            lines.append(f"- {item}")
        lines.append("")

    # Segments: show recent in detail, older as one-liners
    if segments:
        lines.append("### Segments")
        completed = [s for s in segments if s.get("ended_at")]
        current = [s for s in segments if not s.get("ended_at")]

        # Show older segments briefly
        for seg in completed:
            summary_text = seg.get("summary", "")
            focus_text = seg.get("focus", "")
            decisions = seg.get("decisions", [])
            line = f"- **{focus_text}**"
            if summary_text:
                line += f": {summary_text[:100]}"
            if decisions:
                line += f" (decisions: {', '.join(decisions[:3])})"
            lines.append(line)

        # Show current segment in full
        for seg in current:
            lines.append(f"- **{seg.get('focus', '')}** (active)")
            if seg.get("summary"):
                lines.append(f"  {seg['summary']}")
        lines.append("")

    # Linked knowledge nodes: show titles
    if linked_nodes:
        lines.append("### Related knowledge")
        shown = 0
        for nid in linked_nodes:
            if shown >= 10:
                lines.append(f"  ... and {len(linked_nodes) - shown} more")
                break
            node = store.get_node(nid)
            if node:
                lines.append(f"- {node['title']} ({node['type']})")
                shown += 1
        lines.append("")

    return "\n".join(lines)
