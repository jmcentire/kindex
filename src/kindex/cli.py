"""Kindex CLI (kin) — knowledge graph that learns from your conversations."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
from pathlib import Path

import yaml

from . import __version__


def _json_default(obj):
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Not JSON serializable: {type(obj)}")


def _dumps(obj, **kw):
    return json.dumps(obj, default=_json_default, **kw)


def _config(args):
    from .config import load_config
    cfg = load_config(getattr(args, "config", None))
    if getattr(args, "data_dir", None):
        cfg.data_dir = args.data_dir
    return cfg


def _store(args):
    from .store import Store
    return Store(_config(args))


def _ledger(args):
    from .budget import BudgetLedger
    cfg = _config(args)
    return BudgetLedger(cfg.ledger_path, cfg.budget), cfg


# ── search ─────────────────────────────────────────────────────────────

def cmd_search(args):
    """Hybrid search: FTS5 + graph traversal, merged via RRF."""
    store = _store(args)
    query = " ".join(args.query)

    from .retrieve import hybrid_search
    results = hybrid_search(store, query, top_k=args.top_k)

    # --mine: filter to nodes owned by current user
    if getattr(args, "mine", False):
        cfg = _config(args)
        me = cfg.current_user
        results = [r for r in results if me in (r.get("prov_who") or [])
                   or (r.get("extra") or {}).get("owner") == me]

    if not results:
        print("No results.", file=sys.stderr)
        return

    if args.json:
        out = [{
            "id": r["id"], "type": r["type"], "title": r["title"],
            "weight": r["weight"], "rrf_score": r.get("rrf_score", 0),
            "content_preview": (r.get("content") or "")[:300],
            "edges": [{"to": e["to_id"], "type": e["type"], "weight": e["weight"]}
                      for e in r.get("edges_out", [])],
        } for r in results]
        print(_dumps(out, indent=2))
    else:
        print(f"# Kindex:{len(results)} results for \"{query}\"\n")
        for r in results:
            title = r.get("title", r["id"])
            ntype = r.get("type", "concept")
            weight = r.get("weight", 0)
            content = (r.get("content") or "")[:200]
            edges = r.get("edges_out", [])

            print(f"## [{ntype}] {title} (w={weight:.2f})")
            if content:
                print(f"  {content}")
            if edges:
                connected = ", ".join(e.get("to_title", e["to_id"]) for e in edges[:5])
                print(f"  → {connected}")
            print()

    store.close()


# ── context ────────────────────────────────────────────────────────────

def cmd_context(args):
    """Output formatted context block for CLAUDE.md injection.

    Supports five context tiers: full, abridged, summarized, executive, index.
    Auto-selects based on --tokens if --level is not specified.
    """
    store = _store(args)

    from .retrieve import (
        auto_select_tier, detect_domain_from_path, format_context_block, hybrid_search,
    )

    # Auto-detect topic from $PWD if not specified
    topic = args.topic
    if not topic:
        cwd = os.getcwd()
        domains = detect_domain_from_path(store, cwd)
        if domains:
            topic = " ".join(domains)
        else:
            topic = os.path.basename(cwd)

    level = getattr(args, "level", None)
    tokens = getattr(args, "tokens", None)

    results = hybrid_search(store, topic, top_k=args.depth or 10)

    if args.format == "json":
        tier = level or auto_select_tier(tokens)
        print(_dumps({"query": topic, "level": tier, "results": [{
            "id": r["id"], "title": r["title"], "type": r["type"],
        } for r in results]}, indent=2))
    else:
        block = format_context_block(store, results, query=topic,
                                     level=level, max_tokens_approx=tokens)
        print(block)

    store.close()


# ── add ────────────────────────────────────────────────────────────────

def cmd_add(args):
    """Quick capture with auto-extraction and linking.

    For operational types (constraint, directive, checkpoint, watch),
    creates the node directly with metadata from flags.
    For knowledge types, runs the extraction pipeline.
    """
    store = _store(args)
    ledger, cfg = _ledger(args)
    content = " ".join(args.note)
    node_type = args.type or "concept"

    # Resolve current user for provenance
    cfg = _config(args)
    current_user = cfg.current_user

    # Operational types get direct creation with metadata
    operational = {"constraint", "directive", "checkpoint", "watch"}
    if node_type in operational:
        extra = {}
        if args.trigger:
            extra["trigger"] = args.trigger
        if args.action:
            extra["action"] = args.action
        if args.scope:
            extra["scope"] = args.scope
        if args.owner:
            extra["owner"] = args.owner
        if args.expires:
            extra["expires"] = args.expires
        if args.resets:
            extra["resets"] = args.resets

        nid = store.add_node(
            title=content,
            content="",
            node_type=node_type,
            audience=args.audience or "private",
            prov_activity="manual-add",
            prov_source="cli",
            prov_who=[current_user],
            extra=extra,
        )
        label = node_type.capitalize()
        print(f"  {label}: {content} ({nid})")
        if extra:
            for k, v in extra.items():
                print(f"    {k}: {v}")
        print(f"\n1 {node_type} added.")
        store.close()
        return

    # Knowledge types — run extraction pipeline
    from .extract import extract

    existing = [n["title"] for n in store.all_nodes(limit=200)]
    extraction = extract(content, existing, cfg, ledger)

    created_ids = []

    # Add extracted concepts
    for concept in extraction.get("concepts", []):
        existing_node = store.get_node_by_title(concept["title"])
        if existing_node:
            old_content = existing_node.get("content", "")
            new_content = concept.get("content", "")
            if new_content and new_content not in old_content:
                store.update_node(existing_node["id"],
                                  content=old_content + "\n\n" + new_content)
                print(f"  Updated: {concept['title']}")
            continue

        nid = store.add_node(
            title=concept["title"],
            content=concept.get("content", content),
            node_type=concept.get("type", node_type),
            domains=concept.get("domains", []),
            prov_activity="manual-add",
            prov_source="cli",
            prov_who=[current_user],
        )
        created_ids.append(nid)
        print(f"  Created: {concept['title']} ({nid})")

    # If no concepts extracted, create a single node from the raw text
    if not extraction.get("concepts"):
        title = content[:60].strip()
        if len(content) > 60:
            title += "..."
        nid = store.add_node(
            title=title, content=content, node_type=node_type,
            prov_activity="manual-add", prov_source="cli",
            prov_who=[current_user],
        )
        created_ids.append(nid)
        print(f"  Created: {title} ({nid})")

    # Add extracted decisions
    for decision in extraction.get("decisions", []):
        nid = store.add_node(
            title=decision["title"],
            content=decision.get("rationale", ""),
            node_type="decision",
            prov_activity="manual-add",
        )
        created_ids.append(nid)
        print(f"  Decision: {decision['title']} ({nid})")

    # Add extracted questions
    for question in extraction.get("questions", []):
        nid = store.add_node(
            title=question["question"],
            content=question.get("context", ""),
            node_type="question",
            status="open-question",
            prov_activity="manual-add",
        )
        created_ids.append(nid)
        print(f"  Question: {question['question']} ({nid})")

    # Add connections
    for conn in extraction.get("connections", []):
        from_node = store.get_node_by_title(conn.get("from_title", ""))
        to_node = store.get_node_by_title(conn.get("to_title", ""))
        if from_node and to_node:
            store.add_edge(from_node["id"], to_node["id"],
                           edge_type=conn.get("type", "relates_to"),
                           provenance=conn.get("why", "extracted"))
            print(f"  Linked: {conn['from_title']} → {conn['to_title']}")

    # Ensure no orphans — link created nodes to each other if multiple
    if len(created_ids) > 1:
        for i in range(len(created_ids) - 1):
            store.add_edge(created_ids[i], created_ids[i + 1],
                           provenance="co-created")

    print(f"\n{len(created_ids)} node(s) added.")
    store.close()


# ── learn ──────────────────────────────────────────────────────────────

def cmd_learn(args):
    """Extract knowledge from a Claude Code session or inbox."""
    store = _store(args)
    ledger, cfg = _ledger(args)

    if args.from_inbox:
        inbox_dir = cfg.inbox_dir
        if not inbox_dir.exists():
            print("No inbox directory.", file=sys.stderr)
            return

        from .vault import parse_frontmatter
        count = 0
        for f in sorted(inbox_dir.glob("*.md")):
            meta, body = parse_frontmatter(f)
            if meta.get("processed"):
                continue

            content = meta.get("content", body or "")
            if isinstance(content, str) and content.strip():
                from .extract import extract
                existing = [n["title"] for n in store.all_nodes(limit=200)]
                extraction = extract(content, existing, cfg, ledger)

                for concept in extraction.get("concepts", []):
                    if not store.get_node_by_title(concept["title"]):
                        store.add_node(
                            title=concept["title"],
                            content=concept.get("content", content),
                            node_type=concept.get("type", "concept"),
                            domains=concept.get("domains", []),
                            prov_source=str(f.name),
                        )
                        print(f"  Extracted: {concept['title']}")
                        count += 1

            # Mark as processed
            meta["processed"] = True
            from .vault import serialize_frontmatter
            f.write_text(serialize_frontmatter(meta, body))

        print(f"\nProcessed inbox: {count} new node(s).")
    else:
        print("Usage: conv learn --from-inbox", file=sys.stderr)
        print("Session learning will be added with archive integration.", file=sys.stderr)

    store.close()


# ── link ───────────────────────────────────────────────────────────────

def cmd_link(args):
    """Create an edge between two nodes."""
    store = _store(args)

    node_a = store.get_node(args.node_a) or store.get_node_by_title(args.node_a)
    node_b = store.get_node(args.node_b) or store.get_node_by_title(args.node_b)

    if not node_a:
        print(f"Error: '{args.node_a}' not found.", file=sys.stderr)
        sys.exit(1)
    if not node_b:
        print(f"Error: '{args.node_b}' not found.", file=sys.stderr)
        sys.exit(1)

    store.add_edge(node_a["id"], node_b["id"],
                   edge_type=args.relationship,
                   weight=args.weight,
                   provenance=args.why or "")
    print(f"Linked: {node_a['title']} —[{args.relationship}]→ {node_b['title']}")
    store.close()


# ── show ───────────────────────────────────────────────────────────────

def cmd_show(args):
    """Show full node with edges and provenance."""
    store = _store(args)
    node = store.get_node(args.node_id) or store.get_node_by_title(args.node_id)

    if not node:
        print(f"Error: '{args.node_id}' not found.", file=sys.stderr)
        sys.exit(1)

    edges_out = store.edges_from(node["id"])
    edges_in = store.edges_to(node["id"])

    if args.json:
        node["edges_out"] = edges_out
        node["edges_in"] = edges_in
        print(_dumps(node, indent=2))
    else:
        print(f"# {node['title']} [{node['type']}]")
        print(f"**ID:** {node['id']}")
        print(f"**Weight:** {node['weight']:.2f}")
        print(f"**Status:** {node['status']}")
        print(f"**Domains:** {', '.join(node.get('domains') or [])}")
        if node.get("aka"):
            print(f"**AKA:** {', '.join(node['aka'])}")
        if node.get("intent"):
            print(f"**Intent:** {node['intent']}")
        if node.get("prov_source"):
            print(f"**Source:** {node['prov_source']}")
        if node.get("prov_when"):
            print(f"**When:** {node['prov_when']}")

        # Display current_state if present (mutable directive state)
        extra = node.get("extra") or {}
        current_state = extra.get("current_state")
        if current_state:
            print(f"\n**Current State:**")
            for k, v in current_state.items():
                print(f"  {k}: {v}")
            state_updated = extra.get("state_updated_at")
            if state_updated:
                print(f"  (updated: {state_updated})")

        if node.get("content"):
            print(f"\n{node['content'][:1000]}")

        if edges_out:
            print(f"\n## Outgoing ({len(edges_out)})")
            for e in edges_out:
                print(f"  → {e.get('to_title', e['to_id']):30s} [{e['type']}] w={e['weight']:.2f}  {e.get('provenance', '')[:60]}")

        if edges_in:
            print(f"\n## Incoming ({len(edges_in)})")
            for e in edges_in:
                print(f"  ← {e.get('from_title', e['from_id']):30s} [{e['type']}] w={e['weight']:.2f}")

    store.close()


# ── list / recent / orphans ────────────────────────────────────────────

def cmd_list(args):
    store = _store(args)
    nodes = store.all_nodes(node_type=args.type, status=args.status, limit=args.limit or 100)

    # --mine: filter to nodes owned by current user
    if getattr(args, "mine", False):
        cfg = _config(args)
        me = cfg.current_user
        nodes = [n for n in nodes if me in (n.get("prov_who") or [])
                 or (n.get("extra") or {}).get("owner") == me]

    if args.json:
        print(_dumps([{"id": n["id"], "type": n["type"], "title": n["title"],
                        "weight": n["weight"], "status": n["status"]}
                       for n in nodes], indent=2))
    else:
        for n in nodes:
            print(f"  [{n['type'][:4]:4s}] {n['title'][:50]:50s} w={n['weight']:.2f}  {n['id']}")

    store.close()


def cmd_recent(args):
    store = _store(args)
    nodes = store.recent_nodes(n=args.n)

    for n in nodes:
        when = n.get("updated_at", "")[:16]
        print(f"  {when}  [{n['type'][:4]}] {n['title'][:50]}  {n['id']}")

    store.close()


def cmd_orphans(args):
    store = _store(args)
    orphans = store.orphans()

    if orphans:
        print(f"{len(orphans)} orphan(s):")
        for n in orphans:
            print(f"  {n['id']}  [{n['type']}] {n['title']}")
    else:
        print("No orphans. Graph health: good.")

    store.close()


# ── status / budget ────────────────────────────────────────────────────

def cmd_status(args):
    store = _store(args)
    trigger = getattr(args, "trigger", None)
    owner = getattr(args, "owner", None)
    filter_type = getattr(args, "type", None)

    # --mine resolves to current user
    if getattr(args, "mine", False) and not owner:
        cfg = _config(args)
        owner = cfg.current_user

    # If requesting operational status (trigger or specific operational type)
    operational_types = {"constraint", "directive", "checkpoint", "watch"}
    if trigger or filter_type in operational_types:
        ops = store.operational_summary(trigger=trigger, owner=owner)

        if args.json:
            print(_dumps({
                "trigger": trigger,
                "owner": owner,
                "constraints": [{"id": n["id"], "title": n["title"],
                                 "action": (n.get("extra") or {}).get("action", "warn"),
                                 "trigger": (n.get("extra") or {}).get("trigger", "")}
                                for n in ops["constraints"]],
                "checkpoints": [{"id": n["id"], "title": n["title"],
                                 "trigger": (n.get("extra") or {}).get("trigger", "")}
                                for n in ops["checkpoints"]],
                "watches": [{"id": n["id"], "title": n["title"],
                             "owner": (n.get("extra") or {}).get("owner", ""),
                             "expires": (n.get("extra") or {}).get("expires", "")}
                            for n in ops["watches"]],
                "directives": [{"id": n["id"], "title": n["title"],
                                "scope": (n.get("extra") or {}).get("scope", "")}
                               for n in ops["directives"]],
            }, indent=2))
        else:
            if trigger:
                print(f"# Operational status for trigger: {trigger}\n")
            else:
                print("# Operational status\n")

            if ops["constraints"]:
                print(f"## Constraints ({len(ops['constraints'])})")
                for n in ops["constraints"]:
                    extra = n.get("extra") or {}
                    action = extra.get("action", "warn")
                    trig = extra.get("trigger", "")
                    print(f"  [{action:5s}] {n['title'][:60]}")
                    if trig:
                        print(f"         trigger: {trig}")
                print()

            if ops["checkpoints"]:
                print(f"## Checkpoints ({len(ops['checkpoints'])})")
                for n in ops["checkpoints"]:
                    trig = (n.get("extra") or {}).get("trigger", "")
                    print(f"  [ ] {n['title'][:60]}")
                    if trig:
                        print(f"      trigger: {trig}")
                print()

            if ops["watches"]:
                print(f"## Watches ({len(ops['watches'])})")
                for n in ops["watches"]:
                    extra = n.get("extra") or {}
                    who = extra.get("owner", "")
                    exp = extra.get("expires", "")
                    suffix = ""
                    if who:
                        suffix += f" @{who}"
                    if exp:
                        suffix += f" (expires {exp})"
                    print(f"  ! {n['title'][:55]}{suffix}")
                print()

            if ops["directives"]:
                print(f"## Directives ({len(ops['directives'])})")
                for n in ops["directives"]:
                    scope = (n.get("extra") or {}).get("scope", "")
                    print(f"  > {n['title'][:60]}")
                    if scope:
                        print(f"    scope: {scope}")
                print()

            total = sum(len(v) for v in ops.values())
            if total == 0:
                print("No active operational nodes.")

        store.close()
        return

    # Standard graph stats
    stats = store.stats()

    if args.json:
        print(_dumps(stats, indent=2))
    else:
        print(f"Nodes:     {stats['nodes']}")
        print(f"Edges:     {stats['edges']}")
        print(f"Orphans:   {stats['orphans']}")
        print(f"\nBy type:")
        for t, c in sorted(stats.get("types", {}).items()):
            print(f"  {t:12s} {c}")

        # Summary of active operational nodes
        ops = store.operational_summary()
        op_count = sum(len(v) for v in ops.values())
        if op_count > 0:
            print(f"\nOperational: {len(ops['constraints'])} constraints, "
                  f"{len(ops['checkpoints'])} checkpoints, "
                  f"{len(ops['watches'])} watches, "
                  f"{len(ops['directives'])} directives")

    store.close()


def cmd_budget(args):
    ledger, _ = _ledger(args)
    s = ledger.summary()

    if args.json:
        print(_dumps(s, indent=2))
    else:
        print("LLM Budget")
        for period in ["today", "week", "month"]:
            d = s[period]
            bar_len = 20
            pct = d["spent"] / d["limit"] if d["limit"] > 0 else 0
            filled = int(min(pct, 1.0) * bar_len)
            bar = "█" * filled + "░" * (bar_len - filled)
            print(f"  {period:6s} {bar} ${d['spent']:.4f} / ${d['limit']:.2f}")
        status = "OK" if s["can_spend"] else "LIMIT REACHED"
        print(f"\n  Status: {status}")


# ── init / migrate / doctor ────────────────────────────────────────────

def cmd_init(args):
    cfg = _config(args)
    dp = cfg.data_path
    if (dp / "kindex.db").exists() or (dp / "conv.db").exists():
        print(f"Error: database already exists at {dp}", file=sys.stderr)
        sys.exit(1)

    synonyms_dir = dp / "synonyms"
    for d in [cfg.topics_dir, cfg.skills_dir, cfg.inbox_dir, cfg.tmp_dir, synonyms_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Create the database
    from .store import Store
    store = Store(cfg)
    _ = store.conn  # triggers schema creation
    store.close()

    print(f"Initialized Kindex at {dp}")
    print(f"  kindex.db  — SQLite knowledge graph")
    print(f"  topics/    — markdown topic files")
    print(f"  skills/    — skill/ability files")
    print(f"  inbox/     — queued discoveries")
    print(f"  synonyms/  — synonym ring files (.syn)")


def cmd_migrate(args):
    """Import existing Conv markdown topics into the SQLite store."""
    cfg = _config(args)
    from .store import Store
    from .vault import Vault

    vault = Vault(cfg).load()
    store = Store(cfg)

    count = 0
    for slug, topic in vault.topics.items():
        existing = store.get_node(slug) or store.get_node_by_title(topic.title)
        if existing:
            continue

        nid = store.add_node(
            node_id=slug,
            title=topic.title or slug,
            content=topic.body,
            node_type="concept",
            weight=topic.weight or 0.5,
            domains=topic.domains,
            status=str(topic.status) if topic.status else "active",
            extra=topic.__pydantic_extra__ or {},
            prov_source=str(topic.path or ""),
        )
        count += 1

    # Import edges with bidirectional enforcement
    for slug, topic in vault.topics.items():
        for edge in topic.connects_to:
            if store.get_node(edge.target):
                store.add_edge(slug, edge.target,
                               weight=edge.weight,
                               provenance=edge.reason,
                               bidirectional=True)

    # Import skills
    for slug, skill in vault.skills.items():
        if store.get_node(slug):
            continue
        store.add_node(
            node_id=slug,
            title=skill.title or slug,
            content=skill.body,
            node_type="skill",
            domains=skill.domains,
            prov_source=str(skill.path or ""),
        )
        count += 1

        for edge in skill.connects_to:
            if store.get_node(edge.target):
                store.add_edge(slug, edge.target,
                               weight=edge.weight,
                               provenance=edge.reason,
                               bidirectional=True)

    stats = store.stats()
    print(f"Migrated: {count} new nodes")
    print(f"Total: {stats['nodes']} nodes, {stats['edges']} edges, {stats['orphans']} orphans")
    store.close()


def cmd_doctor(args):
    """Comprehensive health check with graph invariants."""
    store = _store(args)
    stats = store.stats()
    issues = []
    warnings = []
    fixes_applied = 0
    do_fix = getattr(args, "fix", False)

    # ── Basic health ──
    if stats["nodes"] == 0:
        issues.append("No nodes — run `kin migrate` or `kin add` to create knowledge")

    # ── Orphan check ──
    orphans = store.orphans()
    if orphans:
        orphan_pct = len(orphans) / max(stats["nodes"], 1) * 100
        if orphan_pct > 30:
            issues.append(f"{len(orphans)} orphan nodes ({orphan_pct:.0f}%) — "
                          f"run `kin orphans` then `kin link`")
        elif orphan_pct > 10:
            warnings.append(f"{len(orphans)} orphan nodes ({orphan_pct:.0f}%)")

    # ── Weight distribution ──
    nodes = store.all_nodes(limit=10000)
    if nodes:
        weights = [n.get("weight", 0) for n in nodes]
        avg_weight = sum(weights) / len(weights)
        low_weight = sum(1 for w in weights if w < 0.1)
        if low_weight > len(weights) * 0.5:
            warnings.append(f"{low_weight}/{len(weights)} nodes have weight < 0.1 — "
                            f"run `kin decay` or boost important nodes")
        if avg_weight < 0.2:
            warnings.append(f"Average weight is {avg_weight:.2f} — graph may be over-decayed")

    # ── Stale nodes (not accessed in 90+ days) ──
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(days=90)).isoformat()[:10]
    stale = [n for n in nodes if (n.get("last_accessed") or "")[:10] < cutoff]
    if stale and len(stale) > len(nodes) * 0.3:
        warnings.append(f"{len(stale)} nodes not accessed in 90+ days")

    # ── FTS5 sync check ──
    try:
        fts_count = store.conn.execute(
            "SELECT COUNT(*) FROM nodes_fts").fetchone()[0]
        node_count = stats["nodes"]
        if fts_count != node_count:
            issues.append(f"FTS5 index out of sync: {fts_count} indexed vs {node_count} nodes")
            if do_fix:
                store.conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")
                store.conn.commit()
                fixes_applied += 1
                issues[-1] += " (FIXED: rebuilt FTS5)"
    except Exception:
        warnings.append("Could not check FTS5 index health")

    # ── Dangling edges ──
    dangling = store.conn.execute(
        """SELECT COUNT(*) FROM edges WHERE
           from_id NOT IN (SELECT id FROM nodes) OR
           to_id NOT IN (SELECT id FROM nodes)"""
    ).fetchone()[0]
    if dangling:
        issues.append(f"{dangling} dangling edge(s) pointing to deleted nodes")
        if do_fix:
            store.conn.execute(
                """DELETE FROM edges WHERE
                   from_id NOT IN (SELECT id FROM nodes) OR
                   to_id NOT IN (SELECT id FROM nodes)""")
            store.conn.commit()
            fixes_applied += 1
            issues[-1] += " (FIXED: removed)"

    # ── Bidirectional invariant check ──
    one_way = store.conn.execute(
        """SELECT COUNT(*) FROM edges e1
           WHERE NOT EXISTS (
               SELECT 1 FROM edges e2
               WHERE e2.from_id = e1.to_id AND e2.to_id = e1.from_id
           )"""
    ).fetchone()[0]
    total_edges = stats["edges"]
    if total_edges > 0 and one_way > total_edges * 0.3:
        warnings.append(f"{one_way}/{total_edges} edges lack reverse — "
                        f"consider re-adding with bidirectional=True")

    # ── Empty content check ──
    empty = sum(1 for n in nodes if not (n.get("content") or "").strip())
    if empty > len(nodes) * 0.5 and len(nodes) > 5:
        warnings.append(f"{empty}/{len(nodes)} nodes have empty content")

    # ── Graph connectivity (bridge edges) ──
    if stats["nodes"] >= 5 and stats["edges"] >= 4:
        from .graph import store_stats as gstats
        gs = gstats(store)
        if gs["components"] > 1:
            warnings.append(f"Graph has {gs['components']} disconnected components")

    # ── Cross-domain bridge density ──
    if stats["nodes"] >= 5 and stats["edges"] >= 4:
        from .graph import build_nx_from_store
        G = build_nx_from_store(store)
        # Collect all unique domains across nodes
        domain_sets = {}
        for nid in G.nodes():
            domains = G.nodes[nid].get("domains") or []
            if isinstance(domains, str):
                domains = [domains]
            domain_sets[nid] = set(domains)
        all_domains = set()
        for ds in domain_sets.values():
            all_domains.update(ds)
        if len(all_domains) >= 2:
            total_edges_g = G.number_of_edges()
            cross_domain = 0
            for u, v in G.edges():
                u_doms = domain_sets.get(u, set())
                v_doms = domain_sets.get(v, set())
                if u_doms and v_doms and not u_doms.intersection(v_doms):
                    cross_domain += 1
            if total_edges_g > 0:
                cross_pct = cross_domain / total_edges_g
                if cross_pct < 0.10:
                    warnings.append(
                        f"Low cross-domain bridging: {cross_domain}/{total_edges_g} edges "
                        f"({cross_pct:.0%}) cross domain boundaries (< 10%)")
                    if do_fix:
                        # Suggest edges between nodes in different domains
                        import random
                        domain_nodes: dict[str, list[str]] = {}
                        for nid, doms in domain_sets.items():
                            for d in doms:
                                domain_nodes.setdefault(d, []).append(nid)
                        dom_list = list(domain_nodes.keys())
                        suggested = 0
                        for i in range(len(dom_list)):
                            for j in range(i + 1, len(dom_list)):
                                pool_a = domain_nodes[dom_list[i]]
                                pool_b = domain_nodes[dom_list[j]]
                                if pool_a and pool_b:
                                    a = random.choice(pool_a)
                                    b = random.choice(pool_b)
                                    a_title = G.nodes[a].get("title", a)
                                    b_title = G.nodes[b].get("title", b)
                                    store.add_suggestion(
                                        a_title, b_title,
                                        reason=f"Cross-domain bridge: {dom_list[i]} <-> {dom_list[j]}",
                                        source="doctor --fix",
                                    )
                                    suggested += 1
                                    if suggested >= 5:
                                        break
                            if suggested >= 5:
                                break
                        if suggested:
                            warnings[-1] += f" (suggested {suggested} bridge edges — see `kin suggest`)"
                            fixes_applied += 1

    # ── Trailhead coverage ──
    if stats["nodes"] > 10 and stats["edges"] >= 4:
        from .graph import store_trailheads
        trailheads = store_trailheads(store, top_k=10)
        # Count trailheads with meaningful scores
        significant = [t for t in trailheads if t["score"] > 0 and t["out_degree"] >= 2]
        if len(significant) < 2:
            warnings.append(
                f"Low trailhead coverage: only {len(significant)} entry point(s) detected "
                f"(< 2). Add more high-connectivity nodes to improve discoverability")

    # ── Component balance ──
    if stats["nodes"] >= 5 and stats["edges"] >= 4:
        import networkx as nx
        try:
            G_bal = build_nx_from_store(store)
        except NameError:
            from .graph import build_nx_from_store
            G_bal = build_nx_from_store(store)
        components = list(nx.weakly_connected_components(G_bal))
        if components:
            largest = max(len(c) for c in components)
            total_nodes = G_bal.number_of_nodes()
            if total_nodes > 0 and largest / total_nodes > 0.80:
                warnings.append(
                    f"Component imbalance: largest component has {largest}/{total_nodes} "
                    f"nodes ({largest/total_nodes:.0%}). Consider splitting into sub-domains")

    # ── Output ──
    if args.json:
        print(_dumps({
            "healthy": not issues,
            "issues": issues,
            "warnings": warnings,
            "stats": stats,
            "fixes_applied": fixes_applied,
        }, indent=2))
    else:
        if issues:
            print(f"{len(issues)} issue(s):")
            for i in issues:
                print(f"  ✗ {i}")
        if warnings:
            print(f"\n{len(warnings)} warning(s):")
            for w in warnings:
                print(f"  ⚠ {w}")
        if not issues and not warnings:
            print(f"Healthy: {stats['nodes']} nodes, {stats['edges']} edges, 0 issues")
        elif not issues:
            print(f"\nNo critical issues. {stats['nodes']} nodes, {stats['edges']} edges.")
        if fixes_applied:
            print(f"\n{fixes_applied} fix(es) applied.")
        if issues and not do_fix:
            print(f"\nRun `kin doctor --fix` to auto-repair fixable issues.")

    store.close()


# ── set-audience ──────────────────────────────────────────────────────

def cmd_set_audience(args):
    """Set the audience scope of a node (private/team/org/public)."""
    store = _store(args)
    node = store.get_node(args.node_id) or store.get_node_by_title(args.node_id)

    if not node:
        print(f"Error: '{args.node_id}' not found.", file=sys.stderr)
        sys.exit(1)

    store.update_node(node["id"], audience=args.audience)
    print(f"Set {node['title']} audience to: {args.audience}")
    store.close()


# ── set-state ─────────────────────────────────────────────────────────

def cmd_set_state(args):
    """Set a key-value pair in a node's current_state (mutable directive state)."""
    store = _store(args)
    node = store.get_node(args.node_id) or store.get_node_by_title(args.node_id)

    if not node:
        print(f"Error: '{args.node_id}' not found.", file=sys.stderr)
        sys.exit(1)

    # Build state dict: get existing current_state and update the key
    extra = node.get("extra") or {}
    current_state = extra.get("current_state") or {}

    # Coerce value to appropriate type
    value = args.value
    if value.lower() in ("true", "yes"):
        value = True
    elif value.lower() in ("false", "no"):
        value = False
    else:
        try:
            value = int(value)
        except ValueError:
            try:
                value = float(value)
            except ValueError:
                pass  # keep as string

    current_state[args.key] = value
    store.update_directive_state(node["id"], current_state)
    print(f"Set state on {node['title']}: {args.key} = {value}")
    store.close()


# ── export ────────────────────────────────────────────────────────────

def _strip_pii(node: dict) -> dict:
    """Strip personally identifiable information from a node dict."""
    import re
    node = dict(node)  # shallow copy
    node["prov_who"] = ["anonymous"]
    node["prov_source"] = Path(node.get("prov_source", "")).name if node.get("prov_source") else ""
    # Strip emails from content
    content = node.get("content", "")
    content = re.sub(r'\S+@\S+\.\S+', '[email]', content)
    # Strip long tokens/keys (API keys, tokens, hashes)
    content = re.sub(r'[A-Za-z0-9_-]{40,}', '[redacted]', content)
    node["content"] = content
    # Strip actor from activity log entries stored in extra
    extra = node.get("extra")
    if isinstance(extra, dict):
        extra = dict(extra)
        if "actor" in extra:
            del extra["actor"]
        node["extra"] = extra
    return node


def cmd_export(args):
    """Export the graph, respecting audience boundaries.

    --audience team: exports team + org + public nodes (for shared drives)
    --audience org: exports org + public nodes (for org-wide sharing)
    --audience public: exports only public nodes (for open-source / LinkedIn)
    --audience private: exports everything (for personal backup)
    """
    store = _store(args)
    target_audience = args.audience

    if target_audience == "private":
        nodes = store.all_nodes(limit=10000)
    elif target_audience == "team":
        team = store.all_nodes(audience="team", limit=10000)
        org = store.all_nodes(audience="org", limit=10000)
        public = store.all_nodes(audience="public", limit=10000)
        seen = set()
        nodes = []
        for n in team + org + public:
            if n["id"] not in seen:
                seen.add(n["id"])
                nodes.append(n)
    elif target_audience == "org":
        org = store.all_nodes(audience="org", limit=10000)
        public = store.all_nodes(audience="public", limit=10000)
        seen = set()
        nodes = []
        for n in org + public:
            if n["id"] not in seen:
                seen.add(n["id"])
                nodes.append(n)
    else:  # public
        nodes = store.all_nodes(audience="public", limit=10000)

    # Apply PII stripping for public/org exports
    strip_pii = target_audience in ("public", "org")

    # Strip edges that cross audience boundaries
    output = []
    node_ids = {n["id"] for n in nodes}
    for n in nodes:
        if strip_pii:
            n = _strip_pii(n)
        edges = store.edges_from(n["id"])
        # Only keep edges where target is in our exported set
        filtered_edges = [e for e in edges if e["to_id"] in node_ids]

        output.append({
            "id": n["id"], "type": n["type"], "title": n["title"],
            "content": n.get("content", ""),
            "weight": n["weight"], "domains": n.get("domains", []),
            "audience": n.get("audience", "private"),
            "edges": [{"to": e["to_id"], "type": e["type"], "weight": e["weight"]}
                      for e in filtered_edges],
        })

    if args.format == "jsonl":
        for item in output:
            print(_dumps(item))
    else:
        print(_dumps(output, indent=2))

    print(f"\nExported {len(output)} nodes.", file=sys.stderr)
    store.close()


# ── ingest ────────────────────────────────────────────────────────────

def cmd_ingest(args):
    """Ingest knowledge from external sources via adapter protocol."""
    from .adapters.pipeline import IngestConfig, run_adapter, run_all
    from .adapters.registry import discover, get

    store = _store(args)
    cfg = _config(args)
    source = args.source

    config = IngestConfig(
        since=getattr(args, "since", None),
        limit=getattr(args, "limit", 50) or 50,
        verbose=True,
    )

    # Collect adapter-specific kwargs
    extra = {}
    for key in ("repo", "repo_path", "team", "directory"):
        val = getattr(args, key, None)
        if val is not None:
            extra[key] = val
    # Pass config for adapters that need it (projects, sessions)
    extra["_config"] = cfg

    if source == "all":
        results = run_all(store, config, **extra)
        total_created = sum(r.created for r in results.values())
        total_updated = sum(r.updated for r in results.values())
        print(f"\n{total_created} created, {total_updated} updated across {len(results)} adapter(s).")
    else:
        adapter = get(source)
        if not adapter:
            adapters = discover()
            names = ", ".join(sorted(adapters.keys()))
            print(f"Unknown adapter: {source}. Available: {names}, all",
                  file=sys.stderr)
            sys.exit(1)
        result = run_adapter(adapter, store, config, **extra)
        if result.errors:
            for err in result.errors:
                print(f"Error: {err}", file=sys.stderr)
            sys.exit(1)
        print(f"\n{adapter.meta.name}: {result}")

    store.close()


# ── git-hook ──────────────────────────────────────────────────────────

def cmd_git_hook(args):
    """Install or uninstall Kindex git hooks in a repository."""
    from .adapters.git_hooks import install_hooks, uninstall_hooks

    action = args.hook_action
    repo_path = getattr(args, "repo_path", ".") or "."

    if action == "install":
        cfg = _config(args)
        actions = install_hooks(repo_path, cfg)
        for a in actions:
            print(f"  {a}")
    elif action == "uninstall":
        actions = uninstall_hooks(repo_path)
        for a in actions:
            print(f"  {a}")
    else:
        print(f"Unknown action: {action}. Use: install, uninstall", file=sys.stderr)


# ── trail ─────────────────────────────────────────────────────────────

def cmd_trail(args):
    """Show temporal history and connections for a node."""
    store = _store(args)
    node = store.get_node(args.node_id) or store.get_node_by_title(args.node_id)

    if not node:
        print(f"Error: '{args.node_id}' not found.", file=sys.stderr)
        sys.exit(1)

    edges_out = store.edges_from(node["id"])
    edges_in = store.edges_to(node["id"])

    # Fetch activity log entries for this node
    node_activity = store.activity_since("1970-01-01")
    node_activity = [
        e for e in node_activity
        if e.get("target_id") == node["id"]
        or (e.get("target_id") or "").startswith(node["id"] + "->")
        or (e.get("target_id") or "").endswith("->" + node["id"])
    ]

    if args.json:
        print(_dumps({
            "node": {"id": node["id"], "title": node["title"], "type": node["type"]},
            "created": node.get("created_at"),
            "updated": node.get("updated_at"),
            "accessed": node.get("last_accessed"),
            "weight": node.get("weight"),
            "provenance": {
                "who": node.get("prov_who", []),
                "when": node.get("prov_when"),
                "activity": node.get("prov_activity"),
                "source": node.get("prov_source"),
                "why": node.get("prov_why"),
            },
            "outgoing": [{"to": e["to_id"], "title": e.get("to_title"),
                         "type": e["type"], "weight": e["weight"]}
                        for e in edges_out],
            "incoming": [{"from": e["from_id"], "title": e.get("from_title"),
                         "type": e["type"], "weight": e["weight"]}
                        for e in edges_in],
            "activity_log": node_activity,
        }, indent=2))
    else:
        print(f"# Trail: {node['title']}")
        print(f"  Created:  {node.get('created_at', '?')}")
        print(f"  Updated:  {node.get('updated_at', '?')}")
        print(f"  Accessed: {node.get('last_accessed', '?')}")
        print(f"  Weight:   {node.get('weight', 0):.2f}")

        if node.get("prov_source"):
            print(f"  Source:   {node['prov_source']}")
        if node.get("prov_activity"):
            print(f"  Activity: {node['prov_activity']}")

        if edges_out:
            print(f"\n  Outgoing ({len(edges_out)}):")
            for e in edges_out:
                print(f"    → {e.get('to_title', e['to_id'])} [{e['type']}] w={e['weight']:.2f}")
        if edges_in:
            print(f"\n  Incoming ({len(edges_in)}):")
            for e in edges_in:
                print(f"    ← {e.get('from_title', e['from_id'])} [{e['type']}] w={e['weight']:.2f}")

        if node_activity:
            print(f"\n  Activity Log ({len(node_activity)} entries):")
            for e in node_activity:
                ts = (e.get("timestamp") or "")[:16]
                action = e.get("action", "")
                actor = e.get("actor", "")
                details = e.get("details") or {}
                actor_str = f" @{actor}" if actor else ""
                detail_str = ""
                if isinstance(details, dict):
                    fields = details.get("fields", [])
                    if fields:
                        detail_str = f" ({', '.join(fields)})"
                print(f"    {ts}  {action}{detail_str}{actor_str}")

    store.close()


# ── decay ─────────────────────────────────────────────────────────────

def cmd_decay(args):
    """Run weight decay on nodes and edges based on last access time."""
    store = _store(args)
    count = store.apply_weight_decay(
        node_half_life_days=args.node_half_life,
        edge_half_life_days=args.edge_half_life,
    )

    if args.json:
        print(_dumps({"decayed_nodes": count}))
    else:
        print(f"Weight decay applied: {count} node(s) adjusted.")

    store.close()


# ── compact-hook ──────────────────────────────────────────────────────

def cmd_compact_hook(args):
    """Pre-compact hook: capture session discoveries before context compaction.

    Reads from stdin or --text, extracts knowledge, and adds to graph.
    Designed to be called by Claude Code's PreCompact hook.
    """
    store = _store(args)
    ledger, cfg = _ledger(args)

    text = args.text
    if not text:
        if not sys.stdin.isatty():
            text = sys.stdin.read()
        else:
            print("No text provided. Use --text or pipe via stdin.", file=sys.stderr)
            store.close()
            return

    if not text or len(text.strip()) < 10:
        store.close()
        return

    from .extract import extract

    existing = [n["title"] for n in store.all_nodes(limit=200)]
    extraction = extract(text, existing, cfg, ledger)

    count = 0
    for concept in extraction.get("concepts", []):
        if not store.get_node_by_title(concept["title"]):
            store.add_node(
                title=concept["title"],
                content=concept.get("content", ""),
                node_type=concept.get("type", "concept"),
                domains=concept.get("domains", []),
                prov_activity="compact-hook",
                prov_source="pre-compact",
            )
            count += 1

    for conn in extraction.get("connections", []):
        from_node = store.get_node_by_title(conn.get("from_title", ""))
        to_node = store.get_node_by_title(conn.get("to_title", ""))
        if from_node and to_node:
            store.add_edge(from_node["id"], to_node["id"],
                           edge_type=conn.get("type", "relates_to"),
                           provenance="compact-hook")

    # Output context at executive level for re-injection after compaction
    if count > 0 or args.emit_context:
        from .retrieve import format_context_block, hybrid_search
        topic = text[:100].split("\n")[0]
        results = hybrid_search(store, topic, top_k=5)
        block = format_context_block(store, results, query=topic, level="executive")
        print(block)

    store.close()


# ── prime ─────────────────────────────────────────────────────────────

def cmd_prime(args):
    """Generate context injection for Claude Code SessionStart hook.

    kin prime [--topic TOPIC] [--tokens N] [--for hook|stdout] [--codebook]
    """
    store = _store(args)

    if getattr(args, "codebook", False):
        _prime_codebook(store, args)
        store.close()
        return

    from .hooks import prime_context

    topic = getattr(args, "topic", None)
    tokens = getattr(args, "tokens", 750) or 750
    output_for = getattr(args, "output_for", "stdout") or "stdout"

    block = prime_context(store, topic=topic, max_tokens=tokens)

    if output_for == "hook":
        # Just the context block, no header
        print(block, end="")
    else:
        # Add a header for human-readable output
        print("# Kindex Prime Context")
        print(f"# Topic: {topic or '(auto-detected)'}")
        print(f"# Max tokens: {tokens}")
        print()
        print(block)

    store.close()


def _prime_codebook(store, args):
    """Regenerate the LLM prompt cache codebook."""
    from .retrieve import generate_codebook

    cfg = _config(args)
    min_weight = cfg.llm.codebook_min_weight
    text, hash_val = generate_codebook(store, min_weight=min_weight)

    old_hash = store.get_meta("codebook_hash")
    store.set_meta("codebook_text", text)
    store.set_meta("codebook_hash", hash_val)
    from datetime import datetime
    store.set_meta("codebook_generated_at", datetime.now().isoformat())

    # Track node count for staleness detection
    stats = store.stats() if hasattr(store, "stats") else {}
    node_count = stats.get("nodes", 0) if isinstance(stats, dict) else 0
    store.set_meta("codebook_node_count", str(node_count))

    entry_count = text.count("\n#")
    est_tokens = len(text) // 4

    if old_hash and old_hash == hash_val:
        print(f"Codebook unchanged (hash: {hash_val})")
    elif old_hash:
        print(f"Codebook updated: {hash_val} (was: {old_hash})")
    else:
        print(f"Codebook created: {hash_val}")
    print(f"  {entry_count} entries, ~{est_tokens} tokens")
    print(f"  Min weight: {min_weight}")


# ── suggest ───────────────────────────────────────────────────────────

def cmd_suggest(args):
    """Show and manage bridge opportunity suggestions.

    kin suggest [--accept ID] [--reject ID] [--limit N]
    """
    store = _store(args)

    accept_id = getattr(args, "accept", None)
    reject_id = getattr(args, "reject", None)
    limit = getattr(args, "limit", 20) or 20

    if accept_id is not None:
        # Accept: create the edge between the two concepts
        suggestions = store.pending_suggestions(limit=1000)
        suggestion = None
        for s in suggestions:
            if s["id"] == accept_id:
                suggestion = s
                break

        if not suggestion:
            # Also check non-pending in case of confusion
            print(f"Suggestion {accept_id} not found or already processed.", file=sys.stderr)
            store.close()
            return

        # Resolve concept titles to nodes
        node_a = store.get_node_by_title(suggestion["concept_a"])
        node_b = store.get_node_by_title(suggestion["concept_b"])

        if node_a and node_b:
            store.add_edge(
                node_a["id"], node_b["id"],
                edge_type="relates_to",
                provenance=f"suggestion: {suggestion.get('reason', '')}",
            )
            store.update_suggestion(accept_id, "accepted")
            print(f"Accepted: {suggestion['concept_a']} <-> {suggestion['concept_b']}")
            print(f"  Edge created: {node_a['title']} -> {node_b['title']}")
        else:
            missing = []
            if not node_a:
                missing.append(suggestion["concept_a"])
            if not node_b:
                missing.append(suggestion["concept_b"])
            print(f"Cannot accept: node(s) not found: {', '.join(missing)}", file=sys.stderr)
            print("Create the nodes first, then accept the suggestion.", file=sys.stderr)

        store.close()
        return

    if reject_id is not None:
        store.update_suggestion(reject_id, "rejected")
        print(f"Rejected suggestion {reject_id}.")
        store.close()
        return

    # List pending suggestions
    suggestions = store.pending_suggestions(limit=limit)

    if not suggestions:
        print("No pending suggestions.")
        store.close()
        return

    if args.json:
        print(_dumps(suggestions, indent=2))
    else:
        print(f"# Bridge Opportunities ({len(suggestions)} pending)\n")
        for s in suggestions:
            sid = s["id"]
            ca = s["concept_a"]
            cb = s["concept_b"]
            reason = s.get("reason", "")
            source = s.get("source", "")
            created = (s.get("created_at") or "")[:16]

            print(f"  [{sid}] {ca} <-> {cb}")
            if reason:
                print(f"       Why: {reason}")
            if source:
                print(f"       Source: {source}")
            if created:
                print(f"       Created: {created}")
            print()

        print(f"Accept: kin suggest --accept <ID>")
        print(f"Reject: kin suggest --reject <ID>")

    store.close()


# ── log ───────────────────────────────────────────────────────────────

def cmd_log(args):
    """Show recent activity log."""
    store = _store(args)
    entries = store.recent_activity(limit=args.n)

    if not entries:
        print("No activity logged yet.")
        store.close()
        return

    if args.json:
        print(_dumps(entries, indent=2))
    else:
        for e in entries:
            ts = (e.get("timestamp") or "")[:16]
            action = e.get("action", "")
            target = e.get("target_title") or e.get("target_id", "")
            actor = e.get("actor", "")
            actor_str = f" @{actor}" if actor else ""
            print(f"  {ts}  {action:15s} {target[:45]}{actor_str}")

    store.close()


# ── changelog ─────────────────────────────────────────────────────────

def cmd_changelog(args):
    """Show what changed in the graph since a date or over the last N days."""
    store = _store(args)

    # Determine the since timestamp
    if args.since:
        since_iso = args.since
    else:
        days = args.days or 7
        since_dt = datetime.datetime.now() - datetime.timedelta(days=days)
        since_iso = since_dt.isoformat(timespec="seconds")

    # Fetch activity, optionally filtered by actor
    if args.actor:
        entries = store.activity_by_actor(args.actor)
        # Further filter by timestamp
        entries = [e for e in entries if (e.get("timestamp") or "") >= since_iso]
    else:
        entries = store.activity_since(since_iso)

    if not entries:
        if args.json:
            print(_dumps({"since": since_iso, "groups": {}, "total": 0}))
        else:
            days_label = args.days or 7
            if args.since:
                print(f"# Changelog (since {args.since})\n\nNo activity found.")
            else:
                print(f"# Changelog (last {days_label} days)\n\nNo activity found.")
        store.close()
        return

    # Group entries by action type, mapping to display categories
    action_map = {
        "add_node": "Added",
        "update_node": "Updated",
        "delete_node": "Deleted",
        "add_edge": "Linked",
    }
    groups: dict[str, list[dict]] = {}
    for e in entries:
        action = e.get("action", "unknown")
        label = action_map.get(action, action)
        groups.setdefault(label, []).append(e)

    if args.json:
        print(_dumps({
            "since": since_iso,
            "actor": args.actor or None,
            "groups": {k: v for k, v in groups.items()},
            "total": len(entries),
        }, indent=2))
    else:
        days_label = args.days or 7
        if args.since:
            print(f"# Changelog (since {args.since})")
        else:
            print(f"# Changelog (last {days_label} days)")
        if args.actor:
            print(f"  Actor: {args.actor}")
        print()

        # Display order: Added, Updated, Deleted, Linked, then anything else
        display_order = ["Added", "Updated", "Deleted", "Linked"]
        all_labels = display_order + [k for k in groups if k not in display_order]

        for label in all_labels:
            if label not in groups:
                continue
            items = groups[label]
            # Determine count label
            if label in ("Linked",):
                count_label = f"{len(items)} edges"
            else:
                count_label = f"{len(items)} nodes"

            print(f"## {label} ({count_label})")
            for e in items:
                ts = (e.get("timestamp") or "")[:10]
                target_title = e.get("target_title") or e.get("target_id", "")
                details = e.get("details") or {}

                if label == "Linked":
                    # Show edge details: from -> to [type]
                    edge_type = details.get("type", "relates_to")
                    target = e.get("target_id", "")
                    print(f"  {ts}  {target} [{edge_type}]")
                elif label == "Updated":
                    fields = details.get("fields", [])
                    field_str = f" ({', '.join(fields)})" if fields else ""
                    print(f"  {ts}  Updated{field_str}: {target_title}")
                else:
                    ntype = details.get("type", "")
                    type_str = f"[{ntype}] " if ntype else ""
                    print(f"  {ts}  {type_str}{target_title}")
            print()

    store.close()


# ── graph ─────────────────────────────────────────────────────────────

def cmd_graph(args):
    """Graph analytics — stats, centrality, communities, bridges, trailheads."""
    store = _store(args)

    from .graph import (
        store_bridges, store_centrality, store_communities,
        store_stats, store_trailheads,
    )

    mode = args.graph_mode or "stats"

    if mode == "stats":
        stats = store_stats(store)
        if args.json:
            print(_dumps(stats, indent=2))
        else:
            print(f"Graph Statistics")
            print(f"  Nodes:      {stats['nodes']}")
            print(f"  Edges:      {stats['edges']}")
            print(f"  Density:    {stats['density']}")
            print(f"  Components: {stats['components']}")
            print(f"  Avg degree: {stats['avg_degree']}")
            if stats['max_degree_node']:
                print(f"  Hub:        {stats['max_degree_node']} (degree {stats['max_degree']})")

    elif mode == "centrality":
        method = args.method or "betweenness"
        results = store_centrality(store, method=method, top_k=args.top_k or 20)
        if args.json:
            print(_dumps([{"id": nid, "title": t, "score": s}
                          for nid, t, s in results], indent=2))
        else:
            print(f"Centrality ({method})")
            for nid, title, score in results:
                bar = "█" * int(score * 40)
                print(f"  {score:.4f} {bar:20s} {title[:50]}")

    elif mode == "communities":
        comms = store_communities(store)
        if args.json:
            print(_dumps(comms, indent=2))
        else:
            print(f"{len(comms)} communities detected")
            for i, comm in enumerate(comms):
                members = ", ".join(m["title"][:30] for m in comm[:5])
                extra = f" +{len(comm)-5} more" if len(comm) > 5 else ""
                print(f"  {i+1}. [{len(comm)} nodes] {members}{extra}")

    elif mode == "bridges":
        bridges = store_bridges(store, top_k=args.top_k or 10)
        if args.json:
            print(_dumps(bridges, indent=2))
        else:
            print("Bridge edges (critical connections)")
            for b in bridges:
                print(f"  {b['from_title'][:25]:25s} <-> {b['to_title'][:25]:25s}  "
                      f"btw={b['betweenness']}")

    elif mode == "trailheads":
        trails = store_trailheads(store, top_k=args.top_k or 10)
        if args.json:
            print(_dumps(trails, indent=2))
        else:
            print("Trailheads (entry points)")
            for t in trails:
                print(f"  [{t['type'][:4]}] {t['title'][:40]:40s}  "
                      f"score={t['score']}  out={t['out_degree']}  btw={t['betweenness']}")

    store.close()


# ── analytics ─────────────────────────────────────────────────────────

def cmd_analytics(args):
    """Archive analytics — session stats and activity heatmap."""
    cfg = _config(args)

    from .analytics import activity_heatmap, find_archive_db, session_stats

    show_heatmap = getattr(args, "heatmap", False)
    show_sessions = getattr(args, "sessions", False)
    days = getattr(args, "days", 90) or 90

    # Default: show sessions if neither flag is set
    if not show_heatmap and not show_sessions:
        show_sessions = True

    if show_sessions:
        stats = session_stats(cfg)
        if "error" in stats:
            db_path = find_archive_db(cfg)
            if not db_path:
                print(f"Error: {stats['error']}", file=sys.stderr)
                print(f"Searched: {cfg.claude_path / 'archive'}", file=sys.stderr)
                sys.exit(1)

        if args.json:
            print(_dumps(stats, indent=2))
        else:
            print("# Archive Session Stats\n")
            print(f"Total sessions: {stats.get('total_sessions', 0)}")

            by_month = stats.get("sessions_by_month", {})
            if by_month:
                print("\n## Sessions by Month")
                for month, count in sorted(by_month.items(), reverse=True):
                    bar = "█" * min(count, 40)
                    print(f"  {month}  {bar} {count}")

            top_projects = stats.get("top_projects", {})
            if top_projects:
                print("\n## Top Projects")
                for proj, count in top_projects.items():
                    print(f"  {proj:30s} {count}")

    if show_heatmap:
        heatmap = activity_heatmap(cfg, days=days)
        if "error" in heatmap:
            print(f"Error: {heatmap['error']}", file=sys.stderr)
            sys.exit(1)

        if args.json:
            print(_dumps(heatmap, indent=2))
        else:
            print(f"\n# Activity Heatmap (last {days} days)\n")
            grid = heatmap.get("grid", {})
            # Header row: hours
            hours_header = "          " + "".join(f"{h:3d}" for h in range(24))
            print(hours_header)
            for day_name in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]:
                row = grid.get(day_name, {})
                cells = []
                for h in range(24):
                    v = row.get(h, 0)
                    if v == 0:
                        cells.append("  .")
                    elif v < 3:
                        cells.append("  o")
                    elif v < 6:
                        cells.append("  O")
                    else:
                        cells.append("  #")
                print(f"  {day_name:3s}   {''.join(cells)}")
            print("\n  Legend: . = 0, o = 1-2, O = 3-5, # = 6+")


# ── index ─────────────────────────────────────────────────────────────

def cmd_index(args):
    """Write .kin/index.json summarizing the graph for git tracking."""
    store = _store(args)

    from .ingest import write_kin_index

    output_dir = Path(getattr(args, "output_dir", None) or os.getcwd())
    path = write_kin_index(store, output_dir)
    print(f"Wrote {path}")
    print(f"  ({path.stat().st_size} bytes)")

    store.close()


# ── sync-links ────────────────────────────────────────────────────────

def cmd_sync_links(args):
    """Update each node's content with a '## Connections' section.

    Reads all nodes, finds their outgoing edges, and appends (or replaces)
    a ``## Connections`` section in the node content listing linked nodes.
    Reports how many nodes were updated.
    """
    import re as _re

    store = _store(args)
    nodes = store.all_nodes(limit=5000)
    updated = 0

    for node in nodes:
        edges = store.edges_from(node["id"])
        if not edges:
            continue

        # Build the connections section
        lines = ["## Connections", ""]
        for edge in edges:
            label = edge.get("to_title") or edge.get("to_id", "?")
            etype = edge.get("type", "relates_to")
            lines.append(f"- **{label}** ({etype})")
        connections_block = "\n".join(lines)

        content = node.get("content") or ""

        # Strip any previous Connections section so we don't duplicate
        content = _re.sub(
            r"(?m)^## Connections\n(?:.*\n)*?(?=^## |\Z)",
            "",
            content,
        ).rstrip()

        # Append the new connections section
        if content:
            new_content = content + "\n\n" + connections_block + "\n"
        else:
            new_content = connections_block + "\n"

        store.update_node(node["id"], content=new_content)
        updated += 1

    print(f"Updated {updated} node(s) with connection references.")
    store.close()


# ── alias ─────────────────────────────────────────────────────────────

def cmd_alias(args):
    """Manage AKA/synonyms for a node.

    kin alias <node> add <alias>    — add a synonym
    kin alias <node> remove <alias> — remove a synonym
    kin alias <node> list           — show all aliases
    """
    store = _store(args)
    node = store.get_node(args.node_id) or store.get_node_by_title(args.node_id)

    if not node:
        print(f"Error: '{args.node_id}' not found.", file=sys.stderr)
        sys.exit(1)

    aka = list(node.get("aka") or [])
    action = args.alias_action

    if action == "list":
        if aka:
            print(f"Aliases for {node['title']}:")
            for a in aka:
                print(f"  - {a}")
        else:
            print(f"No aliases for {node['title']}.")
        store.close()
        return

    if action == "add":
        if not args.alias_value:
            print("Error: kin alias <node> add <alias>", file=sys.stderr)
            sys.exit(1)
        new_alias = args.alias_value
        if new_alias not in aka:
            aka.append(new_alias)
            store.update_node(node["id"], aka=aka)
            print(f"Added alias '{new_alias}' to {node['title']}")
        else:
            print(f"'{new_alias}' is already an alias for {node['title']}")

    elif action == "remove":
        if not args.alias_value:
            print("Error: kin alias <node> remove <alias>", file=sys.stderr)
            sys.exit(1)
        old_alias = args.alias_value
        if old_alias in aka:
            aka.remove(old_alias)
            store.update_node(node["id"], aka=aka)
            print(f"Removed alias '{old_alias}' from {node['title']}")
        else:
            print(f"'{old_alias}' is not an alias for {node['title']}")

    store.close()


# ── whoami ────────────────────────────────────────────────────────────

def cmd_whoami(args):
    """Show the current user identity used for --mine filtering."""
    cfg = _config(args)
    print(cfg.current_user)


# ── embed ─────────────────────────────────────────────────────────────

def cmd_embed(args):
    """Index all nodes for vector similarity search.

    Requires: pip install kindex[vectors]
    """
    store = _store(args)

    try:
        from .vectors import index_all_nodes
        count = index_all_nodes(store, verbose=getattr(args, "verbose", False))
        print(f"Embedded {count} nodes for vector search.")
    except Exception as e:
        print(f"Vector indexing failed: {e}", file=sys.stderr)
        print("Install dependencies: pip install kindex[vectors]", file=sys.stderr)

    store.close()


# ── ask ───────────────────────────────────────────────────────────────


def _classify_question(question: str) -> str:
    """Classify a question by type using keyword heuristics.

    Returns one of: 'procedural', 'decision', 'factual', 'exploratory'.
    """
    q = question.lower().strip()

    # Procedural: how-to questions
    procedural_patterns = [
        "how do i ", "how to ", "how can i ", "how should i ",
        "steps to ", "way to ", "guide to ", "instructions for ",
    ]
    for pat in procedural_patterns:
        if pat in q or q.startswith(pat.strip()):
            return "procedural"

    # Decision: comparison / choice questions
    decision_patterns = [
        "should i ", "which is better", "which one", "compare ",
        "vs ", " or ", "trade-off", "tradeoff", "pros and cons",
        "advantage", "disadvantage", "prefer ",
    ]
    for pat in decision_patterns:
        if pat in q:
            return "decision"

    # Factual: definitional / lookup questions
    factual_patterns = [
        "what is ", "what are ", "what was ", "what does ",
        "who is ", "who are ", "who was ",
        "when did ", "when was ", "when is ",
        "where is ", "where did ", "where are ",
        "define ", "definition of ",
    ]
    for pat in factual_patterns:
        if pat in q or q.startswith(pat.strip()):
            return "factual"

    return "exploratory"


def cmd_ask(args):
    """Query the knowledge graph with natural language.

    Uses LLM if available, otherwise falls back to search + context formatting.
    Classifies the question type to improve search and output.
    """
    store = _store(args)
    question = " ".join(args.question)
    qtype = _classify_question(question)

    from .retrieve import format_context_block, hybrid_search

    # Adjust top_k based on question type
    top_k_map = {
        "factual": 5,
        "procedural": 8,
        "decision": 10,
        "exploratory": 12,
    }
    top_k = top_k_map.get(qtype, 10)

    results = hybrid_search(store, question, top_k=top_k)

    if not results:
        print("No relevant knowledge found.", file=sys.stderr)
        store.close()
        return

    # Try LLM-powered answer
    ledger, cfg = _ledger(args)
    answer = _ask_llm(question, results, cfg, ledger, qtype=qtype, store=store)

    if answer:
        print(answer)
    else:
        # Fallback: show classified context
        level_map = {
            "factual": "abridged",
            "procedural": "full",
            "decision": "full",
            "exploratory": "abridged",
        }
        level = level_map.get(qtype, "abridged")
        block = format_context_block(store, results, query=question, level=level)
        print(f"[{qtype} question] (No LLM available — showing search results)\n")
        print(block)

    store.close()


_STYLE_HINTS = {
    "factual": "Give a direct, concise factual answer.",
    "procedural": "Provide clear step-by-step instructions.",
    "decision": "Compare the options and give a recommendation with trade-offs.",
    "exploratory": "Provide a broad overview touching on the key aspects.",
}

_SYSTEM_PREAMBLE = (
    "You are Kindex, a knowledge graph assistant. "
    "Below is a codebook listing nodes in the user's knowledge graph. "
    "Use it as a lookup table — identify relevant entries by their # number. "
    "Detailed context for query-relevant nodes follows the codebook.\n\n"
)


def _ask_llm(question: str, results: list[dict], config, ledger,
             qtype: str = "exploratory", store=None) -> str | None:
    """Use LLM to answer a question given graph context.

    Routes to cache-optimized path (three-tier with cache_control breakpoints)
    or flat path (single user message) based on config.
    """
    if not config.llm.enabled:
        return None
    if not ledger.can_spend():
        return None

    from .llm import get_client, calculate_cost
    client = get_client(config)
    if client is None:
        return None

    use_cache = (config.llm.cache_control
                 and config.llm.provider == "anthropic"
                 and store is not None)

    if use_cache:
        return _ask_llm_cached(question, results, config, ledger, client, store, qtype)
    return _ask_llm_flat(question, results, config, ledger, client, qtype)


def _ask_llm_flat(question, results, config, ledger, client, qtype):
    """Original flat message format (no caching)."""
    from .llm import calculate_cost

    context_parts = []
    for r in results[:5]:
        title = r.get("title", r["id"])
        content = (r.get("content") or "")[:500]
        ntype = r.get("type", "concept")
        context_parts.append(f"[{ntype}] {title}: {content}")
    context = "\n\n".join(context_parts)
    style = _STYLE_HINTS.get(qtype, _STYLE_HINTS["exploratory"])

    try:
        response = client.messages.create(
            model=config.llm.model,
            max_tokens=500,
            messages=[{"role": "user", "content": f"""Based on this knowledge graph context:

{context}

Answer this question concisely: {question}

{style}

If the context doesn't contain enough information, say so honestly."""}],
        )
        cost_info = calculate_cost(config.llm.model, response.usage)
        ledger.record(**cost_info, model=config.llm.model, purpose="ask")
        return response.content[0].text
    except Exception:
        return None


def _ask_llm_cached(question, results, config, ledger, client, store, qtype):
    """Three-tier cached message format with cache_control breakpoints."""
    from .llm import calculate_cost
    from .retrieve import (build_codebook_index, format_tier2,
                           generate_codebook, predict_tier2)

    # Tier 1: Load or auto-generate codebook
    codebook_text = store.get_meta("codebook_text")
    if not codebook_text:
        codebook_text, codebook_hash = generate_codebook(
            store, min_weight=config.llm.codebook_min_weight)
        store.set_meta("codebook_text", codebook_text)
        store.set_meta("codebook_hash", codebook_hash)
    else:
        # Staleness check
        import json
        stats = store.stats() if hasattr(store, "stats") else {}
        node_count = stats.get("nodes", 0) if isinstance(stats, dict) else 0
        old_count_raw = store.get_meta("codebook_node_count")
        old_count = int(old_count_raw) if old_count_raw else 0
        if old_count and node_count > old_count * 1.1:
            print("Hint: codebook may be stale. Run: kin prime --codebook",
                  file=sys.stderr)

    codebook_index = build_codebook_index(codebook_text)

    # Tier 2: Predict and format context
    tier2_results = predict_tier2(store, question, results)
    tier2_text = format_tier2(tier2_results, codebook_index,
                              max_tokens=config.llm.tier2_max_tokens)

    style = _STYLE_HINTS.get(qtype, _STYLE_HINTS["exploratory"])

    # Min cacheable tokens: 1024 for Haiku, 2048 for larger models
    model_lower = config.llm.model.lower()
    min_cache = 1024 if "haiku" in model_lower else 2048

    # Build system blocks with cache_control breakpoints
    tier1_content = _SYSTEM_PREAMBLE + codebook_text
    tier1_tokens_est = len(tier1_content) // 4

    system_blocks = []
    if tier1_tokens_est >= min_cache:
        # Tier 1 large enough to cache on its own
        system_blocks.append({
            "type": "text",
            "text": tier1_content,
            "cache_control": {"type": "ephemeral"},
        })
        if tier2_text.strip():
            tier2_tokens_est = len(tier2_text) // 4
            if tier2_tokens_est >= min_cache:
                system_blocks.append({
                    "type": "text",
                    "text": tier2_text,
                    "cache_control": {"type": "ephemeral"},
                })
            else:
                system_blocks.append({"type": "text", "text": tier2_text})
    else:
        # Combine tier 1 + tier 2 into single cached block
        combined = tier1_content + "\n\n" + tier2_text
        system_blocks.append({
            "type": "text",
            "text": combined,
            "cache_control": {"type": "ephemeral"},
        })

    # Tier 3: User message — just the question
    user_content = f"{question}\n\n{style}"

    try:
        response = client.messages.create(
            model=config.llm.model,
            max_tokens=800,
            system=system_blocks,
            messages=[{"role": "user", "content": user_content}],
        )
        cost_info = calculate_cost(config.llm.model, response.usage)
        ledger.record(**cost_info, model=config.llm.model, purpose="ask")
        return response.content[0].text
    except Exception:
        return None


# ── register ─────────────────────────────────────────────────────────

def cmd_register(args):
    """Register a file path with a knowledge node.

    Associates filesystem paths with nodes so Claude Code can find
    the actual files that relate to a concept.
    """
    store = _store(args)
    node = store.get_node(args.node_id) or store.get_node_by_title(args.node_id)

    if not node:
        print(f"Error: '{args.node_id}' not found.", file=sys.stderr)
        sys.exit(1)

    filepath = Path(args.filepath).expanduser().resolve()
    if not filepath.exists():
        print(f"Warning: '{filepath}' does not exist.", file=sys.stderr)

    # Store file path in extra metadata
    extra = node.get("extra") or {}
    paths = extra.get("file_paths", [])
    path_str = str(filepath)
    if path_str not in paths:
        paths.append(path_str)
        extra["file_paths"] = paths
        store.update_node(node["id"], extra=extra)
        print(f"Registered: {filepath} -> {node['title']}")
    else:
        print(f"Already registered: {filepath} -> {node['title']}")

    store.close()


# ── skills ─────────────────────────────────────────────────────────────

def cmd_skills(args):
    """Show skill profile for a person."""
    store = _store(args)
    cfg = _config(args)

    person_name = args.person or cfg.current_user
    person = store.get_node_by_title(person_name) or store.get_node(person_name)

    if not person:
        # Try to find any person node that matches
        persons = store.all_nodes(node_type="person", limit=100)
        for p in persons:
            if person_name.lower() in p["title"].lower():
                person = p
                break

    if not person:
        print(f"No person node found for '{person_name}'.", file=sys.stderr)
        print("Create one with: kin add --type person <name>", file=sys.stderr)
        store.close()
        return

    # Find skill edges (demonstrates)
    edges = store.edges_from(person["id"])
    skill_edges = [e for e in edges if e.get("type") == "demonstrates"]

    # Also find context_of edges to skill nodes
    for e in edges:
        if e.get("type") != "demonstrates":
            target = store.get_node(e["to_id"])
            if target and target.get("type") == "skill":
                skill_edges.append(e)

    if args.json:
        skills = []
        for e in skill_edges:
            target = store.get_node(e["to_id"])
            if target:
                skills.append({
                    "title": target["title"],
                    "weight": target["weight"],
                    "edge_weight": e["weight"],
                    "provenance": e.get("provenance", ""),
                    "last_updated": target.get("updated_at", ""),
                })
        print(_dumps({"person": person["title"], "skills": skills}, indent=2))
    else:
        print(f"# Skills: {person['title']}\n")
        if not skill_edges:
            print("  No skills recorded yet.")
            print("  Record with: kin add --type skill '<skill name>'")
        else:
            for e in skill_edges:
                target = store.get_node(e["to_id"])
                if target:
                    when = (target.get("updated_at") or "")[:10]
                    prov = e.get("provenance", "")[:40]
                    print(f"  {target['title'][:40]:40s} w={target['weight']:.2f}  {when}  {prov}")

    store.close()


# ── import ─────────────────────────────────────────────────────────────

def cmd_import_graph(args):
    """Import nodes and edges from a JSON or JSONL file."""
    store = _store(args)
    filepath = Path(args.filepath)

    if not filepath.exists():
        print(f"Error: '{filepath}' not found.", file=sys.stderr)
        sys.exit(1)

    text = filepath.read_text()
    if filepath.suffix == ".jsonl" or args.format == "jsonl":
        items = [json.loads(line) for line in text.strip().split("\n") if line.strip()]
    else:
        data = json.loads(text)
        items = data if isinstance(data, list) else [data]

    dry_run = getattr(args, "dry_run", False)
    merge = getattr(args, "mode", "merge") == "merge"
    created = updated = edges_created = skipped = 0

    for item in items:
        title = item.get("title", "")
        node_id = item.get("id", "")

        if not title and not node_id:
            skipped += 1
            continue

        existing = None
        if node_id:
            existing = store.get_node(node_id)
        if not existing and title:
            existing = store.get_node_by_title(title)

        if existing:
            if merge:
                # Merge: update content if new content is provided
                new_content = item.get("content", "")
                old_content = existing.get("content", "")
                if new_content and new_content != old_content:
                    if not dry_run:
                        combined = old_content + "\n\n" + new_content if old_content else new_content
                        store.update_node(existing["id"], content=combined)
                    updated += 1
                    if dry_run:
                        print(f"  Would update: {title}")
                else:
                    skipped += 1
            else:
                # Replace
                if not dry_run:
                    store.update_node(existing["id"],
                                      title=title,
                                      content=item.get("content", ""),
                                      weight=item.get("weight", existing["weight"]))
                updated += 1
                if dry_run:
                    print(f"  Would replace: {title}")
        else:
            if not dry_run:
                store.add_node(
                    title=title,
                    content=item.get("content", ""),
                    node_id=node_id or None,
                    node_type=item.get("type", "concept"),
                    domains=item.get("domains", []),
                    weight=item.get("weight", 0.5),
                    audience=item.get("audience", "private"),
                    prov_activity="import",
                    prov_source=str(filepath),
                )
            created += 1
            if dry_run:
                print(f"  Would create: {title}")

        # Process edges
        for edge in item.get("edges", []):
            to_id = edge.get("to", "")
            if not to_id:
                continue
            from_id = node_id or (existing["id"] if existing else "")
            if not from_id:
                continue
            # Check if target exists
            target = store.get_node(to_id) or store.get_node_by_title(to_id)
            if target and not dry_run:
                store.add_edge(from_id, target["id"],
                               edge_type=edge.get("type", "relates_to"),
                               weight=edge.get("weight", 0.5),
                               provenance="import")
                edges_created += 1

    prefix = "[DRY RUN] " if dry_run else ""
    print(f"{prefix}Import complete: {created} created, {updated} updated, "
          f"{edges_created} edges, {skipped} skipped")
    store.close()


# ── cron ──────────────────────────────────────────────────────────────

def cmd_cron(args):
    """Run one-shot maintenance cycle (designed for crontab)."""
    store = _store(args)
    cfg = _config(args)
    verbose = getattr(args, "verbose", False)

    from .daemon import cron_run

    results = cron_run(cfg, store, verbose=verbose)

    if args.json:
        print(_dumps(results, indent=2))
    else:
        print("Cron maintenance complete:")
        print(f"  Projects scanned:  {results.get('projects', 0)}")
        print(f"  .kin updates:      {results.get('kin_updates', 0)}")
        print(f"  Sessions ingested: {results.get('sessions', 0)}")
        print(f"  Inbox processed:   {results.get('inbox', 0)}")
        print(f"  Nodes decayed:     {results.get('decayed', 0)}")
        stats = results.get("stats", {})
        print(f"  Graph: {stats.get('nodes', 0)} nodes, "
              f"{stats.get('edges', 0)} edges, "
              f"{results.get('orphan_count', 0)} orphans")
        repack = results.get("repack", {})
        if repack:
            interval = repack.get("interval", "?")
            action = repack.get("action", "?")
            if action == "unchanged":
                print(f"  Cron interval: {interval}s (unchanged)")
            elif action == "updated":
                print(f"  Cron interval: {repack.get('previous', '?')}s -> {interval}s (adaptive)")
            elif action == "disabled":
                print(f"  Cron interval: disabled (no pending reminders)")

    store.close()


# ── watch ─────────────────────────────────────────────────────────────

def cmd_watch(args):
    """Watch for new sessions and ingest them (long-running)."""
    import time

    store = _store(args)
    cfg = _config(args)
    interval = getattr(args, "interval", 60) or 60
    verbose = getattr(args, "verbose", False)

    from .daemon import find_new_sessions, incremental_ingest, set_run_marker

    # Start from now (or last run marker)
    from .daemon import last_run_marker as _last_run
    since = _last_run(cfg)
    if not since:
        import datetime as _dt
        since = _dt.datetime.now(tz=None).isoformat(timespec="seconds")

    print(f"Watching for new sessions (every {interval}s). Ctrl+C to stop.")
    print(f"  Since: {since}")

    try:
        while True:
            new_files = find_new_sessions(cfg, since)
            if new_files:
                count = incremental_ingest(cfg, store, since, verbose=verbose)
                if count > 0:
                    print(f"  [{_now_short()}] Ingested {count} new session(s)")
                    set_run_marker(store)

                # Update the since marker to now
                import datetime as _dt
                since = _dt.datetime.now(tz=None).isoformat(timespec="seconds")
            elif verbose:
                print(f"  [{_now_short()}] No new sessions")

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nWatch stopped.")
    finally:
        store.close()


def _now_short() -> str:
    """Short timestamp for watch output."""
    import datetime as _dt
    return _dt.datetime.now(tz=None).strftime("%H:%M:%S")


# ── session tags ──────────────────────────────────────────────────────


def cmd_remind(args):
    """Reminder management — create, list, snooze, done, cancel, check."""
    store = _store(args)
    cfg = _config(args)
    action = getattr(args, "remind_action", None)

    if action == "create" or action is None:
        from .reminders import create_reminder
        title = " ".join(getattr(args, "title_words", []) or [])
        time_spec = getattr(args, "at", None)
        if not title or not time_spec:
            print("Usage: kin remind create <title> --at <time>", file=sys.stderr)
            store.close()
            return
        try:
            rid = create_reminder(
                store, title, time_spec,
                priority=getattr(args, "priority", None) or "normal",
                channels=([c.strip() for c in args.channel.split(",") if c.strip()]
                          if getattr(args, "channel", None) else None),
                tags=getattr(args, "tag_str", "") or "",
                action_command=getattr(args, "action_command", "") or "",
                action_instructions=getattr(args, "action_instructions", "") or "",
                action_mode=getattr(args, "action_mode", "auto") or "auto",
            )
            r = store.get_reminder(rid)
            if getattr(args, "json", False):
                print(_dumps(r))
            else:
                print(f"Created reminder: {rid} (due: {r['next_due']})")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)

    elif action == "list":
        from .reminders import format_reminder_list
        status_filter = getattr(args, "status", None)
        if status_filter == "all":
            status_filter = None
        reminders = store.list_reminders(
            status=status_filter,
            priority=getattr(args, "priority", None),
        )
        if getattr(args, "json", False):
            print(_dumps(reminders))
        elif not reminders:
            print("No reminders.")
        else:
            print(format_reminder_list(reminders))

    elif action == "show":
        from .reminders import format_reminder
        rid = getattr(args, "reminder_id", None)
        if not rid:
            print("Usage: kin remind show --reminder-id <id>", file=sys.stderr)
            store.close()
            return
        r = store.get_reminder(rid)
        if r:
            if getattr(args, "json", False):
                print(_dumps(r))
            else:
                print(format_reminder(r))
        else:
            print(f"Reminder not found: {rid}", file=sys.stderr)

    elif action == "snooze":
        from .reminders import snooze_reminder, parse_duration
        rid = getattr(args, "reminder_id", None)
        if not rid:
            print("Usage: kin remind snooze --reminder-id <id>", file=sys.stderr)
            store.close()
            return
        duration = getattr(args, "duration", None)
        duration_secs = parse_duration(duration) if duration else None
        try:
            new_time = snooze_reminder(store, rid, duration_secs, cfg)
            print(f"Snoozed until: {new_time}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)

    elif action == "done":
        from .reminders import complete_reminder
        rid = getattr(args, "reminder_id", None)
        if not rid:
            print("Usage: kin remind done --reminder-id <id>", file=sys.stderr)
            store.close()
            return
        try:
            complete_reminder(store, rid)
            print(f"Completed: {rid}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)

    elif action == "cancel":
        from .reminders import cancel_reminder
        rid = getattr(args, "reminder_id", None)
        if not rid:
            print("Usage: kin remind cancel --reminder-id <id>", file=sys.stderr)
            store.close()
            return
        try:
            cancel_reminder(store, rid)
            print(f"Cancelled: {rid}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)

    elif action == "exec":
        from .actions import execute_action, has_action
        rid = getattr(args, "reminder_id", None)
        if not rid:
            print("Usage: kin remind exec --reminder-id <id>", file=sys.stderr)
            store.close()
            return
        r = store.get_reminder(rid)
        if not r:
            print(f"Reminder not found: {rid}", file=sys.stderr)
            store.close()
            return
        if not has_action(r):
            print(f"Reminder {rid} has no action defined.", file=sys.stderr)
            store.close()
            return
        result = execute_action(store, r, cfg)
        if getattr(args, "json", False):
            print(_dumps(result))
        else:
            print(f"Action {result['status']}: {result.get('output', '')[:200]}")

    elif action == "check":
        from .reminders import auto_snooze_stale, check_and_fire
        fired = check_and_fire(store, cfg)
        snoozed = auto_snooze_stale(store, cfg)
        if getattr(args, "json", False):
            print(_dumps({"fired": len(fired), "auto_snoozed": snoozed}))
        else:
            print(f"Checked: {len(fired)} fired, {snoozed} auto-snoozed")

    store.close()


def cmd_stop_guard(args):
    """Stop hook guard: block session exit if actionable reminders are pending.

    Outputs JSON with ``decision: "block"`` if there are pending actionable
    reminders due within the stop_guard_window.  Otherwise outputs nothing.
    """
    import json as _json

    store = _store(args)
    cfg = _config(args)

    if not cfg.reminders.enabled or not cfg.reminders.action_enabled:
        store.close()
        return

    from .actions import get_action_fields, has_action

    window_seconds = cfg.reminders.stop_guard_window
    cutoff = (
        datetime.datetime.now() + datetime.timedelta(seconds=window_seconds)
    ).isoformat(timespec="seconds")

    # Active reminders due within the window
    all_active = store.list_reminders(status="active")
    pending = []
    for r in all_active:
        if not has_action(r):
            continue
        fields = get_action_fields(r)
        if fields["action_status"] != "pending":
            continue
        if r["next_due"] <= cutoff:
            pending.append(r)

    # Also check already-due reminders still pending
    due_now = store.due_reminders()
    due_ids = {r["id"] for r in pending}
    for r in due_now:
        if r["id"] in due_ids:
            continue
        if not has_action(r):
            continue
        fields = get_action_fields(r)
        if fields["action_status"] == "pending":
            pending.append(r)

    store.close()

    if pending:
        titles = [r["title"] for r in pending[:5]]
        msg = (
            f"BLOCKED: {len(pending)} actionable reminder(s) pending. "
            f"Handle before exiting: {', '.join(titles)}. "
            f"Use `kin remind exec <id>` to run or `kin remind done <id>` to dismiss."
        )
        result = {"decision": "block", "message": msg}
        print(_json.dumps(result))


def cmd_tag(args):
    """Session tag management — named work context handles."""
    store = _store(args)
    action = getattr(args, "tag_action", None)
    tag_name = getattr(args, "tag_name", None)

    if action == "start":
        from .sessions import start_tag

        if not tag_name:
            print("Usage: kin tag start <name>", file=sys.stderr)
            store.close()
            return
        remaining = []
        raw = getattr(args, "remaining", None)
        if raw:
            remaining = [r.strip() for r in raw.split(",") if r.strip()]
        try:
            nid = start_tag(
                store,
                tag_name,
                description=getattr(args, "description", "") or "",
                focus=getattr(args, "focus", "") or "",
                remaining=remaining,
                project_path=os.getcwd(),
            )
            print(f"Started session tag: {tag_name} ({nid})")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)

    elif action == "update":
        from .sessions import get_active_tag, update_tag

        if not tag_name:
            active = get_active_tag(store, project_path=os.getcwd())
            if active:
                tag_name = (active.get("extra") or {}).get("tag", active["title"])
            else:
                print("No active session tag. Use: kin tag start <name>", file=sys.stderr)
                store.close()
                return
        remaining = None
        raw = getattr(args, "remaining", None)
        if raw:
            remaining = [r.strip() for r in raw.split(",") if r.strip()]
        append = None
        raw_add = getattr(args, "add_remaining", None)
        if raw_add:
            append = [r.strip() for r in raw_add.split(",") if r.strip()]
        remove = None
        raw_done = getattr(args, "done", None)
        if raw_done:
            remove = [r.strip() for r in raw_done.split(",") if r.strip()]
        try:
            update_tag(
                store,
                tag_name,
                focus=getattr(args, "focus", None),
                description=getattr(args, "description", None),
                remaining=remaining,
                append_remaining=append,
                remove_remaining=remove,
            )
            print(f"Updated: {tag_name}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)

    elif action == "segment":
        from .sessions import add_segment, get_active_tag

        if not tag_name:
            active = get_active_tag(store, project_path=os.getcwd())
            if active:
                tag_name = (active.get("extra") or {}).get("tag", active["title"])
        if not tag_name:
            print("No active session tag.", file=sys.stderr)
            store.close()
            return
        focus = getattr(args, "focus", None) or "New segment"
        summary = getattr(args, "summary", None) or ""
        try:
            add_segment(store, tag_name, new_focus=focus, summary=summary)
            print(f"New segment on {tag_name}: {focus}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)

    elif action == "pause":
        from .sessions import get_active_tag, pause_tag

        if not tag_name:
            active = get_active_tag(store, project_path=os.getcwd())
            if active:
                tag_name = (active.get("extra") or {}).get("tag", active["title"])
        if not tag_name:
            print("No active session tag.", file=sys.stderr)
            store.close()
            return
        summary = getattr(args, "summary", None) or ""
        try:
            pause_tag(store, tag_name, summary=summary)
            print(f"Paused: {tag_name}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)

    elif action == "end":
        from .sessions import complete_tag, get_active_tag

        if not tag_name:
            active = get_active_tag(store, project_path=os.getcwd())
            if active:
                tag_name = (active.get("extra") or {}).get("tag", active["title"])
        if not tag_name:
            print("No active session tag.", file=sys.stderr)
            store.close()
            return
        summary = getattr(args, "summary", None) or ""
        try:
            complete_tag(store, tag_name, summary=summary)
            print(f"Completed: {tag_name}")
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)

    elif action == "resume":
        from .sessions import format_resume_context

        if not tag_name:
            print("Usage: kin tag resume <name>", file=sys.stderr)
            store.close()
            return
        tokens = getattr(args, "tokens", 1500) or 1500
        block = format_resume_context(store, tag_name, max_tokens=tokens)
        print(block)

    elif action == "list":
        from .sessions import list_tags

        status = getattr(args, "status", None)
        project = os.getcwd() if getattr(args, "project", False) else None
        tags = list_tags(store, status=status, project_path=project)
        if not tags:
            print("No session tags found.")
        else:
            for t in tags:
                extra = t.get("extra") or {}
                tname = extra.get("tag", t["title"])
                tstatus = extra.get("session_status", "?")
                tfocus = extra.get("current_focus", "")[:50]
                seg_count = len(extra.get("segments", []))
                updated = (t.get("updated_at") or "")[:16]
                print(f"  [{tstatus:9s}] {tname:25s} {tfocus:50s} ({seg_count} seg) {updated}")

    elif action == "show":
        from .sessions import get_tag

        if not tag_name:
            print("Usage: kin tag show <name>", file=sys.stderr)
            store.close()
            return
        tag = get_tag(store, tag_name)
        if not tag:
            print(f"Tag not found: {tag_name}", file=sys.stderr)
            store.close()
            return
        extra = tag.get("extra") or {}
        print(f"Tag: {extra.get('tag', tag['title'])}")
        print(f"Status: {extra.get('session_status', '?')}")
        print(f"Project: {extra.get('project_path', '')}")
        print(f"Focus: {extra.get('current_focus', '')}")
        print(f"Started: {extra.get('started_at', '')}")
        if extra.get("paused_at"):
            print(f"Paused: {extra['paused_at']}")
        if extra.get("completed_at"):
            print(f"Completed: {extra['completed_at']}")
        if tag.get("content"):
            print(f"Description: {tag['content']}")
        remaining = extra.get("remaining", [])
        if remaining:
            print(f"Remaining ({len(remaining)}):")
            for item in remaining:
                print(f"  - {item}")
        segments = extra.get("segments", [])
        if segments:
            print(f"Segments ({len(segments)}):")
            for seg in segments:
                state = "active" if not seg.get("ended_at") else "done"
                print(f"  [{state}] {seg.get('focus', '')}")
                if seg.get("summary"):
                    print(f"         {seg['summary'][:100]}")
                if seg.get("decisions"):
                    print(f"         Decisions: {', '.join(seg['decisions'][:3])}")
        linked = extra.get("linked_nodes", [])
        if linked:
            print(f"Linked nodes ({len(linked)}):")
            for nid in linked[:10]:
                node = store.get_node(nid)
                if node:
                    print(f"  - {node['title']} ({node['type']})")

    store.close()


# ── setup ─────────────────────────────────────────────────────────────

def cmd_setup_hooks(args):
    """Install/uninstall Kindex hooks in Claude Code's settings.json."""
    from .setup import install_claude_hooks
    cfg = _config(args)
    dry_run = getattr(args, "dry_run", False)

    if getattr(args, "uninstall", False):
        # Remove hooks by loading settings and filtering out kindex entries
        settings_path = cfg.claude_path / "settings.json"
        if settings_path.exists():
            import json as _json
            data = _json.loads(settings_path.read_text())
            hooks = data.get("hooks", {})
            changed = False
            for key in ["SessionStart", "PreCompact"]:
                if key in hooks:
                    before = len(hooks[key])
                    hooks[key] = [
                        h for h in hooks[key]
                        if "kin prime" not in str(h)
                        and "compact-hook" not in str(h)
                        and "kindex" not in str(h).lower()
                    ]
                    if len(hooks[key]) < before:
                        changed = True
                        print(f"Removed Kindex {key} hook")
            if changed and not dry_run:
                settings_path.write_text(_json.dumps(data, indent=2) + "\n")
                print(f"Updated {settings_path}")
            elif not changed:
                print("No Kindex hooks found to remove")
        else:
            print("No Claude Code settings.json found")
        return

    actions = install_claude_hooks(cfg, dry_run=dry_run)
    for a in actions:
        print(f"  {a}")


def cmd_setup_cron(args):
    """Install/uninstall periodic cron job for kin maintenance."""
    import platform
    cfg = _config(args)
    dry_run = getattr(args, "dry_run", False)
    method = getattr(args, "method", None)

    # Auto-detect method
    if method is None:
        method = "launchd" if platform.system() == "Darwin" else "crontab"

    if getattr(args, "uninstall", False):
        if method == "launchd":
            from .setup import uninstall_launchd
            actions = uninstall_launchd(dry_run=dry_run)
        else:
            # Remove crontab entry
            import subprocess
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            if result.returncode == 0:
                lines = result.stdout.splitlines()
                filtered = [l for l in lines if "kin cron" not in l and "kindex" not in l]
                if len(filtered) < len(lines):
                    if not dry_run:
                        new_crontab = "\n".join(filtered) + "\n"
                        subprocess.run(["crontab", "-"], input=new_crontab,
                                       capture_output=True, text=True)
                    actions = ["Removed crontab entry"]
                else:
                    actions = ["No crontab entry found"]
            else:
                actions = ["No crontab found"]
    else:
        if method == "launchd":
            from .setup import install_launchd
            actions = install_launchd(cfg, dry_run=dry_run)
        else:
            from .setup import install_crontab
            actions = install_crontab(cfg, dry_run=dry_run)

    for a in actions:
        print(f"  {a}")


def cmd_setup_claude_md(args):
    """Output recommended CLAUDE.md block for kindex integration.

    kin setup claude-md           — print to stdout
    kin setup claude-md --install — append to ~/.claude/CLAUDE.md if not present
    """
    block = _kindex_claude_md_block()

    if getattr(args, "install", False):
        claude_md = Path.home() / ".claude" / "CLAUDE.md"
        if claude_md.exists():
            existing = claude_md.read_text()
            if "Kindex (REQUIRED" in existing or "kindex MCP tools" in existing:
                print("Kindex directives already present in CLAUDE.md")
                return
            with open(claude_md, "a") as f:
                f.write("\n" + block)
            print(f"Appended kindex directives to {claude_md}")
        else:
            claude_md.parent.mkdir(parents=True, exist_ok=True)
            claude_md.write_text(block)
            print(f"Created {claude_md} with kindex directives")
    else:
        print(block)


def _kindex_claude_md_block() -> str:
    """Generate the recommended CLAUDE.md block for kindex integration."""
    return """\
## Kindex (REQUIRED -- follow these in every session)

Kindex is a persistent knowledge graph. MCP tools (`search`, `add`, `context`, \
`show`, `link`, `list_nodes`, `status`, `ask`, `suggest`, `learn`, `graph_stats`, \
`changelog`, `ingest`, `tag_start`, `tag_update`, `tag_resume`, `remind_create`, \
`remind_exec`) are always available. Use them.

### Session lifecycle (do this every session)
1. **Start**: call `tag_start` with a name and focus for the current task, OR \
`tag_resume` if continuing previous work
2. **During**: follow the capture rules below -- this is the whole point of kindex
3. **Segment**: when switching topics, call `tag_update` with `action=segment`, \
summarizing what was done
4. **End**: call `tag_update` with `action=end` and a summary before the session closes

### What to capture (use MCP `add` tool or `learn` for bulk text)
- **Discoveries**: new patterns, surprising findings, "aha" moments -- `add` as concept
- **Decisions**: architectural choices, trade-offs made, why X over Y -- `add` as decision
- **Key files**: when you discover what a file does or why it exists -- `add` as concept \
with the file path
- **Notable outputs**: test results, build errors, performance numbers, API responses \
worth remembering
- **New topics/keywords**: domain terms, project jargon, recurring themes -- `add` as concept
- **Questions**: open problems, things to investigate later -- `add` as question
- **Connections**: when two concepts relate -- `link` them with a reason

### What NOT to capture
- Trivial file reads, routine git operations, boilerplate
- Anything already in the graph -- always `search` before adding

### When to search
- **Before starting work**: `search` or `context` to see what is already known
- **Before adding**: `search` to avoid duplicates
- **When stuck**: `ask` the graph -- it may already have the answer

### Bulk capture
- After reading a long file, article, or output: use `learn` to extract and index \
multiple concepts at once
- After a complex multi-step task: use `learn` with a summary of what happened and why

### Reminders with actions
- Use `remind_create` with `action` and/or `instructions` for deferred tasks
- The daemon will execute shell commands or launch headless Claude when they come due
"""


# ── config ────────────────────────────────────────────────────────────

def cmd_config(args):
    """Read or write config values.

    kin config show          — print full config
    kin config get <key>     — read a value (dot-separated: llm.enabled)
    kin config set <key> <value> — write a value to config file
    """
    action = args.config_action

    if action == "show":
        cfg = _config(args)
        print(yaml.dump(cfg.model_dump(), default_flow_style=False, sort_keys=False).strip())
        return

    if action == "get":
        if not args.key:
            print("Error: kin config get <key>", file=sys.stderr)
            sys.exit(1)
        cfg = _config(args)
        val = _dotget(cfg.model_dump(), args.key)
        if val is None:
            print(f"No value for '{args.key}'", file=sys.stderr)
            sys.exit(1)
        if isinstance(val, dict):
            print(yaml.dump(val, default_flow_style=False).strip())
        elif isinstance(val, list):
            for item in val:
                print(f"  - {item}")
        else:
            print(val)
        return

    if action == "set":
        if not args.key or args.value is None:
            print("Error: kin config set <key> <value>", file=sys.stderr)
            sys.exit(1)
        is_global = getattr(args, "global_", False)
        _config_write(args.key, args.value, getattr(args, "config", None),
                      global_=is_global)
        scope = "global" if is_global else "local"
        print(f"Set {args.key} = {args.value} ({scope})")
        return

    # Default: show
    cfg = _config(args)
    print(yaml.dump(cfg.model_dump(), default_flow_style=False, sort_keys=False).strip())


def _dotget(d: dict, key: str):
    """Get a value from a nested dict via dot-separated key."""
    parts = key.split(".")
    current = d
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _dotset(d: dict, key: str, value) -> None:
    """Set a value in a nested dict via dot-separated key."""
    parts = key.split(".")
    current = d
    for part in parts[:-1]:
        if part not in current or not isinstance(current[part], dict):
            current[part] = {}
        current = current[part]
    current[parts[-1]] = value


def _coerce_value(value: str):
    """Coerce a string value to the appropriate Python type."""
    if value.lower() in ("true", "yes"):
        return True
    if value.lower() in ("false", "no"):
        return False
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    # List syntax: [a, b, c]
    if value.startswith("[") and value.endswith("]"):
        items = [s.strip().strip("'\"") for s in value[1:-1].split(",")]
        return [i for i in items if i]
    return value


def _config_write(key: str, value: str, config_path: str | None = None,
                   global_: bool = False) -> None:
    """Write a config value to the appropriate config file.

    Resolution (like git config):
    - --config <path>:  explicit file
    - --global:         user-level (~/.config/kindex/kin.yaml)
    - default:          local file if one exists in cwd, else global
    """
    import yaml

    if config_path:
        path = Path(config_path).expanduser().resolve()
    elif global_:
        from .config import _GLOBAL_PATHS
        path = None
        for p in _GLOBAL_PATHS:
            p = p.expanduser().resolve()
            if p.exists():
                path = p
                break
        if path is None:
            path = Path.home() / ".config" / "kindex" / "kin.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
    else:
        from .config import _LOCAL_PATHS, _GLOBAL_PATHS
        path = None
        for p in _LOCAL_PATHS:
            p = p.expanduser().resolve()
            if p.exists():
                path = p
                break
        if path is None:
            for p in _GLOBAL_PATHS:
                p = p.expanduser().resolve()
                if p.exists():
                    path = p
                    break
        if path is None:
            path = Path.home() / ".config" / "kindex" / "kin.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing or start fresh
    if path.exists():
        data = yaml.safe_load(path.read_text()) or {}
    else:
        data = {}

    _dotset(data, key, _coerce_value(value))
    path.write_text(yaml.dump(data, default_flow_style=False, sort_keys=False))


# ── parser ─────────────────────────────────────────────────────────────

def _common(p):
    p.add_argument("--config", help="Path to conv.yaml")
    p.add_argument("--data-dir", help="Override data directory")
    p.add_argument("--json", action="store_true", help="JSON output")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kin",
                                description="Knowledge graph that learns from your conversations")
    p.add_argument("--version", action="store_true")
    sub = p.add_subparsers(dest="command")

    # search
    s = sub.add_parser("search", help="Hybrid search (FTS + graph)")
    s.add_argument("query", nargs="+")
    s.add_argument("--top-k", type=int, default=10)
    s.add_argument("--mine", action="store_true", help="Only my nodes")
    _common(s)
    s.set_defaults(func=cmd_search)

    # context
    s = sub.add_parser("context", help="Context block for CLAUDE.md injection")
    s.add_argument("--topic", help="Topic (auto-detects from $PWD if omitted)")
    s.add_argument("--depth", type=int, default=10)
    s.add_argument("--level", choices=["full", "abridged", "summarized", "executive", "index"],
                   help="Context tier (auto-selects if omitted)")
    s.add_argument("--tokens", type=int, help="Available token budget (auto-selects tier)")
    s.add_argument("--format", choices=["claude", "raw", "json"], default="claude")
    _common(s)
    s.set_defaults(func=cmd_context)

    # add
    s = sub.add_parser("add", help="Quick capture with auto-linking")
    s.add_argument("note", nargs="+")
    s.add_argument("--type", choices=["concept", "document", "decision",
                                       "question", "skill", "artifact", "person",
                                       "constraint", "directive", "checkpoint", "watch"])
    # Operational node metadata
    s.add_argument("--trigger", help="Trigger event (pre-commit, pre-deploy, etc.)")
    s.add_argument("--action", choices=["verify", "warn", "block"], help="Constraint action")
    s.add_argument("--scope", help="Directive scope (e.g. customer-communications)")
    s.add_argument("--owner", help="Person responsible (for watches/directives)")
    s.add_argument("--expires", help="Expiry date YYYY-MM-DD (for watches)")
    s.add_argument("--resets", help="Reset schedule (e.g. monday, monthly)")
    s.add_argument("--audience", choices=["private", "team", "org", "public"],
                   help="Audience scope")
    _common(s)
    s.set_defaults(func=cmd_add)

    # learn
    s = sub.add_parser("learn", help="Extract knowledge from sessions/inbox")
    s.add_argument("--from-inbox", action="store_true", help="Process inbox items")
    s.add_argument("session_id", nargs="?", help="Session ID to learn from")
    _common(s)
    s.set_defaults(func=cmd_learn)

    # link
    s = sub.add_parser("link", help="Create edge between nodes")
    s.add_argument("node_a")
    s.add_argument("node_b")
    s.add_argument("relationship", nargs="?", default="relates_to")
    s.add_argument("--why", help="Reason for link")
    s.add_argument("--weight", type=float, default=0.5)
    _common(s)
    s.set_defaults(func=cmd_link)

    # show
    s = sub.add_parser("show", help="Show node details")
    s.add_argument("node_id")
    _common(s)
    s.set_defaults(func=cmd_show)

    # list
    s = sub.add_parser("list", help="List nodes")
    s.add_argument("--type")
    s.add_argument("--status")
    s.add_argument("--limit", type=int, default=100)
    s.add_argument("--mine", action="store_true", help="Only my nodes")
    _common(s)
    s.set_defaults(func=cmd_list)

    # recent
    s = sub.add_parser("recent", help="Recently active nodes")
    s.add_argument("--n", type=int, default=20)
    _common(s)
    s.set_defaults(func=cmd_recent)

    # orphans
    s = sub.add_parser("orphans", help="Nodes with no edges")
    _common(s)
    s.set_defaults(func=cmd_orphans)

    # status
    s = sub.add_parser("status", help="Graph health & stats")
    s.add_argument("--type", help="Filter by node type (constraint, watch, etc.)")
    s.add_argument("--trigger", help="Filter operational nodes by trigger event")
    s.add_argument("--owner", help="Filter by owner")
    s.add_argument("--mine", action="store_true", help="Filter by current user")
    _common(s)
    s.set_defaults(func=cmd_status)

    # budget
    s = sub.add_parser("budget", help="LLM budget usage")
    _common(s)
    s.set_defaults(func=cmd_budget)

    # init
    s = sub.add_parser("init", help="Initialize Kindex data directory")
    _common(s)
    s.set_defaults(func=cmd_init)

    # migrate
    s = sub.add_parser("migrate", help="Import markdown topics into SQLite")
    _common(s)
    s.set_defaults(func=cmd_migrate)

    # doctor
    s = sub.add_parser("doctor", help="Health check")
    s.add_argument("--fix", action="store_true")
    _common(s)
    s.set_defaults(func=cmd_doctor)

    # set-audience
    s = sub.add_parser("set-audience", help="Set node audience (private/team/org/public)")
    s.add_argument("node_id")
    s.add_argument("audience", choices=["private", "team", "org", "public"])
    _common(s)
    s.set_defaults(func=cmd_set_audience)

    # set-state
    s = sub.add_parser("set-state", help="Set mutable state on a directive/operational node")
    s.add_argument("node_id", help="Node ID or title")
    s.add_argument("key", help="State key to set")
    s.add_argument("value", help="Value to set")
    _common(s)
    s.set_defaults(func=cmd_set_state)

    # export
    s = sub.add_parser("export", help="Export graph (audience-aware)")
    s.add_argument("--audience", choices=["private", "team", "org", "public"], default="team")
    s.add_argument("--format", choices=["json", "jsonl"], default="json")
    _common(s)
    s.set_defaults(func=cmd_export)

    # ingest
    s = sub.add_parser("ingest", help="Ingest from external sources")
    # Dynamic adapter discovery for choices
    try:
        from .adapters.registry import discover as _discover_adapters
        _adapter_names = sorted(_discover_adapters().keys())
    except Exception:
        _adapter_names = ["projects", "sessions", "files", "commits", "github", "linear"]
    s.add_argument("source", choices=_adapter_names + ["all"],
                   help="Adapter name or 'all' for all available sources")
    s.add_argument("--limit", type=int, default=50, help="Max items to ingest")
    s.add_argument("--repo", type=str, default=None, help="GitHub owner/repo (e.g. jmcentire/kindex)")
    s.add_argument("--repo-path", type=str, default=None,
                   help="Local repository path (for commits source)")
    s.add_argument("--since", type=str, default=None, help="ISO date to filter items created after")
    s.add_argument("--team", type=str, default=None, help="Linear team key (for linear source)")
    s.add_argument("--directory", type=str, default=None, help="Directory to ingest (for files source)")
    _common(s)
    s.set_defaults(func=cmd_ingest)

    # git-hook
    s = sub.add_parser("git-hook", help="Install/uninstall Kindex git hooks in a repository")
    s.add_argument("hook_action", choices=["install", "uninstall"],
                   help="Action: install or uninstall git hooks")
    s.add_argument("--repo-path", type=str, default=".",
                   help="Path to git repository (default: current directory)")
    _common(s)
    s.set_defaults(func=cmd_git_hook)

    # trail
    s = sub.add_parser("trail", help="Temporal history of a node")
    s.add_argument("node_id")
    _common(s)
    s.set_defaults(func=cmd_trail)

    # decay
    s = sub.add_parser("decay", help="Run weight decay on nodes/edges")
    s.add_argument("--node-half-life", type=int, default=90, help="Node half-life in days")
    s.add_argument("--edge-half-life", type=int, default=30, help="Edge half-life in days")
    _common(s)
    s.set_defaults(func=cmd_decay)

    # compact-hook
    s = sub.add_parser("compact-hook", help="Pre-compact hook for context capture")
    s.add_argument("--text", help="Text to extract from")
    s.add_argument("--emit-context", action="store_true",
                   help="Always emit executive context summary")
    _common(s)
    s.set_defaults(func=cmd_compact_hook)

    # prime
    s = sub.add_parser("prime", help="Generate context for SessionStart hook")
    s.add_argument("--topic", help="Topic to prime (auto-detects from $PWD if omitted)")
    s.add_argument("--tokens", type=int, default=750, help="Max token budget (default 750)")
    s.add_argument("--for", dest="output_for", choices=["hook", "stdout"], default="stdout",
                   help="Output mode: hook (raw block) or stdout (with header)")
    s.add_argument("--codebook", action="store_true",
                   help="Regenerate the LLM prompt cache codebook")
    _common(s)
    s.set_defaults(func=cmd_prime)

    # suggest
    s = sub.add_parser("suggest", help="Review bridge opportunity suggestions")
    s.add_argument("--accept", type=int, metavar="ID", help="Accept suggestion by ID")
    s.add_argument("--reject", type=int, metavar="ID", help="Reject suggestion by ID")
    s.add_argument("--limit", type=int, default=20, help="Max suggestions to show")
    _common(s)
    s.set_defaults(func=cmd_suggest)

    # log
    s = sub.add_parser("log", help="Show recent activity")
    s.add_argument("--n", type=int, default=50, help="Number of entries")
    _common(s)
    s.set_defaults(func=cmd_log)

    # changelog
    s = sub.add_parser("changelog", help="Show what changed in the graph")
    s.add_argument("--since", help="ISO date/timestamp (e.g. 2026-02-20)")
    s.add_argument("--days", type=int, help="Look back N days (default 7)")
    s.add_argument("--actor", help="Filter by actor")
    _common(s)
    s.set_defaults(func=cmd_changelog)

    # graph
    s = sub.add_parser("graph", help="Graph analytics dashboard")
    s.add_argument("graph_mode", nargs="?", default="stats",
                   choices=["stats", "centrality", "communities", "bridges", "trailheads"])
    s.add_argument("--method", choices=["betweenness", "degree", "closeness"],
                   help="Centrality method")
    s.add_argument("--top-k", type=int, default=20, help="Number of results")
    _common(s)
    s.set_defaults(func=cmd_graph)

    # alias
    s = sub.add_parser("alias", help="Manage AKA/synonyms for a node")
    s.add_argument("node_id", help="Node ID or title")
    s.add_argument("alias_action", choices=["add", "remove", "list"])
    s.add_argument("alias_value", nargs="?", help="Alias to add/remove")
    _common(s)
    s.set_defaults(func=cmd_alias)

    # whoami
    s = sub.add_parser("whoami", help="Show current user identity")
    _common(s)
    s.set_defaults(func=cmd_whoami)

    # embed
    s = sub.add_parser("embed", help="Index all nodes for vector search")
    s.add_argument("--verbose", "-v", action="store_true")
    _common(s)
    s.set_defaults(func=cmd_embed)

    # ask
    s = sub.add_parser("ask", help="Query the knowledge graph")
    s.add_argument("question", nargs="+")
    _common(s)
    s.set_defaults(func=cmd_ask)

    # register
    s = sub.add_parser("register", help="Associate a file path with a node")
    s.add_argument("node_id", help="Node ID or title")
    s.add_argument("filepath", help="File path to register")
    _common(s)
    s.set_defaults(func=cmd_register)

    # setup-hooks
    s = sub.add_parser("setup-hooks", help="Install Kindex hooks into Claude Code")
    s.add_argument("--dry-run", action="store_true", help="Show what would be done")
    s.add_argument("--uninstall", action="store_true", help="Remove installed hooks")
    _common(s)
    s.set_defaults(func=cmd_setup_hooks)

    # setup-cron
    s = sub.add_parser("setup-cron", help="Install periodic cron job for kin maintenance")
    s.add_argument("--dry-run", action="store_true", help="Show what would be done")
    s.add_argument("--uninstall", action="store_true", help="Remove cron entry")
    s.add_argument("--method", choices=["launchd", "crontab"],
                   help="Scheduling method (auto-detects: launchd on macOS, crontab on Linux)")
    _common(s)
    s.set_defaults(func=cmd_setup_cron)

    # setup-claude-md
    s = sub.add_parser("setup-claude-md",
                       help="Output recommended CLAUDE.md kindex directives")
    s.add_argument("--install", action="store_true",
                   help="Append to ~/.claude/CLAUDE.md (if not already present)")
    _common(s)
    s.set_defaults(func=cmd_setup_claude_md)

    # config
    s = sub.add_parser("config", help="View or edit configuration")
    s.add_argument("config_action", nargs="?", default="show",
                   choices=["show", "get", "set"],
                   help="Action: show, get <key>, set <key> <value>")
    s.add_argument("key", nargs="?", help="Config key (dot-separated: llm.enabled)")
    s.add_argument("value", nargs="?", help="Value to set")
    s.add_argument("--global", dest="global_", action="store_true",
                   help="Write to global config (~/.config/kindex/kin.yaml)")
    _common(s)
    s.set_defaults(func=cmd_config)

    # skills
    s = sub.add_parser("skills", help="Show skill profile for a person")
    s.add_argument("person", nargs="?", help="Person name/ID (default: current user)")
    _common(s)
    s.set_defaults(func=cmd_skills)

    # import (named import-graph to avoid Python keyword)
    s = sub.add_parser("import", help="Import nodes/edges from JSON/JSONL")
    s.add_argument("filepath", help="Path to JSON or JSONL file")
    s.add_argument("--mode", choices=["merge", "replace"], default="merge",
                   help="Merge (default) or replace existing nodes")
    s.add_argument("--format", choices=["json", "jsonl"],
                   help="Force format (auto-detects from extension)")
    s.add_argument("--dry-run", action="store_true",
                   help="Show what would be imported without making changes")
    _common(s)
    s.set_defaults(func=cmd_import_graph)

    # analytics
    s = sub.add_parser("analytics", help="Archive session analytics and activity heatmap")
    s.add_argument("--sessions", action="store_true", help="Show session stats")
    s.add_argument("--heatmap", action="store_true", help="Show activity heatmap")
    s.add_argument("--days", type=int, default=90, help="Lookback days for heatmap (default 90)")
    _common(s)
    s.set_defaults(func=cmd_analytics)

    # index
    s = sub.add_parser("index", help="Write .kin/index.json for git tracking")
    s.add_argument("--output-dir", type=str, help="Output directory (default: current dir)")
    _common(s)
    s.set_defaults(func=cmd_index)

    # sync-links
    s = sub.add_parser("sync-links", help="Update node content with connection references")
    _common(s)
    s.set_defaults(func=cmd_sync_links)

    # cron
    s = sub.add_parser("cron", help="One-shot maintenance cycle (for crontab)")
    s.add_argument("--verbose", "-v", action="store_true", help="Detailed logging")
    _common(s)
    s.set_defaults(func=cmd_cron)

    # watch
    s = sub.add_parser("watch", help="Watch for new sessions and ingest them")
    s.add_argument("--interval", type=int, default=60,
                   help="Check interval in seconds (default: 60)")
    s.add_argument("--verbose", "-v", action="store_true", help="Detailed logging")
    _common(s)
    s.set_defaults(func=cmd_watch)

    # tag (session tags)
    s = sub.add_parser("tag", help="Session tag management (start, update, resume, etc.)")
    s.add_argument("tag_action",
                   choices=["start", "update", "segment", "pause", "end",
                            "resume", "list", "show"],
                   help="Tag action")
    s.add_argument("tag_name", nargs="?", help="Tag name (auto-detects active for update/pause/end)")
    s.add_argument("--focus", help="Current focus / new segment focus")
    s.add_argument("--description", help="Session description")
    s.add_argument("--summary", help="Summary (for segment/pause/end)")
    s.add_argument("--remaining", help="Comma-separated remaining items")
    s.add_argument("--add-remaining", help="Add items to remaining list (comma-separated)")
    s.add_argument("--done", help="Mark items as done / remove from remaining (comma-separated)")
    s.add_argument("--status", help="Filter by status (for list: active/paused/completed)")
    s.add_argument("--project", action="store_true", help="Filter by current project (for list)")
    s.add_argument("--tokens", type=int, default=1500, help="Token budget for resume context")
    _common(s)
    s.set_defaults(func=cmd_tag)

    # remind
    s = sub.add_parser("remind", help="Reminder management (create, list, snooze, done, cancel, check)")
    s.add_argument("remind_action", nargs="?", default="create",
                   choices=["create", "list", "show", "snooze", "done", "cancel", "check", "exec"])
    s.add_argument("title_words", nargs="*", help="Reminder title (for create)")
    s.add_argument("--at", help="Time spec: 'in 30 minutes', 'every weekday at 9am', etc.")
    s.add_argument("--priority", choices=["low", "normal", "high", "urgent"])
    s.add_argument("--channel", help="Notification channels (comma-separated)")
    s.add_argument("--tag", dest="tag_str", help="Tags (comma-separated)")
    s.add_argument("--status", help="Filter (for list): active, snoozed, fired, all")
    s.add_argument("--reminder-id", help="Reminder ID (for show/snooze/done/cancel)")
    s.add_argument("--duration", help="Snooze duration: 15m, 1h, 2h30m")
    s.add_argument("--action", dest="action_command", help="Shell command to execute when due")
    s.add_argument("--instructions", dest="action_instructions",
                   help="NL instructions for Claude (triggers claude -p mode)")
    s.add_argument("--action-mode", dest="action_mode",
                   choices=["shell", "claude", "auto"], default="auto",
                   help="Execution mode (default: auto)")
    _common(s)
    s.set_defaults(func=cmd_remind)

    # stop-guard (Claude Code Stop hook)
    s = sub.add_parser("stop-guard", help="Stop hook guard for actionable reminders")
    _common(s)
    s.set_defaults(func=cmd_stop_guard)

    return p


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.version:
        print(f"kin {__version__} (Kindex)")
        return

    if not args.command:
        parser.print_help()
        return

    if hasattr(args, "func"):
        args.func(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
