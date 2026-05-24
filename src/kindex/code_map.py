"""Code-map import/export projections.

Kindex keeps its own graph schema internally. This module provides a small
interop layer for dashboard-oriented code-map JSON, including the shape used by
Understand-Anything.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .adapters.base import IngestResult

if TYPE_CHECKING:
    from .store import Store


UA_VERSION = "1.0.0"


def _now() -> str:
    return datetime.now(tz=None).isoformat(timespec="seconds")


def _sha(value: str, length: int = 12) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:length]


def _git_commit(directory: Path | None) -> str:
    if not directory:
        return ""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(directory),
            capture_output=True,
            text=True,
            timeout=5,
        )
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return ""


def _node_is_code(node: dict) -> bool:
    domains = set(node.get("domains") or [])
    extra = node.get("extra") or {}
    return (
        "code" in domains
        or bool(extra.get("relative_path"))
        or node.get("prov_activity") == "code-ingest"
    )


def _node_matches_root(node: dict, root: Path | None) -> bool:
    """Return whether a code node belongs to the requested repo root."""
    if root is None:
        return True

    extra = node.get("extra") or {}
    repo_root = extra.get("repo_root")
    if repo_root:
        try:
            if Path(repo_root).expanduser().resolve() == root:
                return True
        except OSError:
            pass

    prov_source = node.get("prov_source") or ""
    if prov_source:
        try:
            Path(prov_source).expanduser().resolve().relative_to(root)
            return True
        except (OSError, ValueError):
            pass

    return False


def _ua_node_type(node: dict) -> str:
    if node.get("type") == "artifact":
        return "file"
    extra = node.get("extra") or {}
    kind = str(extra.get("kind", "")).lower()
    if any(k in kind for k in ("class", "struct", "interface", "trait", "enum")):
        return "class"
    if "function" in kind or "method" in kind:
        return "function"
    return "concept"


def _summary(node: dict) -> str:
    content = (node.get("content") or "").strip()
    if not content:
        return ""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    return " ".join(lines[:3])[:600]


def _complexity(node: dict) -> int:
    extra = node.get("extra") or {}
    if "complexity" in extra:
        try:
            return int(extra["complexity"])
        except (TypeError, ValueError):
            return 1
    return max(
        1,
        int(extra.get("class_count") or 0)
        + int(extra.get("function_count") or 0)
        + len(extra.get("public_methods") or []),
    )


def _layer_for_path(path: str, node_type: str) -> str:
    lowered = path.lower()
    if node_type == "class":
        return "domain"
    if any(part in lowered for part in ("api", "route", "router", "endpoint", "controller")):
        return "api"
    if any(part in lowered for part in ("service", "workflow", "usecase", "use_case")):
        return "service"
    if any(part in lowered for part in ("model", "schema", "store", "repo", "repository", "db", "database", "migration")):
        return "data"
    if any(part in lowered for part in ("component", "view", "page", "ui", "frontend")):
        return "ui"
    if any(part in lowered for part in ("test", "spec")):
        return "test"
    return "core"


_EDGE_MAP = {
    "depends_on": "depends_on",
    "implements": "depends_on",
    "context_of": "contains",
    "relates_to": "related",
    "blocks": "related",
    "answers": "related",
    "contradicts": "related",
    "spawned_from": "related",
    "supersedes": "related",
    "exemplifies": "related",
}


def export_understand_anything(
    store: "Store",
    *,
    directory: str | Path | None = None,
    project_name: str | None = None,
    limit: int = 10000,
) -> dict[str, Any]:
    """Project Kindex code nodes into an Understand-Anything-compatible graph."""
    root = Path(directory).resolve() if directory else None
    all_nodes = store.all_nodes(limit=limit)
    code_nodes = [
        n for n in all_nodes
        if _node_is_code(n) and _node_matches_root(n, root)
    ]
    code_ids = {n["id"] for n in code_nodes}

    if project_name is None:
        project_name = root.name if root else "kindex-code-map"

    ua_nodes = []
    layer_members: dict[str, list[str]] = {}
    languages: set[str] = set()

    for node in code_nodes:
        extra = node.get("extra") or {}
        rel_path = extra.get("relative_path") or node.get("prov_source") or ""
        language = extra.get("language") or next(
            (d for d in node.get("domains", []) if d != "code"),
            "",
        )
        if language:
            languages.add(str(language))
        ua_type = _ua_node_type(node)
        layer = _layer_for_path(str(rel_path), ua_type)
        layer_members.setdefault(layer, []).append(node["id"])
        ua_nodes.append({
            "id": node["id"],
            "type": ua_type,
            "name": node.get("title", ""),
            "filePath": rel_path,
            "summary": _summary(node),
            "tags": sorted(set(node.get("domains") or [])),
            "complexity": _complexity(node),
            "languageNotes": {
                "language": language,
                "kindexType": node.get("type", ""),
                "toolTier": extra.get("tool_tier", ""),
            },
        })

    ua_edges = []
    seen_edges: set[tuple[str, str, str]] = set()
    for node in code_nodes:
        for edge in store.edges_from(node["id"]):
            target = edge.get("to_id")
            if target not in code_ids:
                continue
            edge_type = _EDGE_MAP.get(edge.get("type", ""), "related")
            key = (node["id"], target, edge_type)
            if key in seen_edges:
                continue
            seen_edges.add(key)
            ua_edges.append({
                "source": node["id"],
                "target": target,
                "type": edge_type,
                "direction": "outbound",
                "weight": edge.get("weight", 0.5),
            })

    layers = [
        {
            "id": layer_id,
            "name": layer_id.replace("_", " ").title(),
            "description": f"Kindex-inferred {layer_id} layer.",
            "nodeIds": sorted(node_ids),
        }
        for layer_id, node_ids in sorted(layer_members.items())
    ]

    tour = [
        {
            "order": i + 1,
            "title": layer["name"],
            "description": f"Review {len(layer['nodeIds'])} node(s) in the {layer['name']} layer.",
            "nodeIds": layer["nodeIds"][:25],
        }
        for i, layer in enumerate(layers)
    ]

    return {
        "version": UA_VERSION,
        "project": {
            "name": project_name,
            "description": f"Code map exported from Kindex for {project_name}.",
            "languages": sorted(languages),
            "frameworks": [],
            "analyzedAt": _now(),
            "gitCommitHash": _git_commit(root),
        },
        "nodes": ua_nodes,
        "edges": ua_edges,
        "layers": layers,
        "tour": tour,
    }


def _ua_kindex_id(source_id: str) -> str:
    return f"ua-{_sha(source_id)}"


def _resolve_ua_graph(path_or_directory: str | Path) -> Path:
    path = Path(path_or_directory).expanduser().resolve()
    if path.is_dir():
        path = path / ".understand-anything" / "knowledge-graph.json"
    return path


def ingest_understand_anything(
    store: "Store",
    path_or_directory: str | Path,
    *,
    limit: int = 10000,
    verbose: bool = False,
) -> IngestResult:
    """Ingest an Understand-Anything knowledge-graph.json into Kindex."""
    graph_path = _resolve_ua_graph(path_or_directory)
    if not graph_path.exists():
        return IngestResult(errors=[f"Understand-Anything graph not found: {graph_path}"])

    try:
        graph = json.loads(graph_path.read_text())
    except json.JSONDecodeError as e:
        return IngestResult(errors=[f"Invalid JSON in {graph_path}: {e}"])

    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    project = graph.get("project") or {}

    created = updated = skipped = edges_created = 0
    id_map: dict[str, str] = {}

    for item in nodes[:limit]:
        source_id = str(item.get("id") or "")
        if not source_id:
            skipped += 1
            continue
        node_id = _ua_kindex_id(source_id)
        id_map[source_id] = node_id

        title = item.get("name") or item.get("filePath") or source_id
        content = item.get("summary") or ""
        tags = ["code", "understand-anything"] + list(item.get("tags") or [])
        extra = {
            "source": "understand-anything",
            "source_id": source_id,
            "source_type": item.get("type", ""),
            "relative_path": item.get("filePath", ""),
            "complexity": item.get("complexity", 1),
            "project": project.get("name", ""),
            "language_notes": item.get("languageNotes", {}),
        }
        node_type = "artifact" if item.get("type") in ("file", "module") else "concept"

        existing = store.get_node(node_id)
        if existing:
            store.update_node(
                node_id,
                title=title,
                content=content,
                domains=sorted(set(tags)),
                extra=extra,
                prov_source=str(graph_path),
            )
            updated += 1
        else:
            store.add_node(
                title=title,
                content=content,
                node_id=node_id,
                node_type=node_type,
                domains=sorted(set(tags)),
                prov_activity="understand-anything-import",
                prov_source=str(graph_path),
                extra=extra,
            )
            created += 1

    for edge in edges:
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        from_id = id_map.get(source)
        to_id = id_map.get(target)
        if not from_id or not to_id:
            continue
        edge_type = edge.get("type") or "related"
        if edge_type in ("imports", "depends_on", "calls"):
            kindex_type = "depends_on" if edge_type != "calls" else "relates_to"
        elif edge_type == "contains":
            kindex_type = "context_of"
        else:
            kindex_type = "relates_to"
        store.add_edge(
            from_id,
            to_id,
            edge_type=kindex_type,
            weight=float(edge.get("weight", 0.5) or 0.5),
            provenance=f"understand-anything import: {edge_type}",
            bidirectional=False,
        )
        edges_created += 1

    if verbose:
        print(f"  Imported {created} created, {updated} updated, {edges_created} edges from {graph_path}")

    return IngestResult(created=created, updated=updated, skipped=skipped)
