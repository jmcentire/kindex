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

    for d in [cfg.topics_dir, cfg.skills_dir, cfg.inbox_dir, cfg.tmp_dir]:
        d.mkdir(parents=True, exist_ok=True)

    # Create the database
    from .store import Store
    store = Store(cfg)
    _ = store.conn  # triggers schema creation
    store.close()

    print(f"Initialized Kindex at {dp}")
    print(f"  kindex.db — SQLite knowledge graph")
    print(f"  topics/   — markdown topic files")
    print(f"  skills/   — skill/ability files")
    print(f"  inbox/    — queued discoveries")


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
    store = _store(args)
    stats = store.stats()
    issues = []

    orphans = store.orphans()
    if orphans:
        issues.append(f"  {len(orphans)} orphan node(s) — run `kin orphans` to see them")

    if stats["nodes"] == 0:
        issues.append("  No nodes — run `kin migrate` to import existing topics")

    if issues:
        print(f"{len(issues)} issue(s):")
        for i in issues:
            print(i)
    else:
        print(f"Healthy: {stats['nodes']} nodes, {stats['edges']} edges, 0 orphans")

    store.close()


# ── set-audience ──────────────────────────────────────────────────────

def cmd_set_audience(args):
    """Set the audience scope of a node (private/team/public)."""
    store = _store(args)
    node = store.get_node(args.node_id) or store.get_node_by_title(args.node_id)

    if not node:
        print(f"Error: '{args.node_id}' not found.", file=sys.stderr)
        sys.exit(1)

    store.update_node(node["id"], audience=args.audience)
    print(f"Set {node['title']} audience to: {args.audience}")
    store.close()


# ── export ────────────────────────────────────────────────────────────

def cmd_export(args):
    """Export the graph, respecting audience boundaries.

    --audience team: exports team + public nodes (for shared drives)
    --audience public: exports only public nodes (for open-source / LinkedIn)
    --audience private: exports everything (for personal backup)
    """
    store = _store(args)
    target_audience = args.audience

    if target_audience == "private":
        nodes = store.all_nodes(limit=10000)
    elif target_audience == "team":
        team = store.all_nodes(audience="team", limit=10000)
        public = store.all_nodes(audience="public", limit=10000)
        seen = set()
        nodes = []
        for n in team + public:
            if n["id"] not in seen:
                seen.add(n["id"])
                nodes.append(n)
    else:  # public
        nodes = store.all_nodes(audience="public", limit=10000)

    # Strip edges that cross audience boundaries
    output = []
    node_ids = {n["id"] for n in nodes}
    for n in nodes:
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
    """Ingest knowledge from external sources (projects, sessions)."""
    store = _store(args)
    cfg = _config(args)
    source = args.source

    if source == "projects":
        from .ingest import scan_kin_files, scan_projects
        count = scan_projects(cfg, store, verbose=True)
        conv_count = scan_kin_files(cfg, store, verbose=True)
        print(f"\n{count} new project(s), {conv_count} .kin update(s).")
    elif source == "sessions":
        from .ingest import scan_sessions
        limit = getattr(args, "limit", 10) or 10
        count = scan_sessions(cfg, store, limit=limit, verbose=True)
        print(f"\n{count} new session node(s) created.")
    elif source == "all":
        from .ingest import scan_kin_files, scan_projects, scan_sessions
        pc = scan_projects(cfg, store, verbose=True)
        cc = scan_kin_files(cfg, store, verbose=True)
        sc = scan_sessions(cfg, store, limit=getattr(args, "limit", 10) or 10,
                           verbose=True)
        print(f"\n{pc} project(s), {cc} .kin update(s), {sc} session(s) ingested.")
    else:
        print(f"Unknown source: {source}. Use: projects, sessions, all", file=sys.stderr)

    store.close()


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

def cmd_ask(args):
    """Query the knowledge graph with natural language.

    Uses LLM if available, otherwise falls back to search + context formatting.
    """
    store = _store(args)
    question = " ".join(args.question)

    from .retrieve import format_context_block, hybrid_search

    results = hybrid_search(store, question, top_k=10)

    if not results:
        print("No relevant knowledge found.", file=sys.stderr)
        store.close()
        return

    # Try LLM-powered answer
    ledger, cfg = _ledger(args)
    answer = _ask_llm(question, results, cfg, ledger)

    if answer:
        print(answer)
    else:
        # Fallback: just show the context
        block = format_context_block(store, results, query=question, level="abridged")
        print(f"(No LLM available — showing search results)\n")
        print(block)

    store.close()


def _ask_llm(question: str, results: list[dict], config, ledger) -> str | None:
    """Use LLM to answer a question given graph context."""
    if not config.llm.enabled:
        return None
    if not ledger.can_spend():
        return None

    try:
        import os
        import anthropic
        key = os.environ.get(config.llm.api_key_env)
        if not key:
            return None
        client = anthropic.Anthropic(api_key=key)
    except ImportError:
        return None

    # Build context from results
    context_parts = []
    for r in results[:5]:
        title = r.get("title", r["id"])
        content = (r.get("content") or "")[:500]
        ntype = r.get("type", "concept")
        context_parts.append(f"[{ntype}] {title}: {content}")

    context = "\n\n".join(context_parts)

    try:
        response = client.messages.create(
            model=config.llm.model,
            max_tokens=500,
            messages=[{"role": "user", "content": f"""Based on this knowledge graph context:

{context}

Answer this question concisely: {question}

If the context doesn't contain enough information, say so honestly."""}],
        )

        cost_in = response.usage.input_tokens
        cost_out = response.usage.output_tokens
        pricing = {"input": 1.00 / 1_000_000, "output": 5.00 / 1_000_000}
        cost = cost_in * pricing["input"] + cost_out * pricing["output"]
        ledger.record(cost, model=config.llm.model, purpose="ask",
                      tokens_in=cost_in, tokens_out=cost_out)

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
        _config_write(args.key, args.value, getattr(args, "config", None))
        print(f"Set {args.key} = {args.value}")
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


def _config_write(key: str, value: str, config_path: str | None = None) -> None:
    """Write a config value to the config file."""
    import yaml

    # Find existing config file or create one
    if config_path:
        path = Path(config_path).expanduser().resolve()
    else:
        from .config import _SEARCH_PATHS
        path = None
        for p in _SEARCH_PATHS:
            p = p.expanduser().resolve()
            if p.exists():
                path = p
                break
        if path is None:
            # Create default config location
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
    s.add_argument("--audience", choices=["private", "team", "public"],
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
    s = sub.add_parser("set-audience", help="Set node audience (private/team/public)")
    s.add_argument("node_id")
    s.add_argument("audience", choices=["private", "team", "public"])
    _common(s)
    s.set_defaults(func=cmd_set_audience)

    # export
    s = sub.add_parser("export", help="Export graph (audience-aware)")
    s.add_argument("--audience", choices=["private", "team", "public"], default="team")
    s.add_argument("--format", choices=["json", "jsonl"], default="json")
    _common(s)
    s.set_defaults(func=cmd_export)

    # ingest
    s = sub.add_parser("ingest", help="Ingest from external sources")
    s.add_argument("source", choices=["projects", "sessions", "all"])
    s.add_argument("--limit", type=int, default=10, help="Max sessions to scan")
    _common(s)
    s.set_defaults(func=cmd_ingest)

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

    # config
    s = sub.add_parser("config", help="View or edit configuration")
    s.add_argument("config_action", nargs="?", default="show",
                   choices=["show", "get", "set"],
                   help="Action: show, get <key>, set <key> <value>")
    s.add_argument("key", nargs="?", help="Config key (dot-separated: llm.enabled)")
    s.add_argument("value", nargs="?", help="Value to set")
    _common(s)
    s.set_defaults(func=cmd_config)

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
