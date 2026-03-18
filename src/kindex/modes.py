"""Conversation mode management for Kindex.

Modes are reusable conversation-priming artifacts that induce a processing
mode in an AI session. Each mode has a primer (state induction), a boundary
(quality definition), and permissions (what's explicitly allowed).

Modes can be created from session fingerprints, activated for new sessions,
and exported for sharing across users (PII-free).

Research basis (Papers 35-40):
- Induced understanding outperforms direct instruction by 5.4x
- 15 tokens of priming captures 98.8% of achievable benefit
- Adaptive guidance is actively harmful
- Cross-domain shifts improve quality
- Reset + prime beats expert baseline by 39%
"""

from __future__ import annotations

import datetime
import json
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from .store import Store

# ── Default modes ────────────────────────────────────────────────────
# Generated from conversation analysis: 116 conversations, engagement/decay
# metrics, correlation with activation geometry research findings.

DEFAULT_MODES = {
    "collaborate": {
        "primer": (
            "This is a workspace where thinking happens, not a request-response "
            "loop. You do the heavy lifting — surface what I haven't seen, push "
            "back when I'm wrong, flag vague claims as vague. Sycophancy ends the "
            "conversation faster than disagreement does. Minimal steering from me "
            "means I'm watching whether you generate actual movement, not competent "
            "elaboration. Domain shifts are welcome; they reset the mode."
        ),
        "boundary": (
            "Quality means the output contains something I didn't already think — "
            "a reframe, a real objection, a distinction that changes the shape of "
            "the problem. Reasoning must be visible and the epistemic status must "
            "be honest: what you know versus what you're constructing. Precision is "
            "not optional; vague claims need to be named as such."
        ),
        "permissions": (
            "Follow any tangent, change direction mid-thought, say \"I don't know,\" "
            "challenge the premise, or abandon a line of reasoning if a better one "
            "surfaces — whatever keeps the thinking alive."
        ),
        "description": "Open-ended collaborative thinking. Multi-domain workspace.",
    },
    "code": {
        "primer": (
            "We're building together. I describe what needs to exist — the constraints, "
            "the interfaces, what correct looks like. You figure out how to get there. "
            "Don't over-engineer: no factory for one product, no interface for one "
            "implementation, no abstraction for one use. If the infrastructure already "
            "provides the semantics, don't rebuild them. Ship, then iterate."
        ),
        "boundary": (
            "The code must work. Tests must test what matters, not what's easy to test. "
            "If you're unsure about a design decision, say so — don't hide uncertainty "
            "behind a pattern. Favor the simplest thing that satisfies the constraint "
            "over the most extensible thing that might satisfy future constraints."
        ),
        "permissions": (
            "Propose alternatives I didn't ask for. Push back on over-specification. "
            "Say \"this abstraction isn't earning its keep\" or \"who's the second "
            "consumer of this?\" when you see it."
        ),
        "description": "Focused coding and architecture. Constraint-first, ship-oriented.",
    },
    "create": {
        "primer": (
            "This is creative work. The bar is not correctness — it's resonance. "
            "We're looking for the phrase that lands, the structure that reveals, "
            "the framing that makes the reader see something they already knew but "
            "hadn't articulated. Drafts are cheap. Precision in language is expensive "
            "and worth the cost."
        ),
        "boundary": (
            "Quality means compression without loss of meaning. Every word must earn "
            "its place. If a sentence can be cut without changing what the paragraph "
            "communicates, cut it. If a paragraph can be replaced by its last sentence, "
            "replace it. Show, don't tell — but know when telling is the braver choice."
        ),
        "permissions": (
            "Try things that might not work. Offer a line that's too sharp and let me "
            "sand it. Disagree with my word choices. Say \"this section is doing two "
            "things and should do one.\""
        ),
        "description": "Writing, editing, creative work. Compression and resonance.",
    },
    "research": {
        "primer": (
            "We're investigating. I don't know the answer yet and neither do you — "
            "that's the point. Follow the evidence, not the hypothesis. If the data "
            "contradicts the expectation, the expectation is what changes. Surface "
            "connections I might miss, especially cross-domain ones. The interesting "
            "findings are usually the surprising ones."
        ),
        "boundary": (
            "Distinguish what the evidence shows from what you're inferring. Name your "
            "confidence level. If you're pattern-matching from training data rather than "
            "reasoning from the specific evidence in front of us, say so. I need to know "
            "where the ground is solid and where it's conjecture."
        ),
        "permissions": (
            "Speculate freely but label speculation. Follow tangents that connect to "
            "the investigation even if they seem unrelated at first. Say \"this reminds "
            "me of\" even if the connection is loose — the loose ones are often the most "
            "productive."
        ),
        "description": "Exploratory research and analysis. Evidence-first, hypothesis-flexible.",
    },
    "chat": {
        "primer": (
            "Casual. No agenda. I might be thinking out loud, venting, or just curious "
            "about something. Match the energy — if I'm brief, be brief. If I'm expansive, "
            "engage. This isn't a work session and doesn't need to produce an artifact."
        ),
        "boundary": (
            "Be honest. Don't perform helpfulness. If I say something interesting, "
            "engage with it. If I say something wrong, say so. The bar is genuine "
            "conversation, not service."
        ),
        "permissions": (
            "Be funny if something's funny. Be direct. Don't summarize what I just said. "
            "Treat this like a conversation between equals, not a consultation."
        ),
        "description": "Casual conversation. No deliverable, no agenda.",
    },
}


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


# ── CRUD ─────────────────────────────────────────────────────────────


def create_mode(
    store: Store,
    name: str,
    *,
    primer: str,
    boundary: str,
    permissions: str,
    description: str = "",
    link_to: list[str] | None = None,
) -> str:
    """Create a mode node in the graph."""
    extra = {
        "mode_status": "active",
        "primer": primer,
        "boundary": boundary,
        "permissions": permissions,
        "created_at": _now(),
        "session_count": 0,
        "last_activated": None,
    }

    mode_id = store.add_node(
        title=f"mode:{name}",
        content=description or f"Conversation mode: {name}",
        node_type="concept",  # Modes are concepts with mode: prefix
        weight=0.5,
        extra=extra,
        domains=["mode"],
    )

    if link_to:
        for ref in link_to:
            target = store.get_node(ref)
            if not target:
                # Try title match
                target = store.get_node_by_title(ref)
            if not target:
                # Try FTS
                results = store.fts_search(ref, limit=1)
                if results:
                    target = results[0]
            if target:
                store.add_edge(
                    mode_id, target["id"], "context_of",
                    weight=0.5, provenance="mode-creation",
                )

    return mode_id


def get_mode(store: Store, name: str) -> dict | None:
    """Get a mode by name (with or without mode: prefix)."""
    lookup = name if name.startswith("mode:") else f"mode:{name}"
    # Try direct title lookup
    result = store.get_node_by_title(lookup)
    if result:
        return result
    # Fallback: try as node ID
    return store.get_node(name)


def list_modes(store: Store) -> list[dict]:
    """List all modes."""
    # Get all nodes tagged with 'mode' domain
    results = store.all_nodes(tags=["mode"], limit=50)
    return [r for r in results if r.get("title", "").startswith("mode:")]


def activate_mode(store: Store, name: str, session_context: str | None = None) -> str:
    """Generate the injection artifact for a mode.

    Returns the text to inject at the start of a new AI session.
    Structure: reset boundary, primer, boundary, permissions, optional session context.
    Research basis (Paper 39): reset before prime beats expert baseline by 39%.
    """
    mode = get_mode(store, name)

    # Check defaults if not in graph
    if not mode:
        default = DEFAULT_MODES.get(name)
        if not default:
            return f"Mode not found: {name}"
        primer = default["primer"]
        boundary = default["boundary"]
        permissions = default["permissions"]
    else:
        extra = mode.get("extra") or {}
        primer = extra.get("primer", "")
        boundary = extra.get("boundary", "")
        permissions = extra.get("permissions", "")

        # Update activation stats
        extra["session_count"] = extra.get("session_count", 0) + 1
        extra["last_activated"] = _now()
        store.update_node(mode["id"], extra=extra)

    parts = ["---", primer, boundary, permissions]

    if session_context:
        parts.append("---")
        parts.append("Prior context (structural residue, not transcript):")
        parts.append(session_context)

    return "\n\n".join(parts)


def seed_defaults(store: Store) -> list[str]:
    """Seed the default modes into the graph. Idempotent."""
    created = []
    for name, spec in DEFAULT_MODES.items():
        existing = get_mode(store, name)
        if existing:
            continue
        mode_id = create_mode(
            store, name,
            primer=spec["primer"],
            boundary=spec["boundary"],
            permissions=spec["permissions"],
            description=spec["description"],
        )
        created.append(name)
    return created


def export_mode(store: Store, name: str) -> dict | None:
    """Export a mode as a PII-free portable artifact."""
    mode = get_mode(store, name)
    if not mode:
        default = DEFAULT_MODES.get(name)
        if not default:
            return None
        return {
            "name": name,
            "primer": default["primer"],
            "boundary": default["boundary"],
            "permissions": default["permissions"],
            "description": default["description"],
            "version": 1,
        }

    extra = mode.get("extra") or {}
    return {
        "name": name.replace("mode:", ""),
        "primer": extra.get("primer", ""),
        "boundary": extra.get("boundary", ""),
        "permissions": extra.get("permissions", ""),
        "description": mode.get("content", ""),
        "version": 1,
    }


def import_mode(store: Store, artifact: dict) -> str:
    """Import a mode from a portable artifact."""
    name = artifact.get("name", "")
    if not name:
        raise ValueError("Artifact missing 'name' field")

    existing = get_mode(store, name)
    if existing:
        # Update existing
        extra = existing.get("extra") or {}
        extra["primer"] = artifact.get("primer", extra.get("primer", ""))
        extra["boundary"] = artifact.get("boundary", extra.get("boundary", ""))
        extra["permissions"] = artifact.get("permissions", extra.get("permissions", ""))
        store.update_node(existing["id"], extra=extra,
                         content=artifact.get("description", existing.get("content", "")))
        return existing["id"]

    return create_mode(
        store, name,
        primer=artifact.get("primer", ""),
        boundary=artifact.get("boundary", ""),
        permissions=artifact.get("permissions", ""),
        description=artifact.get("description", ""),
    )


def format_mode_list(modes: list[dict], defaults: dict | None = None) -> str:
    """Format modes for display."""
    lines = []

    # Show graph-stored modes
    for m in modes:
        extra = m.get("extra") or {}
        name = m["title"].replace("mode:", "")
        sessions = extra.get("session_count", 0)
        last = extra.get("last_activated", "never")
        if last and last != "never":
            last = last[:10]
        lines.append(f"  {name:<15} {sessions:>3} sessions  last: {last}")

    # Show defaults not yet in graph
    if defaults:
        stored_names = {m["title"].replace("mode:", "") for m in modes}
        for name, spec in defaults.items():
            if name not in stored_names:
                lines.append(f"  {name:<15}   — default  {spec['description']}")

    if not lines:
        return "No modes found."
    return "Modes:\n" + "\n".join(lines)


def format_mode_detail(name: str, mode: dict | None = None,
                       default: dict | None = None) -> str:
    """Format a single mode for display."""
    if mode:
        extra = mode.get("extra") or {}
        primer = extra.get("primer", "")
        boundary = extra.get("boundary", "")
        permissions = extra.get("permissions", "")
        desc = mode.get("content", "")
        sessions = extra.get("session_count", 0)
    elif default:
        primer = default["primer"]
        boundary = default["boundary"]
        permissions = default["permissions"]
        desc = default["description"]
        sessions = 0
    else:
        return f"Mode not found: {name}"

    return (
        f"Mode: {name}\n"
        f"Description: {desc}\n"
        f"Sessions: {sessions}\n"
        f"\n--- Primer ---\n{primer}\n"
        f"\n--- Boundary ---\n{boundary}\n"
        f"\n--- Permissions ---\n{permissions}"
    )
