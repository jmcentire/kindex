"""Tests for code-map interop import/export."""

from __future__ import annotations

import json
import subprocess
import sys

from kindex.code_map import export_understand_anything, ingest_understand_anything
from kindex.config import Config
from kindex.store import Store


def run(*args, data_dir=None):
    cmd = [sys.executable, "-m", "kindex.cli", *args]
    if data_dir:
        cmd.extend(["--data-dir", str(data_dir)])
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30)


def test_export_understand_anything_projection(tmp_path):
    cfg = Config(data_dir=str(tmp_path / "data"))
    store = Store(cfg)
    try:
        store.add_node(
            "src/app.py",
            content="Module src/app.py\nDefines AppService.",
            node_id="code-mod-demo-app",
            node_type="artifact",
            domains=["code", "python"],
            prov_activity="code-ingest",
            extra={
                "relative_path": "src/app.py",
                "repo_root": str(tmp_path),
                "language": "Python",
                "class_count": 1,
                "function_count": 3,
                "tool_tier": "C",
            },
        )
        store.add_node(
            "AppService",
            content="class AppService\nPublic methods: run",
            node_id="code-sym-demo-appservice",
            node_type="concept",
            domains=["code", "python"],
            prov_activity="code-ingest",
            extra={
                "relative_path": "src/app.py",
                "repo_root": str(tmp_path),
                "language": "Python",
                "kind": "class",
                "public_methods": ["run"],
            },
        )
        store.add_edge(
            "code-sym-demo-appservice",
            "code-mod-demo-app",
            edge_type="context_of",
            weight=0.6,
            provenance="test",
            bidirectional=False,
        )

        graph = export_understand_anything(
            store,
            directory=tmp_path,
            project_name="demo",
        )
    finally:
        store.close()

    assert graph["version"] == "1.0.0"
    assert graph["project"]["name"] == "demo"
    assert graph["project"]["languages"] == ["Python"]
    assert {n["id"] for n in graph["nodes"]} == {
        "code-mod-demo-app",
        "code-sym-demo-appservice",
    }
    service = next(n for n in graph["nodes"] if n["name"] == "AppService")
    assert service["type"] == "class"
    assert service["filePath"] == "src/app.py"
    assert graph["edges"][0]["type"] == "contains"
    assert graph["layers"]
    assert graph["tour"]


def test_export_understand_anything_filters_by_repo_root(tmp_path):
    cfg = Config(data_dir=str(tmp_path / "data"))
    store = Store(cfg)
    repo_a = tmp_path / "repo-a"
    repo_b = tmp_path / "repo-b"
    repo_a.mkdir()
    repo_b.mkdir()
    try:
        store.add_node(
            "src/a.py",
            node_id="code-mod-a",
            node_type="artifact",
            domains=["code", "python"],
            prov_activity="code-ingest",
            extra={"relative_path": "src/a.py", "repo_root": str(repo_a)},
        )
        store.add_node(
            "src/b.py",
            node_id="code-mod-b",
            node_type="artifact",
            domains=["code", "python"],
            prov_activity="code-ingest",
            extra={"relative_path": "src/b.py", "repo_root": str(repo_b)},
        )

        graph = export_understand_anything(store, directory=repo_a)
    finally:
        store.close()

    assert [n["id"] for n in graph["nodes"]] == ["code-mod-a"]


def test_export_understand_anything_uses_canonical_ordering_and_source_time(tmp_path):
    cfg = Config(data_dir=str(tmp_path / "data"))
    store = Store(cfg)
    try:
        store.add_node(
            "src/z.py",
            node_id="code-mod-demo-z",
            node_type="artifact",
            domains=["python", "code"],
            weight=1.0,
            prov_activity="code-ingest",
            extra={"relative_path": "src/z.py", "repo_root": str(tmp_path)},
        )
        store.add_node(
            "src/a.py",
            node_id="code-mod-demo-a",
            node_type="artifact",
            domains=["code", "python"],
            weight=0.1,
            prov_activity="code-ingest",
            extra={"relative_path": "src/a.py", "repo_root": str(tmp_path)},
        )
        store.add_edge(
            "code-mod-demo-z",
            "code-mod-demo-a",
            edge_type="depends_on",
            weight=0.4,
            bidirectional=False,
        )
        store.add_edge(
            "code-mod-demo-a",
            "code-mod-demo-z",
            edge_type="relates_to",
            weight=0.2,
            bidirectional=False,
        )

        graph = export_understand_anything(store, directory=tmp_path)
        graph_again = export_understand_anything(store, directory=tmp_path)
        node_times = [
            store.get_node("code-mod-demo-a")["updated_at"],
            store.get_node("code-mod-demo-z")["updated_at"],
        ]
    finally:
        store.close()

    assert graph == graph_again
    assert [node["id"] for node in graph["nodes"]] == [
        "code-mod-demo-a",
        "code-mod-demo-z",
    ]
    assert graph["edges"] == sorted(
        graph["edges"],
        key=lambda edge: (edge["source"], edge["target"], edge["type"]),
    )
    assert graph["project"]["analyzedAt"] == max(node_times)


def test_ingest_understand_anything_graph(tmp_path):
    graph_dir = tmp_path / "repo" / ".understand-anything"
    graph_dir.mkdir(parents=True)
    graph_path = graph_dir / "knowledge-graph.json"
    graph_path.write_text(json.dumps({
        "project": {"name": "demo", "languages": ["Python"]},
        "nodes": [
            {
                "id": "file:src/app.py",
                "type": "file",
                "name": "src/app.py",
                "filePath": "src/app.py",
                "summary": "Application module.",
                "tags": ["python"],
                "complexity": 2,
            },
            {
                "id": "class:src/app.py:AppService",
                "type": "class",
                "name": "AppService",
                "filePath": "src/app.py",
                "summary": "Runs the app.",
                "tags": ["service"],
                "complexity": 3,
            },
        ],
        "edges": [
            {
                "source": "class:src/app.py:AppService",
                "target": "file:src/app.py",
                "type": "contains",
                "weight": 0.8,
            },
        ],
        "layers": [],
        "tour": [],
    }))

    cfg = Config(data_dir=str(tmp_path / "data"))
    store = Store(cfg)
    try:
        result = ingest_understand_anything(store, tmp_path / "repo")
        assert result.created == 2
        nodes = store.all_nodes(tags=["understand-anything"], limit=10)
        assert len(nodes) == 2
        app_service = next(n for n in nodes if n["title"] == "AppService")
        assert app_service["extra"]["source"] == "understand-anything"
        assert app_service["extra"]["relative_path"] == "src/app.py"
        edges = store.edges_from(app_service["id"])
        assert len(edges) == 1
        assert edges[0]["type"] == "context_of"
    finally:
        store.close()


def test_cli_export_code_map(tmp_path):
    data_dir = tmp_path / "data"
    cfg = Config(data_dir=str(data_dir))
    store = Store(cfg)
    try:
        store.add_node(
            "src/app.py",
            node_id="code-mod-cli-app",
            node_type="artifact",
            domains=["code", "python"],
            prov_activity="code-ingest",
            extra={"relative_path": "src/app.py", "language": "Python"},
        )
    finally:
        store.close()

    r = run(
        "export",
        "code-map",
        "--format",
        "understand-anything",
        "--project-name",
        "cli-demo",
        data_dir=data_dir,
    )
    assert r.returncode == 0, r.stderr
    graph = json.loads(r.stdout)
    assert graph["project"]["name"] == "cli-demo"
    assert graph["nodes"][0]["id"] == "code-mod-cli-app"


def test_adapter_is_registered():
    from kindex.adapters.registry import discover

    assert "understand-anything" in discover()
