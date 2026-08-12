"""Run-v8 acceptance oracles for honest fence notes and backfill bounds.

Authority: product specification ``58916cd0...`` R3.1 and R5.1,
architecture ``de100e45...`` search/retrieval surfaces, and strategy
``ede46bf4...``.  Fixtures contain multiple fenced and surviving elements so
note and backfill assertions cannot pass through a one-survivor ordering trap.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from kindex.config import Config
from kindex.retrieve import hybrid_search
from kindex.store import Store


def _env(tmp_path: Path) -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("KIN_"):
            env.pop(key)
    for key in (
        "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
        "GOOGLE_API_KEY", "VOYAGE_API_KEY",
    ):
        env.pop(key, None)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    return env


def _run_cli(tmp_path: Path, *args: str, data_dir: Path) -> subprocess.CompletedProcess:
    child_env = _env(tmp_path)
    # Config bindings are process-local.  Give the CLI child its own HOME root
    # so its post-import resolution cannot depend on the parent's state.
    child_env["HOME"] = str(data_dir.parent)
    return subprocess.run(
        [sys.executable, "-m", "kindex.cli", *args, "--data-dir", str(data_dir)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(tmp_path),
        env=child_env,
    )


def _retired_fixture(data_dir: Path) -> tuple[Store, dict[str, str]]:
    store = Store(Config(data_dir=str(data_dir)))
    titles = {
        "live": "V8 promise live result",
        "archived": "V8 promise archived result",
        "superseded": "V8 promise superseded result",
    }
    token = "v8promisetoken"
    store.add_node(titles["live"], content=token, node_id="promise-live")
    store.add_node(titles["archived"], content=token, node_id="promise-archived")
    store.update_node("promise-archived", status="archived")
    store.add_node(titles["superseded"], content=token, node_id="promise-old")
    store.supersede_node("promise-old", "replacement text without the query token")
    return store, titles


def _fence_note(output: str) -> str:
    """Extract either the retired-status note or the bounded-window note."""
    lines = []
    for line in output.splitlines():
        normalized = line.strip().lower()
        if "fenced" in normalized or (
                "candidate" in normalized
                and ("bound" in normalized or "window" in normalized)
        ):
            lines.append(line.strip())
    assert lines, f"search output omitted its fence/window note: {output!r}"
    return " ".join(lines)


def _assert_note_matches_flag(default: str, expanded: str, titles: dict[str, str]) -> None:
    note = _fence_note(default)
    named = {
        status for status in ("archived", "superseded")
        if status in note.lower()
    }
    assert named, f"fence note names no retired status: {note!r}"
    revealed = {
        status for status in ("archived", "superseded")
        if titles[status] in expanded
    }
    assert named == revealed, (
        f"note promises {sorted(named)}, but --include-archived reveals "
        f"{sorted(revealed)}; note={note!r}"
    )


@pytest.mark.red_now
def test_r3_1_cli_fence_note_promises_exactly_what_flag_reveals(tmp_path):
    """R3.1: the CLI note's named statuses equal the flag's revealed set.

    Reversion: restoring the text "archived/superseded" while the flag still
    excludes superseded content turns this red.  Reachability/non-degeneracy:
    one live, one archived, and one superseded node share a unique query token;
    default output must contain the live title and omit both retired titles,
    while a second real CLI process uses ``--include-archived``.  Parsing the
    note's status words and comparing them to two independently identifiable
    titles discriminates either allowed fix (narrow note or broaden flag).
    Base evidence: 0.30.1 names both statuses but reveals only archived, the
    exact V3/R3.1 over-promise.
    """
    data_dir = tmp_path / "cli-data"
    store, titles = _retired_fixture(data_dir)
    store.close()

    default = _run_cli(
        tmp_path, "search", "v8promisetoken", "--top-k", "10", data_dir=data_dir
    )
    expanded = _run_cli(
        tmp_path,
        "search",
        "v8promisetoken",
        "--top-k",
        "10",
        "--include-archived",
        data_dir=data_dir,
    )
    assert default.returncode == 0, default.stderr
    assert expanded.returncode == 0, expanded.stderr
    assert titles["live"] in default.stdout
    assert titles["archived"] not in default.stdout
    assert titles["superseded"] not in default.stdout
    _assert_note_matches_flag(default.stdout, expanded.stdout, titles)


@pytest.mark.red_now
def test_r3_1_mcp_fence_note_promises_exactly_what_flag_reveals(
        tmp_path, monkeypatch):
    """R3.1: MCP makes the same truthful note/flag promise as the CLI.

    Reversion: leaving the MCP formatter's combined archived/superseded note
    unchanged while its flag reveals archived only turns this red.
    Reachability/non-degeneracy: a real Store has three matching status classes
    and is installed using the existing MCP fixture seam; default and expanded
    tool calls both execute, the live title controls search reachability, and
    two retired titles make the set comparison discriminating rather than an
    ordering assertion over one survivor.  Base evidence: 0.30.1's MCP note
    over-promises superseded results for the same R3.1 reason as the CLI.
    """
    try:
        import kindex.mcp_server as mcp_mod
    except (ImportError, SystemExit) as exc:
        pytest.skip(f"authoritative MCP extra is unavailable: {exc}")

    store, titles = _retired_fixture(tmp_path / "mcp-data")
    cfg = Config(data_dir=str(tmp_path / "mcp-data"))
    try:
        monkeypatch.setattr(mcp_mod, "_store", store)
        monkeypatch.setattr(mcp_mod, "_config", cfg)
        default = mcp_mod.search("v8promisetoken", top_k=10)
        expanded = mcp_mod.search(
            "v8promisetoken", top_k=10, include_archived=True
        )
        assert titles["live"] in default
        assert titles["archived"] not in default
        assert titles["superseded"] not in default
        _assert_note_matches_flag(default, expanded, titles)
    finally:
        store.close()
        monkeypatch.setattr(mcp_mod, "_store", None)
        monkeypatch.setattr(mcp_mod, "_config", None)


def _bound_is_stated(text: str, *, top_k: int) -> bool:
    lower = text.lower().replace("`", "")
    has_window_language = "candidate" in lower and (
        "bound" in lower or "window" in lower or "first" in lower
    )
    formula = re.search(
        r"\b3\s*(?:\*|x|×|times)?\s*top[_ -]?k\b", lower
    )
    concrete = re.search(
        rf"(?:candidate\D{{0,30}}\b{3 * top_k}\b|\b{3 * top_k}\b\D{{0,30}}candidate)",
        lower,
    )
    return bool(has_window_language and (formula or concrete))


@pytest.mark.red_now
def test_r5_1_backfills_past_old_window_or_discloses_exact_bound(tmp_path):
    """R5.1: return five live hits past 3*top_k, or disclose that bound twice.

    Reversion: restoring the silent fixed ``3 * top_k`` candidate slice returns
    fewer than five while neither API docs nor the fence note states the bound,
    turning this red.  Reachability/non-degeneracy: twenty-five stronger
    expired matches fill the first fifteen FTS positions, while five weaker
    live matches remain discoverable in the full candidate set; those controls
    prove genuine live exhaustion has *not* occurred and provide five surviving
    elements.  ``hybrid_search`` then either reaches all five (exhaustive
    branch), or both its public documentation and a real CLI fence note must
    state the exact 3*top_k/15 window (bounded branch).  Base evidence: 0.30.1
    under-fills behind the old window without either disclosure, exactly R5.1.
    """
    data_dir = tmp_path / "window-data"
    data_dir.mkdir(parents=True)
    store = Store(Config(data_dir=str(data_dir)))
    token = "v8windowtoken"
    try:
        for index in range(25):
            store.add_node(
                f"{token} {token} {token} expired strong {index}",
                content=f"{token} {token} {token} {token}",
                node_id=f"expired-{index}",
                weight=1.0,
                extra={"expires": "2020-01-01"},
            )
        for index in range(5):
            store.add_node(
                f"live weak result {index}",
                content=token,
                node_id=f"live-{index}",
                weight=0.1,
            )

        full = store.fts_search(token, limit=100, include_archived=True)
        full_ids = {row["id"] for row in full}
        assert {f"live-{index}" for index in range(5)} <= full_ids, (
            f"reachability control: full search cannot see all live rows: {full_ids}"
        )
        old_window = store.fts_search(token, limit=15, include_archived=True)
        old_ids = [row["id"] for row in old_window]
        assert len(old_ids) == 15 and all(
            item.startswith("expired-") for item in old_ids
        ), (
            f"fixture failed to saturate old 3*top_k window: {old_ids}"
        )

        results = hybrid_search(store, token, top_k=5, expand_graph=False)
        result_ids = [row["id"] for row in results]
        assert all(item.startswith("live-") for item in result_ids), (
            f"retired candidates leaked through fence: {result_ids}"
        )
    finally:
        store.close()

    if len(result_ids) == 5:
        assert len(set(result_ids)) == 5
        return  # exhaustive implementation: the first allowed R5.1 branch

    assert len(result_ids) < 5, f"hybrid_search exceeded top_k: {result_ids}"
    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text()
    api_docs = (hybrid_search.__doc__ or "") + "\n" + readme
    assert _bound_is_stated(api_docs, top_k=5), (
        "bounded implementation must state the exact 3*top_k candidate window "
        "in public API documentation"
    )

    cli = _run_cli(
        tmp_path, "search", token, "--top-k", "5", data_dir=data_dir
    )
    assert cli.returncode == 0, cli.stderr
    note = _fence_note(cli.stdout)
    assert _bound_is_stated(note, top_k=5), (
        f"bounded implementation's fence note omits exact candidate window: {note!r}"
    )


@pytest.mark.red_now
def test_r5_1_window_disclosure_only_when_candidate_window_binds(tmp_path):
    """R5.1: genuine exhaustion is silent; a bounded result discloses its window."""
    data_dir = tmp_path / "window-scope"
    store = Store(Config(data_dir=str(data_dir)))
    for i in range(3):
        store.add_node(f"Window scope live {i}", content="window-scope-token", node_id=f"live-{i}")
    store.close()
    exhausted = _run_cli(tmp_path, "search", "window-scope-token", "--top-k", "10", data_dir=data_dir)
    assert exhausted.returncode == 0, exhausted.stderr
    disclosure_lines = [
        line for line in exhausted.stdout.splitlines()
        if "candidate" in line.lower()
        and ("window" in line.lower() or "bound" in line.lower())
    ]
    assert not disclosure_lines, (
        f"exhausted search disclosed a candidate window: {disclosure_lines!r}"
    )

    bounded = Store(Config(data_dir=str(data_dir)))
    for i in range(25):
        bounded.add_node(f"Window scope expired {i}", content="window-scope-bound", node_id=f"expired-{i}", weight=1.0, extra={"expires": "2020-01-01"})
    for i in range(5):
        bounded.add_node(f"Window scope survivor {i}", content="window-scope-bound", node_id=f"survivor-{i}", weight=0.1)
    bounded.close()
    limited = _run_cli(tmp_path, "search", "window-scope-bound", "--top-k", "5", data_dir=data_dir)
    assert limited.returncode == 0, limited.stderr
    assert "Window scope survivor" not in limited.stdout, (
        "reachability control: bounded fixture unexpectedly returned live survivors"
    )
    assert _bound_is_stated(_fence_note(limited.stdout), top_k=5), limited.stdout
