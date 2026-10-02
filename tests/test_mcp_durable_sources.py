"""Durable source provenance must outlive MCP result handles (issue #70)."""

import json
from pathlib import Path
import sqlite3
import subprocess

import pytest

pytest.importorskip("mcp")

from kindex.config import Config
from kindex.store import Store
from test_mcp_cross_graph import graphs  # noqa: F401 -- shared public-tool fixture


def test_merged_deleted_and_recreated_worktree_does_not_recover_local_evidence(
        tmp_path, monkeypatch):
    import kindex.mcp_server as server

    main = tmp_path / "main"
    feature = tmp_path / "feature"
    main.mkdir()

    def git(root, *args):
        return subprocess.run([
            "git", "-c", "user.name=Kindex Test", "-c", "user.email=test@example.invalid",
            "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null",
            "-C", str(root), *args,
        ], check=True, capture_output=True, text=True)

    git(main, "init", "-b", "main")
    (main / ".kin").mkdir()
    (main / ".kin" / "config").write_text("name: worktree-lifecycle\n")
    git(main, "add", ".kin/config")
    git(main, "commit", "-m", "Initialize repository")
    git(main, "worktree", "add", "-b", "feature", str(feature))

    home_dir = tmp_path / "global"

    def config(root):
        cfg = Config(data_dir=str(root / ".kin" / "local" / "kindex"))
        cfg._project_path = root
        cfg._global_data_dir = str(home_dir)
        return cfg

    local = Store(config(feature))
    primary = Store(config(main))
    home = Store(Config(data_dir=str(home_dir)))
    monkeypatch.setattr(server, "_store", local)
    monkeypatch.setattr(server, "_config", local.config)
    try:
        local_id, _, refs = _refs(server, local, home)
        source_graph_id = local.graph_id
        primary.add_node("Sibling worktree impostor", node_id=local_id)
        assert primary.graph_id != source_graph_id
        server.add("Capture from temporary worktree", source_refs=refs)
        capture = home.get_node_by_title("Capture from temporary worktree")
        records = _records(capture)
        assert any(record["graph_id"] == source_graph_id for record in records)

        (feature / "merged.txt").write_text("Merged code survives deletion.\n")
        git(feature, "add", "merged.txt")
        git(feature, "commit", "-m", "Feature change")
        git(main, "merge", "--ff-only", "feature")
        assert (main / "merged.txt").is_file()
        # Neither the Git merge nor its sibling graph absorbed local evidence.
        assert primary.peek_node(local_id)["title"] == "Sibling worktree impostor"
        local.close()
        monkeypatch.setattr(server, "_store", primary)
        monkeypatch.setattr(server, "_config", primary.config)
        git(main, "worktree", "remove", "--force", str(feature))
        assert not feature.exists()
        handle = server._graph_ref("global", capture["id"])
        entries = _entries(server.show(handle, resolve_sources=True))
        missing = [entry for entry in entries if entry["status"] == "unresolved"]
        assert len(missing) == 1 and missing[0]["reason"] == "database_missing"
        assert home.peek_node(capture["id"])["extra"]["source_refs"] == records
        assert "Sibling worktree impostor" not in json.dumps(entries)
        assert not feature.exists()  # Resolution did not recreate it.

        # Recreating the branch restores tracked files, not its ignored database.
        git(main, "worktree", "add", str(feature), "feature")
        assert not local.db_path.exists()
        replacement = Store(config(feature))
        try:
            replacement.add_node("Recreated worktree impostor", node_id=local_id)
            assert replacement.graph_id != source_graph_id
            entries = _entries(server.show(handle, resolve_sources=True))
            missing = [entry for entry in entries if entry["status"] == "unresolved"]
            assert len(missing) == 1
            assert missing[0]["reason"] == "graph_identity_mismatch"
            assert "Recreated worktree impostor" not in json.dumps(entries)
        finally:
            replacement.close()
    finally:
        local.close()
        primary.close()
        home.close()


def _refs(server, local, home):
    # Both DBs must exist before the graph-aware MCP scope is initialized.
    local_id = local.add_node("Original project evidence", content="local origin")
    global_id = home.add_node("Original global evidence", content="global origin")
    return local_id, global_id, (
        f"{server._graph_ref('project', local_id)},"
        f"{server._graph_ref('global', global_id)}"
    )


def _records(node):
    records = node["extra"].get("source_refs")
    assert isinstance(records, list) and records, "Evidence handles were not persisted"
    for record in records:
        assert isinstance(record, dict)
        assert record["version"] == 1
        assert record["node_id"]
        assert record["graph_id"]
        assert Path(record["db_path"]).is_absolute()
        assert not record["node_id"].startswith(("project:", "global:"))
    return records


def _entries(output):
    if isinstance(output, str):
        marker = "Source resolution:"
        payload = json.loads(output.split(marker, 1)[1].strip()) if marker in output else json.loads(output)
    else:
        payload = output
    def visit(item):
        if isinstance(item, dict):
            if item.get("status") in {"resolved", "unresolved"}:
                yield item
            for value in item.values():
                yield from visit(value)
        elif isinstance(item, list):
            for value in item:
                yield from visit(value)
    return list(visit(payload))


@pytest.mark.parametrize("tool", ["add", "task_add", "watch_add", "learn"])
def test_all_derived_captures_persist_mixed_graph_sources(graphs, tool, monkeypatch):
    server, local, home, _ = graphs
    local_id, global_id, refs = _refs(server, local, home)
    before = {row[0] for row in home.conn.execute("SELECT id FROM nodes")}
    text = ("We decided to use Redis for caching because its speed supports "
            "Distributed Systems architecture under production load.")
    if tool == "learn":
        monkeypatch.setattr("kindex.extract.extract", lambda *args, **kwargs: {
            "concepts": [{"title": "Durable learned finding",
                          "content": "Long substantive evidence content across mixed stores",
                          "type": "concept"}],
            "connections": [],
        })
    result = getattr(server, tool)(text, source_refs=refs)
    assert not result.startswith("Error:"), result
    created = [home.get_node(row[0]) for row in home.conn.execute("SELECT id FROM nodes")
               if row[0] not in before]
    assert created, result
    for node in created:
        records = _records(node)
        assert {record["node_id"] for record in records} == {local_id, global_id}
        assert len({record["graph_id"] for record in records}) == 2
        by_id = {record["node_id"]: record for record in records}
        assert Path(by_id[local_id]["db_path"]) == local.db_path.resolve()
        assert Path(by_id[global_id]["db_path"]) == home.db_path.resolve()
        assert local.get_node(node["id"]) is None
        reopened = Store(home.config)
        try:
            assert reopened.get_node(node["id"])["extra"]["source_refs"] == records
        finally:
            reopened.close()


def test_show_displays_saved_sources_and_provenance_reason(graphs):
    server, local, home, _ = graphs
    local_id, _, refs = _refs(server, local, home)
    server.add("Durable derived observation", source_refs=refs)
    node = home.get_node_by_title("Durable derived observation")
    _records(node)
    output = server.show(server._graph_ref("global", node["id"]))
    assert local_id in output
    assert node["prov_why"] and node["prov_why"] in output


def test_saved_sources_resolve_original_graph_after_project_switch(graphs, tmp_path, monkeypatch):
    server, local, home, _ = graphs
    local_id, global_id, refs = _refs(server, local, home)
    old_handle = server._graph_ref("project", local_id)
    server.add("Surviving provenance", source_refs=refs)
    derived = home.get_node_by_title("Surviving provenance")
    records = _records(derived)
    next_project = tmp_path / "different-project"
    next_project.mkdir()
    cfg = Config(data_dir=str(next_project / ".kin" / "local" / "kindex"))
    cfg._project_path = next_project
    cfg._global_data_dir = str(home.db_path.parent)
    selected = Store(cfg)
    selected.add_node("Wrong colliding evidence", node_id=local_id)
    monkeypatch.setattr(server, "_store", selected)
    monkeypatch.setattr(server, "_config", cfg)
    try:
        assert "Stale graph reference" in server.edit(old_handle, append="wrong")
        assert "Stale graph reference" in server.add("Wrong derived write", source_refs=old_handle)
        current = server._graph_ref("global", derived["id"])
        entries = _entries(server.show(current, resolve_sources=True))
        assert len(entries) == len(records)
        assert all(entry["status"] == "resolved" for entry in entries)
        serialized = json.dumps(entries)
        assert "Original project evidence" in serialized
        assert "Original global evidence" in serialized
        assert "Wrong colliding evidence" not in serialized
        assert local_id in serialized and global_id in serialized
        assert selected.get_node(local_id)["content"] == ""
    finally:
        selected.close()


@pytest.mark.parametrize("failure, expected_reason", [
    ("missing", "database_missing"),
    ("replacement", "graph_identity_mismatch"),
    ("identity_missing", "graph_identity_missing"),
    ("node_missing", "node_missing"),
    ("corrupt", "database_unavailable"),
    ("outdated_schema", None),
])
def test_saved_source_failures_are_explicit_and_never_repair_source(
        graphs, tmp_path, failure, expected_reason):
    server, local, home, _ = graphs
    local_id, _, refs = _refs(server, local, home)
    server.add("Unavailable evidence observation", source_refs=refs)
    derived = home.get_node_by_title("Unavailable evidence observation")
    _records(derived)
    source_path = local.db_path
    local.close()
    if failure in {"missing", "replacement"}:
        source_path.rename(tmp_path / "original-source.sqlite")
    if failure == "replacement":
        replacement = Store(local.config)
        replacement.add_node("Replacement impostor", node_id=local_id)
        replacement.close()
    elif failure == "corrupt":
        source_path.write_bytes(b"This is deliberately not a SQLite database")
    elif failure in {"outdated_schema", "identity_missing", "node_missing"}:
        conn = sqlite3.connect(source_path)
        if failure == "outdated_schema":
            conn.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
        elif failure == "identity_missing":
            conn.execute("DELETE FROM meta WHERE key='graph_id'")
        else:
            conn.execute("DELETE FROM nodes WHERE id=?", (local_id,))
        conn.commit()
        conn.close()
    before = source_path.read_bytes() if source_path.exists() else None
    output = server.show(server._graph_ref("global", derived["id"]), resolve_sources=True)
    entries = _entries(output)
    unresolved = [entry for entry in entries if entry["status"] == "unresolved"]
    if failure == "outdated_schema":
        assert len(entries) == 2
        assert all(entry["status"] == "resolved" for entry in entries)
    else:
        assert len(unresolved) == 1
        assert unresolved[0]["reason"] == expected_reason
    assert "Replacement impostor" not in json.dumps(entries)
    assert (source_path.read_bytes() if source_path.exists() else None) == before


def test_resolving_sources_does_not_touch_source_timestamps_or_schema(graphs):
    server, local, home, _ = graphs
    local_id, _, refs = _refs(server, local, home)
    server.add("Read-only provenance observation", source_refs=refs)
    derived = home.get_node_by_title("Read-only provenance observation")
    _records(derived)
    local.conn.execute("UPDATE nodes SET last_accessed='2000-01-01' WHERE id=?", (local_id,))
    local.conn.commit()
    before = list(local.conn.iterdump())
    entries = _entries(server.show(server._graph_ref("global", derived["id"]),
                                  resolve_sources=True))
    assert len(entries) == 2
    assert all(entry["status"] == "resolved" for entry in entries)
    assert list(local.conn.iterdump()) == before


@pytest.mark.parametrize("malformed", [
    {"version": 1, "node_id": "abc123"},
    "project:expired-session:abc123",
])
def test_malformed_saved_source_is_reported_without_crashing(graphs, malformed):
    server, local, home, _ = graphs
    _, _, refs = _refs(server, local, home)
    server.add("Malformed persisted evidence", source_refs=refs)
    derived = home.get_node_by_title("Malformed persisted evidence")
    extra = derived["extra"]
    extra["source_refs"] = [malformed]
    home.conn.execute("UPDATE nodes SET extra=? WHERE id=?",
                      (json.dumps(extra), derived["id"]))
    home.conn.commit()
    entries = _entries(server.show(server._graph_ref("global", derived["id"]),
                                  resolve_sources=True))
    assert len(entries) == 1
    assert entries[0]["status"] == "unresolved"
    assert entries[0]["reason"] == "invalid_source_ref"
