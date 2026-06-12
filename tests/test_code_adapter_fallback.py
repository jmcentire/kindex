"""Regression tests for code adapter fallback behavior."""
from __future__ import annotations

import json
from types import SimpleNamespace

from kindex.adapters import code
from kindex.config import Config
from kindex.store import Store


def test_run_ctags_keeps_valid_output_from_failed_process(tmp_path, monkeypatch):
    source = tmp_path / "main.py"
    source.write_text("def main():\n    pass\n")
    tag = {
        "_type": "tag",
        "name": "main",
        "path": str(source),
        "language": "Python",
        "line": 1,
        "kind": "function",
    }

    def fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=1,
            stdout=json.dumps(tag) + "\n",
            stderr="one source file failed",
        )

    monkeypatch.setattr(code.subprocess, "run", fake_run)

    assert code._run_ctags([source], tmp_path) == [tag]


def test_run_ctags_retries_files_missing_from_failed_batch(tmp_path, monkeypatch):
    good_source = tmp_path / "good.py"
    bad_source = tmp_path / "bad.py"
    good_source.write_text("def good():\n    pass\n")
    bad_source.write_text("not valid python\n")
    tag = {
        "_type": "tag",
        "name": "good",
        "path": str(good_source),
        "language": "Python",
        "line": 1,
        "kind": "function",
    }

    def fake_run_once(files, _repo_root):
        if len(files) > 1:
            return [], True
        if files[0] == good_source:
            return [tag], False
        return [], True

    monkeypatch.setattr(code, "_run_ctags_once", fake_run_once)

    assert code._run_ctags([good_source, bad_source], tmp_path) == [tag]


def test_ingest_keeps_modules_and_uses_treesitter_when_ctags_is_empty(
    tmp_path,
    monkeypatch,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("import helper\n")
    (repo / "helper.py").write_text("def helper():\n    return 1\n")
    (repo / "page.astro").write_text("---\n---\n<div>hello</div>\n")

    monkeypatch.setattr(code, "_run_ctags", lambda *_args: [])
    monkeypatch.setattr(code, "_check_cscope", lambda: False)
    monkeypatch.setattr(
        code,
        "_ts_extract_imports_python",
        lambda *_args: [("helper", "helper")],
    )
    monkeypatch.setattr(code, "_ts_extract_calls", lambda *_args: [])

    class FakeParser:
        def parse(self, _source):
            return object()

    seen_languages = []

    def fake_check_treesitter(language):
        seen_languages.append(language)
        return FakeParser() if language == "Python" else None

    monkeypatch.setattr(code, "_check_treesitter", fake_check_treesitter)

    cfg = Config(data_dir=str(tmp_path / "kindex"))
    store = Store(cfg)
    try:
        result = code.ingest_code(store, repo)
        assert result.errors == []
        assert result.created == 3
        assert seen_languages == ["Python", "Astro"]

        modules = {
            node["title"]: node
            for node in store.all_nodes(node_type="artifact", limit=100)
        }
        assert set(modules) == {"main.py", "helper.py", "page.astro"}
        assert modules["main.py"]["extra"]["language"] == "Python"
        assert modules["main.py"]["extra"]["language_source"] == "extension"
        assert modules["main.py"]["extra"]["ctags_tag_count"] == 0
        assert modules["main.py"]["extra"]["tool_tier"] == "C"
        assert modules["page.astro"]["extra"]["language"] == "Astro"
        assert modules["page.astro"]["extra"]["language_source"] == "extension"
        assert modules["page.astro"]["extra"]["tool_tier"] == "A"
        assert modules["page.astro"]["extra"]["line_count"] == 3

        edges = store.edges_from(modules["main.py"]["id"])
        assert any(
            edge.get("type") == "depends_on"
            and edge.get("to_id") == modules["helper.py"]["id"]
            for edge in edges
        )
    finally:
        store.close()
