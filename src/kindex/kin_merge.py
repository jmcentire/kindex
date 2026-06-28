"""Structured 3-way merge for git-tracked ``.kin`` artifacts.

``.kin/index.json`` and ``.kin/code-map.json`` are generated, id-keyed JSON
snapshots. Git's line-based merge conflicts on them needlessly — they are sorted
node lists, so the correct merge is a structured union keyed by node id, not a
textual 3-way diff. This module powers the ``kin merge-kin`` git merge driver.

Why a union rather than "regenerate from the graph": ``index.json`` projects the
local SQLite DB, which is NOT in git. Regenerating from one machine's DB would
silently drop the *other* branch's concept/decision nodes (that DB never ingested
them). A union of the two committed files is lossless across machines. The result
is byte-identical to what ``kin index`` would emit for the merged node set, so a
later regeneration produces no spurious diff.

``code-map.json`` projects the code (which IS in the merge tree), so its content
collections are unioned here too; ``kin code-map`` is the canonical refresh for
the commit-tied ``project`` metadata.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


def load_json(path: str | Path) -> dict | None:
    """Load a ``.kin`` side for merging.

    Returns ``None`` for an absent/empty side (git passes an empty file when a
    file exists on only one branch). Raises ``ValueError`` on non-empty invalid
    JSON so the driver can decline and let git fall back to a normal conflict.
    """
    p = Path(path)
    if not p.exists():
        return None
    text = p.read_text()
    if not text.strip():
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {path}: {exc}") from exc
    return data if isinstance(data, dict) else None


def dumps_kin(obj: dict) -> str:
    """Serialize ``index.json`` exactly as ``write_kin_index`` does (sort_keys),
    so a later ``kin index`` regeneration yields no diff."""
    return json.dumps(obj, indent=2, sort_keys=True) + "\n"


def dumps_code_map(obj: dict) -> str:
    """Serialize ``code-map.json`` exactly as ``kin export code-map`` does — no
    ``sort_keys`` (insertion-order keys), matching the canonical exporter so a
    later regeneration yields no diff. The merge reuses the original node/layer
    dicts, so their key order is already the exporter's."""
    return json.dumps(obj, indent=2) + "\n"


def _three_way_union(
    base: list[dict] | None,
    ours: list[dict] | None,
    theirs: list[dict] | None,
    key: Callable[[dict], Any],
    pick: Callable[[dict, dict], dict] | None = None,
) -> dict[Any, dict]:
    """3-way set merge of id-keyed item lists.

    Union of ``ours`` and ``theirs``; on a key present in both, ``pick`` chooses
    (default: keep ``ours``). ``base`` is used to honor deletions: an item present
    on exactly one side and unchanged there from base was deleted on the other
    side, so it is dropped. Returns a key -> item dict.
    """
    om = {key(x): x for x in (base or [])}
    am = {key(x): x for x in (ours or [])}
    bm = {key(x): x for x in (theirs or [])}
    out: dict[Any, dict] = {}
    for k in set(am) | set(bm):
        xa, xb = am.get(k), bm.get(k)
        if xa is not None and xb is not None:
            out[k] = pick(xa, xb) if pick else xa
        elif xa is not None:
            if k in om and xa == om[k]:
                continue  # unchanged on ours, deleted on theirs
            out[k] = xa
        else:
            if k in om and xb == om[k]:
                continue  # unchanged on theirs, deleted on ours
            out[k] = xb
    return out


def _newer(a: dict, b: dict) -> dict:
    """Pick the node with the later ``updated_at`` (ISO strings sort lexically).

    On an exact timestamp tie, ``a`` (ours) wins — deterministic per merge but
    direction-dependent. Acceptable: the snapshot is advisory and a later
    ``kin index`` from the authoritative DB overwrites it regardless.
    """
    return b if str(b.get("updated_at", "")) > str(a.get("updated_at", "")) else a


def merge_index(
    base: dict | None, ours: dict | None, theirs: dict | None
) -> dict:
    """Union ``.kin/index.json`` node sets; recompute the derived header."""
    head = ours or theirs or {}
    merged = _three_way_union(
        (base or {}).get("nodes"),
        (ours or {}).get("nodes"),
        (theirs or {}).get("nodes"),
        key=lambda n: n["id"],
        pick=_newer,
    )
    nodes = [merged[k] for k in sorted(merged)]
    return {
        "domains": sorted({d for n in nodes for d in (n.get("domains") or [])}),
        "node_count": len(nodes),
        "nodes": nodes,
        "repo": head.get("repo"),
        "version": head.get("version", 1),
    }


def merge_code_map(
    base: dict | None, ours: dict | None, theirs: dict | None
) -> dict:
    """Union ``.kin/code-map.json`` content collections.

    Nodes/edges are unioned (lossless across branches); layers union their
    members; ``tour`` is recomputed from the merged layers. ``project`` keeps
    ours' commit-tied metadata (``kin code-map`` refreshes it) but unions the
    detected ``languages``.
    """
    o, a, b = base or {}, ours or {}, theirs or {}

    node_map = _three_way_union(
        o.get("nodes"), a.get("nodes"), b.get("nodes"), key=lambda n: n["id"]
    )
    # Match the exporter's _canonical_code_node_key order: (filePath, type, id).
    nodes = sorted(
        node_map.values(),
        key=lambda n: (n.get("filePath", ""), n.get("type", ""), n.get("id", "")),
    )

    edge_map = _three_way_union(
        o.get("edges"), a.get("edges"), b.get("edges"),
        key=lambda e: (e.get("source"), e.get("target"), e.get("type")),
    )
    edges = sorted(
        edge_map.values(),
        key=lambda e: (e.get("source", ""), e.get("target", ""), e.get("type", "")),
    )

    # Layers: union by id, union member node ids.
    layers_by_id: dict[Any, dict] = {}
    for layer in (a.get("layers") or []) + (b.get("layers") or []):
        lid = layer.get("id")
        existing = layers_by_id.get(lid)
        if existing is None:
            layers_by_id[lid] = {**layer, "nodeIds": list(layer.get("nodeIds") or [])}
        else:
            existing["nodeIds"] = sorted(
                set(existing["nodeIds"]) | set(layer.get("nodeIds") or [])
            )
    present_ids = {n["id"] for n in nodes}
    layers = []
    for lid in sorted(layers_by_id, key=lambda k: str(k)):
        layer = layers_by_id[lid]
        members = sorted(nid for nid in layer.get("nodeIds") or [] if nid in present_ids)
        layers.append({**layer, "nodeIds": members})

    tour = [
        {
            "order": i + 1,
            "title": layer.get("name"),
            "description": f"Review {len(layer['nodeIds'])} node(s) in the {layer.get('name')} layer.",
            "nodeIds": layer["nodeIds"][:25],
        }
        for i, layer in enumerate(layers)
    ]

    project = dict(a.get("project") or b.get("project") or {})
    langs = set(project.get("languages") or [])
    langs |= set((b.get("project") or {}).get("languages") or [])
    project["languages"] = sorted(langs)

    return {
        "version": a.get("version") or b.get("version"),
        "project": project,
        "nodes": nodes,
        "edges": edges,
        "layers": layers,
        "tour": tour,
    }


# Dispatch by the in-repo filename git passes as %P. Each artifact has its own
# serializer matching its canonical writer so a post-merge regeneration is a no-op.
_MERGERS: dict[str, Callable[[dict | None, dict | None, dict | None], dict]] = {
    "index.json": merge_index,
    "code-map.json": merge_code_map,
}
_SERIALIZERS: dict[str, Callable[[dict], str]] = {
    "index.json": dumps_kin,
    "code-map.json": dumps_code_map,
}


def merge_for(
    name: str, base: dict | None, ours: dict | None, theirs: dict | None
) -> dict | None:
    """Merge by ``.kin`` filename; ``None`` if the filename is not recognized."""
    merger = _MERGERS.get(Path(name).name)
    if merger is None:
        return None
    return merger(base, ours, theirs)


def merge_kin_files(
    repo_path: str, base_file: str, ours_file: str, theirs_file: str
) -> str | None:
    """Driver entrypoint. Returns merged text to write to the ours (%A) file, or
    ``None`` to decline (unknown file / invalid JSON) so git keeps the conflict."""
    if Path(repo_path).name not in _MERGERS:
        return None
    try:
        base = load_json(base_file)
        ours = load_json(ours_file)
        theirs = load_json(theirs_file)
    except ValueError:
        return None
    merged = merge_for(repo_path, base, ours, theirs)
    if merged is None:
        return None
    return _SERIALIZERS[Path(repo_path).name](merged)
