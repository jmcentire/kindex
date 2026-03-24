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

    # 6. Suggest cross-component links
    suggestion_count = _suggest_links(store, verbose=verbose)
    results["link_suggestions"] = suggestion_count

    # 7. Graph hygiene — archive stale orphans, auto-link viable ones
    hygiene = _graph_hygiene(store, verbose=verbose)
    results["orphans_archived"] = hygiene.get("archived", 0)
    results["orphans_linked"] = hygiene.get("linked", 0)

    # 8. Slow graph archival — move deeply decayed nodes to archive
    try:
        from .archive import archive_cycle
        archived_to_slow = archive_cycle(config, store, verbose=verbose)
        results["slow_graph_archived"] = archived_to_slow
    except Exception:
        results["slow_graph_archived"] = 0

    # 9. Watch hygiene — expire overdue watches
    watch_results = _check_watches(store, verbose=verbose)
    results["watches_expired"] = watch_results.get("expired", 0)
    results["watches_notified"] = watch_results.get("notified", 0)

    # 10. Check reminders
    reminder_results = _check_reminders(config, store, verbose=verbose)
    results["reminders_fired"] = reminder_results.get("fired", 0)
    results["reminders_auto_snoozed"] = reminder_results.get("auto_snoozed", 0)

    # 11. Lightweight dream — dedup + suggestion auto-apply
    try:
        from .dream import dream_lightweight
        dream_results = dream_lightweight(config, store, verbose=verbose)
        results["dream_merged"] = dream_results.get("merged", 0)
        results["dream_suggestions_applied"] = dream_results.get("suggestions_applied", 0)
    except Exception:
        results["dream_merged"] = 0
        results["dream_suggestions_applied"] = 0

    # Update run marker
    set_run_marker(store)

    # Adaptive scheduling: repack cron interval based on nearest reminder
    try:
        from .scheduling import repack_schedule
        repack = repack_schedule(store, config)
        results["repack"] = repack
    except Exception:
        pass  # don't let scheduling errors break cron

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


def _suggest_links(store: "Store", verbose: bool = False) -> int:
    """Find and store cross-component link suggestions."""
    try:
        from .graph import suggest_cross_component_links

        suggestions = suggest_cross_component_links(store, max_suggestions=5)
        count = 0
        for s in suggestions:
            # Check if this suggestion already exists
            existing = store.pending_suggestions(limit=100)
            already = any(
                (e["concept_a"] == s["concept_a"] and e["concept_b"] == s["concept_b"])
                or (e["concept_a"] == s["concept_b"] and e["concept_b"] == s["concept_a"])
                for e in existing
            )
            if not already:
                store.add_suggestion(
                    concept_a=s["concept_a"],
                    concept_b=s["concept_b"],
                    reason=s["reason"],
                    source="cron-auto-suggest",
                )
                count += 1
                if verbose:
                    print(f"  Suggested: {s['concept_a']} <-> {s['concept_b']}")

        return count
    except Exception:
        return 0


def _graph_hygiene(store: "Store", verbose: bool = False) -> dict:
    """Archive stale orphans and auto-link viable ones.

    Stale: orphan with weight < 0.15 and not updated in 30+ days.
    Viable: orphan whose title FTS-matches existing connected nodes.
    """
    import datetime as _dt

    results = {"archived": 0, "linked": 0}

    try:
        orphans = store.orphans()
    except Exception:
        return results

    if not orphans:
        return results

    now = _dt.datetime.now()
    stale_cutoff = now - _dt.timedelta(days=30)

    for orphan in orphans:
        oid = orphan["id"]
        weight = orphan.get("weight", 0.5) or 0.5
        title = orphan.get("title", "")
        node_type = orphan.get("type", "concept")

        # Skip task/session/checkpoint nodes — they have their own lifecycle
        if node_type in ("task", "session", "checkpoint", "directive", "constraint"):
            continue

        # Parse updated_at
        updated = orphan.get("updated_at", "")
        try:
            updated_dt = _dt.datetime.fromisoformat(updated) if updated else _dt.datetime.min
        except (ValueError, TypeError):
            updated_dt = _dt.datetime.min

        is_stale = weight < 0.15 and updated_dt < stale_cutoff

        if is_stale:
            # Archive: set status to archived, drop weight to floor
            store.update_node(oid, status="archived", weight=0.01)
            results["archived"] += 1
            if verbose:
                print(f"  Archived orphan: {title} (w={weight:.2f})")
            continue

        # Try to auto-link viable orphans via FTS title match
        if not title or len(title) < 3:
            continue

        matches = store.fts_search(title, limit=5)
        for match in matches:
            mid = match["id"]
            if mid == oid:
                continue
            # Only link to nodes that already have edges (not other orphans)
            if not store.edges_from(mid) and not store.edges_to(mid):
                continue
            # Create a low-weight link
            store.add_edge(
                oid, mid,
                edge_type="relates_to",
                weight=0.2,
                provenance="auto-linked by graph hygiene",
            )
            results["linked"] += 1
            if verbose:
                print(f"  Linked orphan: {title} -> {match.get('title', mid)}")
            break  # One link is enough to de-orphan

    return results


def _check_watches(store: "Store", verbose: bool = False) -> dict:
    """Check watch nodes for expiry and flag triggered ones.

    - Expired watches (past their expires date) get archived.
    - Watches with check_command in extra get flagged for Claude attention.
    """
    import datetime as _dt

    results = {"expired": 0, "notified": 0}

    try:
        # Query all active watch nodes (including those past expiry that
        # haven't been archived yet — active_watches() filters those out)
        watches = store.all_nodes(node_type="watch", status="active", limit=200)
    except Exception:
        return results

    today = _dt.date.today().isoformat()

    for w in watches:
        extra = w.get("extra") or {}
        expires = extra.get("expires", "")
        wid = w["id"]

        # Expire overdue watches
        if expires and expires < today:
            store.update_node(wid, status="archived")
            results["expired"] += 1
            if verbose:
                print(f"  Expired watch: {w['title']} (was due {expires})")
            continue

        # Boost weight of watches approaching expiry (within 3 days)
        if expires:
            try:
                exp_date = _dt.date.fromisoformat(expires)
                days_left = (exp_date - _dt.date.today()).days
                if 0 <= days_left <= 3:
                    # Boost so it surfaces prominently in prime_context
                    current_weight = w.get("weight", 0.5)
                    if current_weight < 0.8:
                        store.update_node(wid, weight=0.9)
                        results["notified"] += 1
                        if verbose:
                            print(f"  Boosted watch: {w['title']} ({days_left}d left)")
            except (ValueError, TypeError):
                pass

    return results


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
