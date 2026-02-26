"""Background ingestion — one-shot cron mode and file change detection."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .store import Store


def cron_run(config: "Config", store: "Store", verbose: bool = False) -> dict:
    """One-shot run of all maintenance tasks. Designed for crontab.

    Steps:
    1. Ingest new projects (scan for CLAUDE.md files)
    2. Ingest new sessions (scan ~/.claude/projects/ for JSONL)
    3. Process inbox items
    4. Apply weight decay
    5. Run doctor checks

    Returns dict of results.
    """
    from .ingest import scan_kin_files, scan_projects, scan_sessions

    results: dict = {}

    # 1. Ingest new projects
    if verbose:
        print("Scanning projects...")
    proj_count = scan_projects(config, store, verbose=verbose)
    kin_count = scan_kin_files(config, store, verbose=verbose)
    results["projects"] = proj_count
    results["kin_updates"] = kin_count

    # 2. Ingest new sessions
    if verbose:
        print("Scanning sessions...")
    session_count = scan_sessions(config, store, limit=50, verbose=verbose)
    results["sessions"] = session_count

    # 3. Process inbox items
    inbox_count = _process_inbox(config, store, verbose=verbose)
    results["inbox"] = inbox_count

    # 4. Apply weight decay
    if verbose:
        print("Applying weight decay...")
    decay_count = store.apply_weight_decay()
    results["decayed"] = decay_count

    # 5. Run doctor checks
    if verbose:
        print("Running health checks...")
    stats = store.stats()
    orphans = store.orphans()
    results["stats"] = stats
    results["orphan_count"] = len(orphans)

    # 6. Check reminders
    reminder_results = _check_reminders(config, store, verbose=verbose)
    results["reminders_fired"] = reminder_results.get("fired", 0)
    results["reminders_auto_snoozed"] = reminder_results.get("auto_snoozed", 0)

    # Update run marker
    set_run_marker(store)

    return results


def _process_inbox(config: "Config", store: "Store", verbose: bool = False) -> int:
    """Process pending inbox items (markdown files in inbox/)."""
    inbox = config.inbox_dir
    if not inbox.exists():
        return 0

    count = 0
    for md_file in sorted(inbox.glob("*.md")):
        try:
            text = md_file.read_text(errors="replace").strip()
        except OSError:
            continue

        if not text or len(text) < 10:
            continue

        from .extract import keyword_extract

        extraction = keyword_extract(text)
        concepts = extraction.get("concepts", [])

        for concept in concepts:
            existing = store.get_node_by_title(concept["title"])
            if existing:
                continue
            store.add_node(
                title=concept["title"],
                content=concept.get("content", text[:500]),
                node_type=concept.get("type", "concept"),
                domains=concept.get("domains", []),
                prov_activity="inbox-ingest",
                prov_source=str(md_file),
            )
            count += 1
            if verbose:
                print(f"  Inbox: {concept['title']}")

        # Move processed file to .processed
        processed_dir = inbox / ".processed"
        processed_dir.mkdir(exist_ok=True)
        md_file.rename(processed_dir / md_file.name)

    return count


def _check_reminders(config: "Config", store: "Store", verbose: bool = False) -> dict:
    """Run the reminder check cycle."""
    if not config.reminders.enabled:
        return {"fired": 0, "auto_snoozed": 0}

    from .reminders import auto_snooze_stale, check_and_fire

    fired = check_and_fire(store, config)
    auto_snoozed = auto_snooze_stale(store, config)

    if verbose and fired:
        for r in fired:
            print(f"  Fired reminder: {r['title']}")

    return {"fired": len(fired), "auto_snoozed": auto_snoozed}


def last_run_marker(config: "Config") -> str:
    """Read last cron run timestamp from meta table.

    Returns ISO timestamp string, or empty string if never run.
    """
    from .store import Store

    store = Store(config)
    ts = store.get_meta("last_cron_run")
    store.close()
    return ts or ""


def set_run_marker(store: "Store") -> None:
    """Set last cron run timestamp in meta table."""
    now = datetime.datetime.now(tz=None).isoformat(timespec="seconds")
    store.set_meta("last_cron_run", now)


def find_new_sessions(config: "Config", since_iso: str) -> list[Path]:
    """Find JSONL session files modified since the given timestamp.

    Args:
        config: Kindex configuration.
        since_iso: ISO timestamp string. Files modified after this time
                   are returned.

    Returns:
        List of Path objects for new/modified JSONL files.
    """
    projects_dir = config.claude_path / "projects"
    if not projects_dir.exists():
        return []

    try:
        since_dt = datetime.datetime.fromisoformat(since_iso)
    except (ValueError, TypeError):
        # If invalid timestamp, return all files
        since_dt = datetime.datetime.min

    results = []
    for jsonl_path in projects_dir.rglob("*.jsonl"):
        try:
            mtime = datetime.datetime.fromtimestamp(jsonl_path.stat().st_mtime)
            if mtime > since_dt:
                results.append(jsonl_path)
        except OSError:
            continue

    return sorted(results, key=lambda p: p.stat().st_mtime, reverse=True)


def incremental_ingest(
    config: "Config", store: "Store", since_iso: str, verbose: bool = False
) -> int:
    """Only ingest sessions newer than since_iso. Returns count.

    This is a lightweight alternative to full scan_sessions that only
    looks at files modified since the given timestamp.
    """
    import json

    new_files = find_new_sessions(config, since_iso)
    if not new_files:
        return 0

    from .extract import keyword_extract

    count = 0
    for jsonl_path in new_files:
        session_id = jsonl_path.stem[:12]
        session_slug = f"session-{session_id}"

        # Skip already-ingested sessions
        if store.get_node(session_slug):
            continue

        # Extract text from the session
        text = _extract_session_text_quick(jsonl_path)
        if not text or len(text) < 50:
            continue

        project_context = jsonl_path.parent.name

        # Extract knowledge
        extraction = keyword_extract(text)
        concepts = extraction.get("concepts", [])
        if not concepts:
            continue

        # Create session node
        summary = text[:500]
        store.add_node(
            node_id=session_slug,
            title=f"Session: {project_context[:40]}",
            content=summary,
            node_type="session",
            prov_source=str(jsonl_path),
            prov_activity="incremental-ingest",
            extra={"project": project_context},
        )
        count += 1

        if verbose:
            print(f"  Ingested: {session_slug} ({project_context})")

        # Link extracted concepts
        for concept in concepts[:5]:
            existing = store.get_node_by_title(concept["title"])
            if existing:
                store.add_edge(
                    session_slug,
                    existing["id"],
                    edge_type="context_of",
                    weight=0.3,
                    provenance="mentioned in session",
                )

    return count


def _extract_session_text_quick(jsonl_path: Path, max_chars: int = 4000) -> str:
    """Quick text extraction from a JSONL session file."""
    import json

    texts = []
    total_len = 0

    try:
        with open(jsonl_path, "r", errors="replace") as f:
            for line in f:
                if total_len >= max_chars:
                    break
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                role = entry.get("role", "")
                if role != "assistant":
                    continue

                content = entry.get("content", "")
                if isinstance(content, str):
                    chunk = content[:800]
                    texts.append(chunk)
                    total_len += len(chunk)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            chunk = block.get("text", "")[:800]
                            texts.append(chunk)
                            total_len += len(chunk)
    except OSError:
        return ""

    return "\n".join(texts)
