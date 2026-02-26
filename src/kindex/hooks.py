"""Hook infrastructure for Claude Code integration.

Provides functions for SessionStart (prime_context), PostSession (capture_session_end),
inbox writes, and CLAUDE.md directive generation.
"""

from __future__ import annotations

import datetime
import os
import tempfile
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .store import Store
    from .budget import BudgetLedger


def prime_context(store: Store, topic: str | None = None, max_tokens: int = 750,
                  config: Config | None = None) -> str:
    """Generate compact context injection (~500-750 tokens) for SessionStart hook.

    - Auto-detects topic from current working directory if not provided
    - Uses hybrid_search to find relevant nodes
    - Formats as summarized/executive tier
    - Includes active operational nodes (constraints, watches)
    - Includes recent activity summary (what changed since yesterday)

    Returns a string suitable for CLAUDE.md injection.
    """
    from .retrieve import detect_domain_from_path, hybrid_search

    # Auto-detect topic from cwd if not provided
    if not topic:
        cwd = os.getcwd()
        domains = detect_domain_from_path(store, cwd)
        if domains:
            topic = " ".join(domains)
        else:
            # Use the directory name as a fallback search term
            topic = os.path.basename(cwd)

    # Search for relevant nodes
    results = hybrid_search(store, topic, top_k=8)

    lines: list[str] = []
    lines.append("## Kindex Context (auto-primed)")
    lines.append("")

    # Budget: roughly 3 chars per token, target ~2000-2250 chars for 750 tokens
    char_budget = max_tokens * 3
    used = sum(len(l) for l in lines)

    # -- Key concepts (summarized tier) --
    if results:
        lines.append("### Key concepts")
        for r in results[:6]:
            title = r.get("title", r["id"])
            ntype = r.get("type", "concept")
            content = (r.get("content") or "")[:120]
            edges = r.get("edges_out", [])
            connected = ", ".join(e.get("to_title", e["to_id"]) for e in edges[:3])

            entry = f"- **{title}** ({ntype})"
            if content:
                entry += f": {content}"
            if connected:
                entry += f" [{connected}]"

            if used + len(entry) + 1 > char_budget - 400:
                break
            lines.append(entry)
            used += len(entry) + 1

        lines.append("")

    # -- Active operational nodes --
    ops = store.operational_summary()

    if ops["constraints"]:
        lines.append("### Active constraints")
        for c in ops["constraints"][:3]:
            extra = c.get("extra") or {}
            action = extra.get("action", "warn")
            entry = f"- [{action}] {c['title']}"
            lines.append(entry)
            used += len(entry) + 1
        lines.append("")

    if ops["watches"]:
        lines.append("### Watches")
        for w in ops["watches"][:3]:
            extra = w.get("extra") or {}
            parts = [f"! {w['title']}"]
            if extra.get("owner"):
                parts.append(f"@{extra['owner']}")
            if extra.get("expires"):
                parts.append(f"(expires {extra['expires']})")
            entry = f"- {' '.join(parts)}"
            lines.append(entry)
            used += len(entry) + 1
        lines.append("")

    if ops["directives"]:
        lines.append("### Directives")
        for d in ops["directives"][:2]:
            scope = (d.get("extra") or {}).get("scope", "")
            entry = f"- {d['title']}"
            if scope:
                entry += f" [scope: {scope}]"
            lines.append(entry)
            used += len(entry) + 1
        lines.append("")

    # -- Recent activity summary (since yesterday) --
    yesterday = (datetime.datetime.now() - datetime.timedelta(days=1)).isoformat(timespec="seconds")
    recent = store.activity_since(yesterday)
    if recent:
        lines.append("### Recent activity (last 24h)")
        # Group by action
        action_counts: dict[str, int] = {}
        notable: list[str] = []
        for entry in recent:
            action = entry.get("action", "unknown")
            action_counts[action] = action_counts.get(action, 0) + 1
            target = entry.get("target_title") or entry.get("target_id", "")
            if target and len(notable) < 5:
                notable.append(f"{action}: {target}")

        summary_parts = [f"{count} {action}" for action, count in action_counts.items()]
        lines.append(f"- Activity: {', '.join(summary_parts)}")
        for n in notable[:3]:
            lines.append(f"  - {n}")
        lines.append("")

    # -- Active session tag --
    try:
        from .sessions import get_active_tag
        import os

        active_tag = get_active_tag(store, project_path=os.getcwd())
        if active_tag:
            extra = active_tag.get("extra") or {}
            tag_name = extra.get("tag", active_tag["title"])
            focus = extra.get("current_focus", "")
            remaining = extra.get("remaining", [])
            segments = extra.get("segments", [])

            lines.append(f"### Active session: {tag_name}")
            if focus:
                lines.append(f"**Focus:** {focus}")
            if remaining:
                lines.append(f"**Remaining:** {', '.join(remaining[:5])}")
            if segments:
                past = [s for s in segments if s.get("ended_at")]
                if past:
                    lines.append(f"**Previous segments:** {len(past)}")
                    for seg in past[-2:]:
                        lines.append(f"  - {seg['focus']}: {seg.get('summary', '')[:80]}")
            lines.append("")
    except Exception:
        pass  # Don't break priming if sessions module has issues

    # -- Due/upcoming reminders --
    try:
        if config and config.reminders.enabled:
            upcoming_window = datetime.datetime.now() + datetime.timedelta(hours=1)
            upcoming_iso = upcoming_window.isoformat(timespec="seconds")

            due_now = store.due_reminders()
            upcoming = [r for r in store.list_reminders(status="active")
                        if r["next_due"] <= upcoming_iso]
            due_ids = {d["id"] for d in due_now}
            all_reminders = due_now + [r for r in upcoming if r["id"] not in due_ids]

            if all_reminders:
                lines.append("### Reminders")
                for r in all_reminders[:5]:
                    prefix = "**DUE NOW**" if r["id"] in due_ids else "upcoming"
                    p_marker = f" [{r['priority']}]" if r.get("priority", "normal") != "normal" else ""
                    lines.append(
                        f"- {prefix}{p_marker}: {r['title']} "
                        f"(due: {r['next_due'][:16]}, id: {r['id']})"
                    )
                    lines.append(
                        f"  Use `kin remind done {r['id']}` or `kin remind snooze {r['id']}`"
                    )
                lines.append("")
    except Exception:
        pass  # Don't break priming

    return "\n".join(lines) + "\n"


def capture_session_end(
    store: Store,
    config: Config,
    ledger: BudgetLedger,
    session_text: str | None = None,
) -> int:
    """Capture discoveries at session end.

    - Extracts knowledge from the session text using the extract pipeline
    - Creates new nodes and edges automatically
    - Stores bridge_opportunities in the suggestions table
    - Returns count of items captured
    """
    if not session_text or len(session_text.strip()) < 20:
        return 0

    from .extract import extract

    existing = [n["title"] for n in store.all_nodes(limit=200)]
    extraction = extract(session_text, existing, config, ledger)

    count = 0
    created_ids: list[str] = []

    # Add extracted concepts
    for concept in extraction.get("concepts", []):
        if store.get_node_by_title(concept["title"]):
            continue
        nid = store.add_node(
            title=concept["title"],
            content=concept.get("content", ""),
            node_type=concept.get("type", "concept"),
            domains=concept.get("domains", []),
            prov_activity="session-end-hook",
            prov_source="post-session",
        )
        created_ids.append(nid)
        count += 1

    # Add extracted decisions
    for decision in extraction.get("decisions", []):
        nid = store.add_node(
            title=decision["title"],
            content=decision.get("rationale", ""),
            node_type="decision",
            prov_activity="session-end-hook",
            prov_source="post-session",
        )
        created_ids.append(nid)
        count += 1

    # Add extracted questions
    for question in extraction.get("questions", []):
        nid = store.add_node(
            title=question["question"],
            content=question.get("context", ""),
            node_type="question",
            status="open-question",
            prov_activity="session-end-hook",
            prov_source="post-session",
        )
        created_ids.append(nid)
        count += 1

    # Add connections
    for conn in extraction.get("connections", []):
        from_node = store.get_node_by_title(conn.get("from_title", ""))
        to_node = store.get_node_by_title(conn.get("to_title", ""))
        if from_node and to_node:
            store.add_edge(
                from_node["id"], to_node["id"],
                edge_type=conn.get("type", "relates_to"),
                provenance="session-end-hook",
            )
            count += 1

    # Store bridge opportunities as suggestions
    for bridge in extraction.get("bridge_opportunities", []):
        concept_a = bridge.get("concept_a", "")
        concept_b = bridge.get("concept_b", "")
        reason = bridge.get("potential_link", "")
        if concept_a and concept_b:
            store.add_suggestion(
                concept_a=concept_a,
                concept_b=concept_b,
                reason=reason,
                source="session-end-hook",
            )
            count += 1

    # Link co-created nodes to prevent orphans
    if len(created_ids) > 1:
        for i in range(len(created_ids) - 1):
            store.add_edge(created_ids[i], created_ids[i + 1],
                           provenance="co-created-session-end")

    # Link captured nodes to active session tag
    if created_ids:
        try:
            from .sessions import get_active_tag, link_node_to_tag
            import os

            active_tag = get_active_tag(store, project_path=os.getcwd())
            if active_tag:
                tag_name = (active_tag.get("extra") or {}).get("tag", active_tag["title"])
                for nid in created_ids:
                    link_node_to_tag(store, tag_name, nid)
        except Exception:
            pass  # Don't break session end capture

    return count


def write_inbox_item(
    config: Config,
    content: str,
    source: str = "",
    topic_hint: str = "",
) -> Path:
    """Write an inbox item to the inbox directory.

    Uses atomic writes (write to tmp then rename).
    Format: YAML frontmatter + markdown body.

    Returns the path to the created file.
    """
    inbox_dir = config.inbox_dir
    inbox_dir.mkdir(parents=True, exist_ok=True)

    # Generate a unique filename
    ts = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    uid = uuid.uuid4().hex[:6]
    filename = f"{ts}-{uid}.md"
    target = inbox_dir / filename

    # Build YAML frontmatter
    frontmatter_lines = [
        "---",
        f"created: {datetime.datetime.now().isoformat(timespec='seconds')}",
    ]
    if source:
        frontmatter_lines.append(f"source: {source}")
    if topic_hint:
        frontmatter_lines.append(f"topic_hint: {topic_hint}")
    frontmatter_lines.append("processed: false")
    frontmatter_lines.append("---")

    file_content = "\n".join(frontmatter_lines) + "\n\n" + content + "\n"

    # Atomic write: write to tmp file then rename
    tmp_dir = config.tmp_dir
    tmp_dir.mkdir(parents=True, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(dir=str(tmp_dir), suffix=".md", prefix="inbox-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(file_content)
        # Atomic rename (same filesystem)
        os.rename(tmp_path, str(target))
    except Exception:
        # Cleanup on failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return target


def generate_session_directive(store: Store) -> str:
    """Generate CLAUDE.md text that instructs Claude Code to write back discoveries.

    Returns markdown string with instructions for capturing knowledge during sessions.
    """
    lines = [
        "## Kindex: Knowledge Capture Directives",
        "",
        "During this session, capture discoveries into the knowledge graph:",
        "",
        "### Adding new concepts",
        "When you discover a new concept, pattern, or technique:",
        "```bash",
        'kin add "concept title and brief description" --type concept',
        "```",
        "",
        "### Linking related ideas",
        "When you find connections between ideas or concepts:",
        "```bash",
        'kin link "Source Concept" "Target Concept" relates_to --why "reason for connection"',
        "```",
        "",
        "### Recording decisions",
        "When a decision is made with rationale:",
        "```bash",
        'kin add "What was decided and why" --type decision',
        "```",
        "",
        "### Capturing open questions",
        "When an unresolved question emerges:",
        "```bash",
        'kin add "The open question" --type question',
        "```",
        "",
        "### Operational nodes",
        "For constraints, watches, or directives:",
        "```bash",
        'kin add "Rule or constraint text" --type constraint --trigger pre-commit --action warn',
        'kin add "Thing to watch" --type watch --owner <user> --expires YYYY-MM-DD',
        "```",
        "",
        "### Session-end capture",
        "Before session ends, capture key learnings:",
        "```bash",
        "kin compact-hook --text 'Summary of key discoveries and decisions from this session'",
        "```",
        "",
    ]

    # Add current graph context summary
    stats = store.stats()
    if stats["nodes"] > 0:
        lines.append(f"*Current graph: {stats['nodes']} nodes, {stats['edges']} edges.*")

        # Show pending suggestions count
        pending = store.pending_suggestions(limit=1)
        if pending:
            lines.append(f"*Run `kin suggest` to review bridge opportunities.*")
        lines.append("")

    return "\n".join(lines) + "\n"
