"""Persisted evidence locators must not enlarge a Lite server's repository scope.

The record and output formats come from the public durable-source tests.
Calls exercise actual MCP dispatch; no Lite implementation helpers are used.
"""

import builtins
import io
import json
import sqlite3
from pathlib import Path
from urllib.parse import unquote, urlsplit

import pytest

pytest.importorskip("mcp")

from test_mcp_lite import _call, _server, isolated  # noqa: F401


def locator(store, node_id, path=None):
    return {"version": 1, "graph_id": store.graph_id, "node_id": node_id,
            "db_path": str(path or store.db_path.resolve())}


def entries(output):
    marker = "Source resolution:"
    payload = json.loads(output.split(marker, 1)[1].strip()) if marker in output else json.loads(output)

    def visit(value):
        if isinstance(value, dict):
            if value.get("status") in {"resolved", "unresolved", "refused"}:
                yield value
            for child in value.values():
                yield from visit(child)
        elif isinstance(value, list):
            for child in value:
                yield from visit(child)

    return list(visit(payload))


@pytest.mark.parametrize("foreign_graph,path_form", [
    ("outer", "direct"), ("other", "direct"), ("outer", "symlink"),
    ("outer", "hardlink"), ("other", "parent_traversal"),
])
def test_lite_never_opens_foreign_saved_evidence_even_with_valid_identity(
        isolated, monkeypatch, foreign_graph, path_form):
    local = isolated["local"]
    foreign = isolated[foreign_graph]
    foreign_id = "222222222222" if foreign_graph == "outer" else "333333333333"
    saved_path = foreign.db_path.resolve()
    if path_form in {"symlink", "hardlink"}:
        saved_path = isolated["repo"] / ".kin" / ("foreign-" + path_form + ".sqlite")
        if path_form == "symlink":
            saved_path.symlink_to(foreign.db_path)
        else:
            saved_path.hardlink_to(foreign.db_path)
    elif path_form == "parent_traversal":
        saved_path = isolated["repo"] / ".." / "sibling" / ".kin/local/kindex/kindex.db"
    records = [locator(local, "111111111111"),
               locator(foreign, foreign_id, saved_path)]
    derived = local.add_node("Scoped durable source observation",
                             extra={"source_refs": records})
    server = _server(isolated["repo"])
    forbidden_paths = {foreign.db_path.resolve(), saved_path.absolute(), saved_path.resolve()}
    attempts = []

    def foreign_path(value):
        if not isinstance(value, (str, bytes, Path)):
            return False
        if isinstance(value, bytes):
            value = value.decode()
        raw = str(value)
        if raw.startswith("file:"):
            raw = unquote(urlsplit(raw).path)
        return Path(raw).absolute() in forbidden_paths or Path(raw).resolve() in forbidden_paths

    def guard(original):
        def checked(path, *args, **kwargs):
            if foreign_path(path):
                attempts.append(str(path))
                raise AssertionError("Foreign durable source was opened before scope refusal")
            return original(path, *args, **kwargs)
        return checked

    monkeypatch.setattr(sqlite3, "connect", guard(sqlite3.connect))
    monkeypatch.setattr(sqlite3.dbapi2, "connect", guard(sqlite3.dbapi2.connect))
    monkeypatch.setattr(builtins, "open", guard(builtins.open))
    monkeypatch.setattr(io, "open", guard(io.open))
    output = _call(server, "show", node_id=derived, resolve_sources=True)
    assert attempts == [], attempts
    resolutions = entries(output)
    assert len(resolutions) == 2, output
    resolved = [item for item in resolutions if item["status"] == "resolved"]
    blocked = [item for item in resolutions if item["status"] in {"unresolved", "refused"}]
    assert len(resolved) == 1 and len(blocked) == 1, output
    assert "Local boundaryneedle" in json.dumps(resolved)
    assert blocked[0].get("reason"), "Foreign evidence must have an explicit refusal reason"
    assert "HOME_PRIVATE_SENTINEL" not in output
    assert "SIBLING_PRIVATE_SENTINEL" not in output
    assert "Home boundaryneedle" not in output
    assert "Sibling boundaryneedle" not in output
    assert local.peek_node(derived)["extra"]["source_refs"] == records


def test_lite_resolves_its_saved_local_sources_after_new_server_instance(isolated):
    local = isolated["local"]
    records = [locator(local, "111111111111")]
    derived = local.add_node("Persisted local evidence survives server restart",
                             extra={"source_refs": records})
    first = _server(isolated["repo"])
    first_output = _call(first, "show", node_id=derived, resolve_sources=True)
    assert entries(first_output)[0]["status"] == "resolved", first_output
    restarted = _server(isolated["repo"])
    output = _call(restarted, "show", node_id=derived, resolve_sources=True)
    result, = entries(output)
    assert result["status"] == "resolved", output
    assert "Local boundaryneedle" in json.dumps(result)
    assert local.peek_node(derived)["extra"]["source_refs"] == records


def test_lite_construction_does_not_restrict_full_mcp_durable_source_resolution(isolated, monkeypatch):
    import kindex.mcp_server as full

    local = isolated["local"]
    foreign = isolated["outer"]
    records = [locator(foreign, "222222222222")]
    derived = local.add_node("Full server explicitly resolves saved foreign evidence",
                             extra={"source_refs": records})
    monkeypatch.setattr(full, "_store", local)
    monkeypatch.setattr(full, "_config", local.config)
    lite = _server(isolated["repo"])
    lite_output = _call(lite, "show", node_id=derived, resolve_sources=True)
    assert entries(lite_output)[0]["status"] in {"refused", "unresolved"}, lite_output
    full_output = _call(full.mcp, "show", node_id=derived, resolve_sources=True)
    result, = entries(full_output)
    assert result["status"] == "resolved", full_output
    assert "Home boundaryneedle" in json.dumps(result)
    assert local.peek_node(derived)["extra"]["source_refs"] == records
