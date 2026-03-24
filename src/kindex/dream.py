"""Dream cycle — post-session knowledge consolidation.

Performs memory consolidation on the knowledge graph:
- Fuzzy deduplication (title similarity + content overlap)
- Suggestion auto-application
- Domain-based edge strengthening

Three invocation modes:
- lightweight: dedup + suggestions only, <5s target
- full: all non-LLM consolidation
- deep: includes LLM-powered cluster summarisation (in dream_deep.py)

Designed to run from CLI (kin dream), cron (daemon.py), or
as a detached subprocess from the Claude Code Stop hook.
"""

from __future__ import annotations

import datetime
import difflib
import fcntl
import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .store import Store

logger = logging.getLogger(__name__)

# Node types that dream must never touch (CD008)
PROTECTED_TYPES = frozenset({"constraint", "directive", "checkpoint"})

# Similarity thresholds (CD002)
DEFAULT_MERGE_THRESHOLD = 0.95
DEFAULT_SUGGEST_THRESHOLD = 0.85


# ── Locking ───────────────────────────────────────────────────────────


def _lock_path(config: Config) -> Path:
    return config.data_path / "dream.lock"


def _acquire_lock(config: Config) -> int | None:
    """Try to acquire exclusive dream lock. Returns fd or None if locked."""
    lock_file = _lock_path(config)
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        return None


def _release_lock(fd: int, config: Config) -> None:
    """Release dream lock."""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    except OSError:
        pass


# ── Similarity ────────────────────────────────────────────────────────


def title_similarity(a: str, b: str) -> float:
    """Normalised title similarity using SequenceMatcher."""
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def content_overlap(a: str, b: str) -> float:
    """Content similarity via SequenceMatcher on first 500 chars."""
    if not a or not b:
        return 0.0
    a_trunc = a[:500].lower()
    b_trunc = b[:500].lower()
    return difflib.SequenceMatcher(None, a_trunc, b_trunc).ratio()


def combined_similarity(node_a: dict, node_b: dict) -> float:
    """Weighted combination: 70% title, 30% content."""
    t_sim = title_similarity(node_a.get("title", ""), node_b.get("title", ""))
    c_sim = content_overlap(node_a.get("content", ""), node_b.get("content", ""))
    return 0.7 * t_sim + 0.3 * c_sim


# ── Core operations ──────────────────────────────────────────────────


def find_duplicates(
    store: Store,
    merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
    suggest_threshold: float = DEFAULT_SUGGEST_THRESHOLD,
) -> dict:
    """Find near-duplicate node pairs.

    Returns {"merge": [(a, b, score)], "suggest": [(a, b, score)]}.
    """
    nodes = store.all_nodes(status="active", limit=5000)
    # Filter out protected types
    nodes = [n for n in nodes if n.get("type", "concept") not in PROTECTED_TYPES]

    merge_pairs: list[tuple[str, str, float]] = []
    suggest_pairs: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()

    # Group by first 4 chars of lowercase title for O(n*k) instead of O(n^2)
    # Cap bucket size at 50 to bound worst-case pairwise comparisons
    buckets: dict[str, list[dict]] = {}
    for n in nodes:
        title = (n.get("title") or "").lower()
        if len(title) < 4:
            continue
        key = title[:4]
        bucket = buckets.setdefault(key, [])
        if len(bucket) < 50:
            bucket.append(n)

    for bucket_nodes in buckets.values():
        if len(bucket_nodes) < 2:
            continue
        for i, a in enumerate(bucket_nodes):
            for b in bucket_nodes[i + 1:]:
                pair_key = tuple(sorted([a["id"], b["id"]]))
                if pair_key in seen:
                    continue
                seen.add(pair_key)

                score = combined_similarity(a, b)
                if score >= merge_threshold:
                    merge_pairs.append((a["id"], b["id"], score))
                elif score >= suggest_threshold:
                    suggest_pairs.append((a["id"], b["id"], score))

    return {"merge": merge_pairs, "suggest": suggest_pairs}


def merge_nodes(store: Store, source_id: str, target_id: str) -> bool:
    """Merge source into target: move edges, merge content, archive source.

    Returns True if merge succeeded.
    """
    source = store.get_node(source_id)
    target = store.get_node(target_id)
    if not source or not target:
        return False

    # Move edges from source to target
    for edge in store.edges_from(source_id):
        if edge["to_id"] != target_id:
            store.add_edge(
                target_id, edge["to_id"],
                edge_type=edge.get("type", "relates_to"),
                weight=edge.get("weight", 0.3),
                provenance="dream-cycle merge",
            )
    for edge in store.edges_to(source_id):
        if edge["from_id"] != target_id:
            store.add_edge(
                edge["from_id"], target_id,
                edge_type=edge.get("type", "relates_to"),
                weight=edge.get("weight", 0.3),
                provenance="dream-cycle merge",
            )

    # Merge content if source has unique content
    source_content = source.get("content", "") or ""
    target_content = target.get("content", "") or ""
    if source_content and source_content not in target_content:
        merged = f"{target_content}\n\n[Merged from: {source['title']}]\n{source_content}"
        store.update_node(target_id, content=merged)

    # Boost target weight
    sw = source.get("weight", 0.5) or 0.5
    tw = target.get("weight", 0.5) or 0.5
    store.update_node(target_id, weight=min(1.0, max(tw, sw)))

    # Archive source (CD001: never delete, only archive)
    store.update_node(
        source_id, status="archived", weight=0.01,
        extra={"merged_into": target_id, "merged_by": "dream-cycle"},
    )
    return True


def auto_apply_suggestions(store: Store) -> int:
    """Apply pending suggestions where nodes clearly relate.

    Returns count of suggestions applied.
    """
    suggestions = store.pending_suggestions(limit=100)
    applied = 0

    for s in suggestions:
        concept_a = s.get("concept_a", "")
        concept_b = s.get("concept_b", "")

        # Resolve to actual nodes
        node_a = store.get_node(concept_a) or store.get_node_by_title(concept_a)
        node_b = store.get_node(concept_b) or store.get_node_by_title(concept_b)

        if not node_a or not node_b:
            continue
        if node_a.get("status") != "active" or node_b.get("status") != "active":
            continue

        # Check title similarity for auto-apply confidence
        sim = title_similarity(
            node_a.get("title", ""), node_b.get("title", ""),
        )
        if sim < 0.7:
            continue

        # Check edge doesn't already exist
        existing_out = {e["to_id"] for e in store.edges_from(node_a["id"])}
        existing_in = {e["from_id"] for e in store.edges_to(node_a["id"])}
        if node_b["id"] in existing_out or node_b["id"] in existing_in:
            store.update_suggestion(s["id"], "accepted")
            applied += 1
            continue

        store.add_edge(
            node_a["id"], node_b["id"],
            edge_type="relates_to",
            weight=0.4,
            provenance="dream-cycle auto-apply",
        )
        store.update_suggestion(s["id"], "accepted")
        applied += 1

    return applied


def strengthen_domain_edges(
    store: Store,
    *,
    dry_run: bool = False,
    verbose: bool = False,
) -> int:
    """Find active nodes sharing domains but lacking edges; create weak links."""
    import json

    nodes = store.all_nodes(status="active", limit=2000)
    # Build domain -> node_ids index
    domain_index: dict[str, list[dict]] = {}
    for n in nodes:
        if n.get("type", "concept") in PROTECTED_TYPES:
            continue
        domains = n.get("domains") or []
        if isinstance(domains, str):
            try:
                domains = json.loads(domains)
            except (json.JSONDecodeError, TypeError):
                domains = []
        for d in domains:
            domain_index.setdefault(d, []).append(n)

    created = 0
    seen_pairs: set[tuple[str, str]] = set()

    for domain, members in domain_index.items():
        if len(members) < 2 or len(members) > 50:
            continue
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                pair = tuple(sorted([a["id"], b["id"]]))
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)

                # Check if edge already exists
                existing = {e["to_id"] for e in store.edges_from(a["id"])}
                if b["id"] in existing:
                    continue
                existing_rev = {e["from_id"] for e in store.edges_to(a["id"])}
                if b["id"] in existing_rev:
                    continue

                if dry_run:
                    created += 1
                    continue

                store.add_edge(
                    a["id"], b["id"],
                    edge_type="relates_to",
                    weight=0.15,
                    provenance="dream-cycle domain co-membership",
                )
                created += 1
                if verbose and created <= 10:
                    print(f"  Domain link: {a['title']} <-> {b['title']} ({domain})")

    return created


# ── Dream cycles ─────────────────────────────────────────────────────


def dream_lightweight(
    config: Config,
    store: Store,
    *,
    verbose: bool = False,
    dry_run: bool = False,
) -> dict:
    """Fast dream: dedup detection + suggestion auto-apply.

    Target: <5s for graphs under 5000 nodes (CD009).
    No LLM calls (CD003).
    """
    results: dict = {}

    # Fuzzy dedup
    dupes = find_duplicates(store)

    # Auto-merge high-confidence pairs
    merged = 0
    for source_id, target_id, score in dupes["merge"]:
        if dry_run:
            logger.info("Would merge %s -> %s (score=%.3f)", source_id, target_id, score)
            merged += 1
            continue
        if merge_nodes(store, source_id, target_id):
            merged += 1
            if verbose:
                print(f"  Merged: {source_id} -> {target_id} (score={score:.3f})")

    # Create suggestions for near-misses
    suggested = 0
    for a_id, b_id, score in dupes["suggest"]:
        if dry_run:
            suggested += 1
            continue
        # Deduplicate against existing suggestions
        existing = store.pending_suggestions(limit=200)
        already = any(
            (e["concept_a"] in (a_id, b_id) and e["concept_b"] in (a_id, b_id))
            for e in existing
        )
        if not already:
            store.add_suggestion(
                concept_a=a_id, concept_b=b_id,
                reason=f"Fuzzy match (score={score:.3f})",
                source="dream-cycle",
            )
            suggested += 1

    # Auto-apply pending suggestions
    applied = 0
    if not dry_run:
        applied = auto_apply_suggestions(store)

    results["merged"] = merged
    results["suggested"] = suggested
    results["suggestions_applied"] = applied

    return results


def dream_full(
    config: Config,
    store: Store,
    *,
    verbose: bool = False,
    dry_run: bool = False,
) -> dict:
    """Full dream cycle: lightweight + edge strengthening.

    No LLM calls.
    """
    results = dream_lightweight(config, store, verbose=verbose, dry_run=dry_run)

    # Strengthen edges between nodes that share domains
    strengthened = strengthen_domain_edges(store, dry_run=dry_run, verbose=verbose)
    results["edges_strengthened"] = strengthened

    return results


# ── Entry points ─────────────────────────────────────────────────────


def dream_cycle(
    config: Config,
    store: Store,
    *,
    mode: str = "full",
    verbose: bool = False,
    dry_run: bool = False,
) -> dict:
    """Run a dream cycle with file locking.

    Args:
        mode: 'lightweight', 'full', or 'deep'.
        verbose: Print progress.
        dry_run: Report without making changes.

    Returns dict of results, or {"skipped": "locked"} if another cycle is running.
    """
    fd = _acquire_lock(config)
    if fd is None:
        if verbose:
            print("Dream cycle already running (locked). Skipping.")
        return {"skipped": "locked"}

    try:
        if mode == "lightweight":
            results = dream_lightweight(config, store, verbose=verbose, dry_run=dry_run)
        elif mode == "deep":
            from .dream_deep import dream_deep
            results = dream_deep(config, store, verbose=verbose, dry_run=dry_run)
        else:
            results = dream_full(config, store, verbose=verbose, dry_run=dry_run)

        results["mode"] = mode
        results["timestamp"] = datetime.datetime.now().isoformat(timespec="seconds")

        # Store last dream marker
        if not dry_run:
            store.set_meta("last_dream_run", results["timestamp"])
            store.set_meta("last_dream_mode", mode)

        return results
    finally:
        _release_lock(fd, config)


def detach_dream(config: Config, mode: str = "lightweight") -> int:
    """Spawn a detached dream subprocess. Returns child PID.

    Uses start_new_session=True so the child survives parent exit (CD005).
    """
    from .setup import _find_kin_path

    kin_path = _find_kin_path()
    log_dir = config.data_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "dream.log"

    cmd = [kin_path, "dream", f"--{mode}"]

    with open(log_file, "a") as log_fd:
        proc = subprocess.Popen(
            cmd,
            start_new_session=True,
            stdout=log_fd,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
        )

    return proc.pid
