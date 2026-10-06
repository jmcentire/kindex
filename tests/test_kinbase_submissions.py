"""Independent acceptance tests for explicit AI observations submitted to Kinbase.

Only a disposable native executable runs. The public bridge and actual MCP
tool boundary are exercised; no organization or developer graph is contacted.
"""

import asyncio
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from kindex.config import Config
from kindex.store import Store


def submit(repo, text, node_type="concept", **kwargs):
    return importlib.import_module("kindex.kinbase").submit_observation(
        repo, text, node_type=node_type, **kwargs)


def attempt(repo, text, node_type="concept", **kwargs):
    try:
        return submit(repo, text, node_type, **kwargs)
    except (ValueError, RuntimeError, OSError) as exc:
        return {"error": str(exc)}


def refused(result):
    assert isinstance(result, dict), result
    assert result.get("ok") is False or result.get("error"), result


def call(server, tool_name, **arguments):
    from mcp.server.fastmcp.exceptions import ToolError

    try:
        result = asyncio.run(server.call_tool(tool_name, arguments))
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    if isinstance(result, tuple):
        result = result[0]
    if hasattr(result, "content"):
        result = result.content
    text = "\n".join(block.text for block in result if hasattr(block, "text"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"error": text} if any(s in text.lower() for s in ("refus", "error", "denied")) else {"text": text}


@pytest.fixture
def submission_env(tmp_path, monkeypatch):
    import kindex.config as config_module

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for key in ("KIN_PROFILE", "KIN_PROJECT", "KIN_PROJECT_PATH", "KIN_DATA_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(config_module, "_GLOBAL_PATHS", [home / "absent.yaml"])
    repo = tmp_path / "repo with spaces"
    graph = repo / ".kin/local/kindex"
    local = Store(Config(data_dir=str(graph)))
    local.add_node("Private unrelated corpus", content="DO_NOT_SUBMIT_CORPUS_SENTINEL")
    monkeypatch.chdir(repo)
    log = tmp_path / "native-calls.jsonl"
    receipt = tmp_path / "receipt.json"
    receipt.write_text(json.dumps({"ok": True, "stage": "ingested",
                                   "receipt_id": "native-fixture-receipt", "ingested": 1}))
    executable = tmp_path / "fake-kinbase"
    executable.write_text(
        "#!" + sys.executable + "\n"
        "import json, sqlite3, sys\n"
        "from pathlib import Path\n"
        "args = sys.argv[1:]\n"
        "assert args[:2] == ['ingest', 'kindex'], args\n"
        "artifact = Path(args[2])\n"
        "db = sqlite3.connect(artifact.resolve().as_uri() + '?mode=ro', uri=True)\n"
        "db.row_factory = sqlite3.Row\n"
        "rows = [{key: (value.decode('utf-8') if isinstance(value, bytes) else value) "
        "for key, value in dict(row).items()} for row in db.execute('SELECT * FROM nodes')]\n"
        "db.close()\n"
        f"with open({str(log)!r}, 'a') as out:\n"
        "    out.write(json.dumps({'argv': args, 'artifact': str(artifact), 'rows': rows}) + '\\n')\n"
        f"reply = json.loads(Path({str(receipt)!r}).read_text())\n"
        "print(json.dumps(reply))\n"
        "sys.exit(1 if reply.get('error') else 0)\n"
    )
    executable.chmod(0o700)
    yield dict(repo=repo, graph=graph, local=local, binary=str(executable), log=log,
               receipt=receipt, home=home)
    local.close()


def invocations(env):
    if not env["log"].exists():
        return []
    return [json.loads(line) for line in env["log"].read_text().splitlines()]


@pytest.mark.parametrize("node_type", ["concept", "decision", "constraint", "question"])
def test_native_submission_is_exactly_one_fixed_provenance_observation(submission_env, node_type, monkeypatch):
    env = submission_env
    observed = []
    real_run = subprocess.run

    def audited_run(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and str(argv[0]) == env["binary"]:
            observed.append((argv, kwargs))
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", audited_run)
    text = "Observed explicit boundary; $(touch SHOULD_NEVER_RUN) stays literal."
    result = submit(env["repo"], text, node_type, binary=env["binary"])
    assert "native-fixture-receipt" in json.dumps(result)
    assert observed, "Native submission never reached the controlled executable"
    for argv, kwargs in observed:
        assert isinstance(argv, (list, tuple))
        assert not kwargs.get("shell", False)
        assert 0 < kwargs.get("timeout", 0) <= 120
    invocation, = invocations(env)
    artifact = Path(invocation["artifact"])
    assert artifact.resolve().is_relative_to((env["graph"] / "kinbase-submissions").resolve())
    assert invocation["argv"] == ["ingest", "kindex", str(artifact), "--repo", str(env["repo"].resolve()), "--json"]
    row, = invocation["rows"]
    assert row["content"] == text
    assert row["type"] == node_type
    assert "agent" in row["prov_source"]
    assert row["audience"] == "team"
    assert "DO_NOT_SUBMIT_CORPUS_SENTINEL" not in json.dumps(invocation)
    assert not (env["repo"] / "SHOULD_NEVER_RUN").exists()
    assert env["local"].conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
    serialized = json.dumps(result).lower()
    assert '"stage": "ratified"' not in serialized
    assert '"ratified": true' not in serialized


def test_identical_retry_reuses_content_address_and_row_identity(submission_env):
    env = submission_env
    submit(env["repo"], "Retry observation", binary=env["binary"])
    first, = invocations(env)
    artifact = Path(first["artifact"])
    before = artifact.read_bytes()
    submit(env["repo"], "Retry observation", binary=env["binary"])
    first, second = invocations(env)
    assert first["artifact"] == second["artifact"]
    assert first["rows"] == second["rows"]
    assert artifact.read_bytes() == before
    submit(env["repo"], "Different observation", binary=env["binary"])
    third = invocations(env)[2]
    assert third["artifact"] != first["artifact"]
    assert third["rows"][0]["id"] != first["rows"][0]["id"]


@pytest.mark.parametrize("text,node_type", [
    ("", "concept"), (" \n\t", "concept"), ("ordinary text", "task"),
    ("ordinary text", "person"), ("x" * (16 * 1024 + 1), "concept"),
    ("é" * 8193, "concept"),
    ("api_key=sk-proj-" + "A" * 64, "concept"),
    ("-----BEGIN PRIVATE KEY-----\nsynthetic-secret\n-----END PRIVATE KEY-----", "concept"),
])
def test_invalid_or_sensitive_payload_never_invokes_native(submission_env, text, node_type):
    env = submission_env
    result = attempt(env["repo"], text, node_type, binary=env["binary"])
    refused(result)
    assert invocations(env) == []
    assert not [path for path in (env["graph"] / "kinbase-submissions").rglob("*") if path.is_file()]


def test_size_limit_is_utf8_bytes_and_accepts_boundary(submission_env):
    env = submission_env
    text = "é" * 8192
    result = submit(env["repo"], text, binary=env["binary"])
    assert "native-fixture-receipt" in json.dumps(result)
    assert invocations(env)[0]["rows"][0]["content"] == text


@pytest.mark.parametrize("tamper", ["content", "symlink", "hardlink"])
def test_existing_tampered_or_linked_artifact_refuses_without_overwrite(submission_env, tmp_path, tamper):
    env = submission_env
    text = "Immutable submission observation"
    submit(env["repo"], text, binary=env["binary"])
    artifact = Path(invocations(env)[0]["artifact"])
    if tamper == "content":
        with sqlite3.connect(artifact) as conn:
            conn.execute("UPDATE nodes SET content='TAMPERED_OBSERVATION'")
    elif tamper == "symlink":
        external = tmp_path / "external.sqlite"
        shutil.copy2(artifact, external)
        artifact.unlink()
        artifact.symlink_to(external)
    else:
        (tmp_path / "hardlinked.sqlite").hardlink_to(artifact)
    before = artifact.read_bytes()
    refused(attempt(env["repo"], text, binary=env["binary"]))
    assert len(invocations(env)) == 1
    assert artifact.read_bytes() == before


def test_typed_native_refusal_is_preserved(submission_env):
    env = submission_env
    native = {"error": {"code": "REPO_UNCERTIFIED", "message": "fixture repository refusal",
                        "remediation": "Certify this repository", "retryable": False}}
    env["receipt"].write_text(json.dumps(native))
    result = submit(env["repo"], "Observation awaiting certification", binary=env["binary"])
    assert result["error"] == native["error"]
    assert result.get("ok") is not True


def test_native_ingest_admission_receipt_is_preserved_without_contradiction(submission_env):
    env = submission_env
    native = {"ok": True, "stage": "ingested", "ingested": 1,
              "derived_facts": [{"fact_id": "native-derived-fixture", "admitted": True}]}
    env["receipt"].write_text(json.dumps(native))
    result = submit(env["repo"], "Native ingest may derive admitted facts", binary=env["binary"])

    def dictionaries(value):
        if isinstance(value, dict):
            yield value
            for child in value.values():
                yield from dictionaries(child)
        elif isinstance(value, list):
            for child in value:
                yield from dictionaries(child)

    assert any(value.get("derived_facts") == native["derived_facts"]
               for value in dictionaries(result)), result
    assert result.get("admission") != "not_performed", result
    assert result.get("admitted") is not False, result
    assert result.get("ratified") is not True, result
    invocation, = invocations(env)
    assert invocation["argv"][:2] == ["ingest", "kindex"]


def test_timeout_preserves_uncertain_submission_outcome(submission_env, monkeypatch):
    env = submission_env
    real_run = subprocess.run
    attempts = []

    def timeout_after_possible_write(argv, *args, **kwargs):
        if isinstance(argv, (list, tuple)) and str(argv[0]) == env["binary"]:
            attempts.append(argv)
            assert 0 < kwargs.get("timeout", 0) <= 120
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", timeout_after_possible_write)
    result = attempt(env["repo"], "Observation with uncertain receipt", binary=env["binary"])
    refused(result)
    assert len(attempts) == 1, "A timeout must not trigger an automatic blind resubmission"
    output = json.dumps(result).lower()
    assert any(word in output for word in ("unknown", "uncertain", "may have", "indeterminate")), output
    assert "no side effect" not in output
    assert '"submitted": false' not in output


@pytest.mark.parametrize("surface", ["full", "lite"])
def test_mcp_submission_has_no_caller_authority_controls(submission_env, monkeypatch, surface):
    pytest.importorskip("mcp")
    import kindex.kinbase as bridge
    import kindex.mcp_server as full

    env = submission_env
    captured = []

    def backend(repo, text, node_type="concept", *, binary="kinbase"):
        captured.append((Path(repo).resolve(), text, node_type, binary))
        return {"ok": True, "stage": "ingested", "receipt_id": "mcp-submission-receipt"}

    monkeypatch.setattr(bridge, "submit_observation", backend)
    if surface == "lite":
        server = importlib.import_module("kindex.mcp_lite").create_server(env["repo"])
    else:
        monkeypatch.setattr(full, "_store", env["local"])
        monkeypatch.setattr(full, "_config", env["local"].config)
        server = full.mcp
    tool = next(tool for tool in asyncio.run(server.list_tools()) if tool.name == "kinbase_submit")
    assert set(tool.inputSchema["properties"]) == {"repo", "text", "node_type"}
    result = call(server, "kinbase_submit", repo=str(env["repo"]), text="Explicit MCP observation",
                  node_type="question")
    assert "mcp-submission-receipt" in json.dumps(result)
    assert captured == [(env["repo"].resolve(), "Explicit MCP observation", "question", "kinbase")]
    if surface == "lite":
        refused(call(server, "kinbase_submit", repo=str(env["home"]), text="Foreign observation"))
        assert len(captured) == 1


def cli(env, text, *options):
    process_env = dict(os.environ)
    source = Path(__file__).resolve().parents[1] / "src"
    process_env["PYTHONPATH"] = str(source)
    return subprocess.run(
        [sys.executable, "-m", "kindex.cli", "kinbase", "submit", text,
         "--repo", str(env["repo"]), "--binary", env["binary"], "--json", *options],
        cwd=env["repo"], env=process_env, capture_output=True, text=True, timeout=20)


def test_cli_submits_explicit_text_and_prints_native_receipt(submission_env):
    env = submission_env
    result = cli(env, "Explicit CLI observation", "--type", "decision")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert "native-fixture-receipt" in json.dumps(payload)
    invocation, = invocations(env)
    row, = invocation["rows"]
    assert row["content"] == "Explicit CLI observation"
    assert row["type"] == "decision"
    assert "agent" in row["prov_source"]
    assert row["audience"] == "team"
    assert env["local"].conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 1
    assert not (env["home"] / ".config/kindex").exists()


def test_cli_native_refusal_has_nonzero_exit_and_preserved_code(submission_env):
    env = submission_env
    native = {"error": {"code": "REPO_UNCERTIFIED", "message": "fixture refusal",
                        "remediation": "Certify repository", "retryable": False}}
    env["receipt"].write_text(json.dumps(native))
    result = cli(env, "CLI observation with refused receipt")
    assert result.returncode != 0
    payload = json.loads(result.stdout)
    assert payload["error"] == native["error"]
    assert payload.get("ok") is not True


def test_cli_rejects_unsupported_type_before_native_invocation(submission_env):
    env = submission_env
    result = cli(env, "Cannot submit this task", "--type", "task")
    assert result.returncode != 0
    assert invocations(env) == []
