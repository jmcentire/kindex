"""Run batch0 acceptance suite -- S2: memory failure degrades, never crashes.

Tester-lane authored, blind to the implementation; the oracle is the signed
run artifacts only. Citation shorthand:

  spec@1f0cdd71  = product-specification.md
      sha256 1f0cdd7134ad671d3795252cbaedde858ae82dd0ef4d5a65d7d09a048bca617b
  arch@59540239  = architecture.md
      sha256 595402395f445c619122ede5435a303b31e8ed9df5d4c1f96e2db73a6ec4f1e6
  strat@e58068c2 = testing-strategy.md
      sha256 e58068c20913db342c6e24af5896f5f24d429ecc699103a8a787fda0372eece5

Markers per strat@e58068c2 T0.2: ``red_now`` = asserts FIXED behavior,
expected to fail on unfixed base 8c5cc925648c; unmarked = green-now guard.
Determinism per T0.4: no network, no provider keys, tmp dirs only; the DB
corruption fixture is garbage bytes (not chmod) so it behaves identically
under any uid. Conventions replicated from the read-only tests/test_doctor.py
and tests/test_hooks.py.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from kindex.config import Config
from kindex.store import Store

REQUIRED_EVENT_KEYS = {"ts", "cmd", "profile", "profile_source",
                       "error_class", "msg"}  # spec@1f0cdd71 R2.2


def _env(tmp_path):
    """Hermetic subprocess env (strat@e58068c2 T0.4)."""
    env = dict(os.environ)
    for k in list(env):
        if k.startswith("KIN_"):
            env.pop(k)
    for k in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
              "GOOGLE_API_KEY", "VOYAGE_API_KEY"):
        env.pop(k, None)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env["HOME"] = str(home)
    return env


def _run(tmp_path, *args, data_dir=None, input_text=None):
    cmd = [sys.executable, "-m", "kindex.cli", *args]
    if data_dir is not None:
        cmd.extend(["--data-dir", str(data_dir)])
    return subprocess.run(cmd, input=input_text, capture_output=True,
                          text=True, timeout=120, env=_env(tmp_path),
                          cwd=str(tmp_path))


def _make_corrupt_db(data_dir: Path) -> Path:
    """Create a healthy DB, then overwrite it with non-SQLite bytes.

    The DB filename is discovered from Store itself (declared surface:
    Store construction against a tmp path, per existing test fixtures) so
    the fixture does not hardcode implementation file names.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg = Config(data_dir=str(data_dir))
    s = Store(cfg)
    db = Path(s.db_path)
    s.close()
    db.write_bytes(b"this is not a sqlite database. " * 64)
    return db


def _ledger_events(path: Path):
    lines = [l for l in path.read_text().splitlines() if l.strip()]
    return [json.loads(l) for l in lines]


# -- R2.1: hook commands degrade to one line + exit 0 ---------------------


@pytest.mark.red_now
def test_r2_1_prime_degrades_to_single_line_exit_zero(tmp_path):
    """spec@1f0cdd71 R2.1; strat@e58068c2 oracle row R2.1 (red_now).

    `kin prime --for hook` over a corrupted DB: exit 0 and a single
    `# kindex degraded: <ErrClass> -- session starting without memory
    context` line on stdout. Nothing else.

    Red if: main() again catches only ProfileMismatchError -- the corrupt DB
    tracebacks with nonzero exit on the hook surface (the S2 defect).
    """
    data_dir = tmp_path / "data"
    _make_corrupt_db(data_dir)
    r = _run(tmp_path, "prime", "--for", "hook", data_dir=data_dir,
             input_text="{}")
    assert r.returncode == 0, r.stderr
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert len(lines) == 1, f"expected exactly one degraded line, got {lines!r}"
    assert lines[0].startswith("# kindex degraded:"), lines[0]
    assert "session starting without memory context" in lines[0]


@pytest.mark.red_now
def test_r2_1_compact_hook_degrades_silently(tmp_path):
    """spec@1f0cdd71 R2.1 (compact-hook degraded shape: nothing);
    strat@e58068c2 oracle row R2.1 (red_now).

    compact-hook over a corrupted DB with a valid envelope: exit 0, empty
    stdout (a Stop hook has no user-visible surface to degrade onto).

    Red if: the corrupt DB tracebacks (nonzero exit) or the degraded path
    prints noise into the hook stream.
    """
    data_dir = tmp_path / "data"
    _make_corrupt_db(data_dir)
    transcript = tmp_path / "t.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "message": {"role": "assistant",
                    "content": [{"type": "text",
                                 "text": "We learned that alpha maps to "
                                         "beta in the gamma pipeline."}]},
    }) + "\n")
    envelope = json.dumps({
        "hook_event_name": "Stop",
        "transcript_path": str(transcript),
        "session_id": "b0b0c0de-0000-0000-0000-000000000000",
        "cwd": str(tmp_path),
    })
    r = _run(tmp_path, "compact-hook", data_dir=data_dir,
             input_text=envelope)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_r2_1_prompt_check_degrades_empty_fail_open(tmp_path):
    """spec@1f0cdd71 R2.1 (guard-type hooks: empty fail-open output);
    strat@e58068c2 oracle row R2.1 (green-now: observed at the 0.29.0
    authoring baseline that prompt-check already exits 0 with empty stdout
    on this corrupt-DB trigger, so this guards identical behavior; the
    red_now burden for R2.1 is carried by the prime and compact-hook
    tests. Validator re-verifies the classification at base per T0.5).

    prompt-check over a corrupted DB: exit 0, empty stdout -- the guard
    fails open rather than blocking the user's turn, before and after the
    fix.

    Red if: the S2 rework makes guard-type hooks traceback (nonzero exit
    blocks the turn) or print degraded noise into the prompt stream.
    """
    data_dir = tmp_path / "data"
    _make_corrupt_db(data_dir)
    r = _run(tmp_path, "prompt-check", data_dir=data_dir,
             input_text=json.dumps({"session_id": "chat-1",
                                    "prompt": "status"}))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == ""


def test_r2_1_non_hook_command_keeps_real_error(tmp_path):
    """spec@1f0cdd71 R2.1 (non-hook commands keep full tracebacks);
    strat@e58068c2 oracle row R2.1 green-now; arch@59540239 surface note
    ("masks real errors if over-broad").

    `kin search` over the same corrupted DB: NONZERO exit and a real
    error/traceback on stderr -- hooks fail open, humans see errors. This is
    the anti-Goodhart guard against an over-broad catch-all.

    Red if: the hook catch-all is applied to human commands too (exit 0 /
    silent degradation on `kin search`).
    """
    data_dir = tmp_path / "data"
    _make_corrupt_db(data_dir)
    r = _run(tmp_path, "search", "anything", data_dir=data_dir)
    assert r.returncode != 0, (
        "non-hook commands must not silently degrade")
    assert "Traceback" in r.stderr or "Error" in r.stderr or r.stderr.strip()


# -- R2.2: the degraded ledger ---------------------------------------------


@pytest.mark.red_now
def test_r2_2_degraded_event_appended_with_required_keys(tmp_path):
    """spec@1f0cdd71 R2.2; strat@e58068c2 oracle row R2.2 (red_now).

    Each degraded hook failure appends ONE JSON line to degraded.jsonl in
    the base data dir carrying at least {ts, cmd, profile, profile_source,
    error_class, msg<=200}; a second failure appends a second line.
    (No profile is configured here, so the base pre-profile dir is the
    --data-dir itself; the profile-indirection case has its own test.)

    Red if: no ledger is written on hook failure (the S2 silent-failure
    defect), a required key is missing, msg is untruncated (>200), or the
    second failure overwrites instead of appending.
    """
    data_dir = tmp_path / "data"
    _make_corrupt_db(data_dir)
    r1 = _run(tmp_path, "prime", "--for", "hook", data_dir=data_dir,
              input_text="{}")
    assert r1.returncode == 0, r1.stderr
    ledger = data_dir / "degraded.jsonl"
    assert ledger.exists(), "degraded event must land in the base data dir"
    events = _ledger_events(ledger)
    assert len(events) == 1
    evt = events[0]
    missing = REQUIRED_EVENT_KEYS - set(evt)
    assert not missing, f"degraded event missing required keys: {missing}"
    assert "prime" in str(evt["cmd"])
    assert str(evt["error_class"]).strip()
    assert len(str(evt["msg"])) <= 200

    r2 = _run(tmp_path, "prime", "--for", "hook", data_dir=data_dir,
              input_text="{}")
    assert r2.returncode == 0, r2.stderr
    assert len(_ledger_events(ledger)) == 2, "second failure must append"


@pytest.mark.red_now
def test_r2_2_size_cap_rewrite_keeps_tail_and_new_event(tmp_path):
    """spec@1f0cdd71 R2.2 (size cap: over 1 MB rewrite keeping the last 200
    lines); strat@e58068c2 oracle row R2.2 size-cap clause (red_now).

    Seed a >1MB ledger (6000 seeded lines), trigger one degraded append:
    the file shrinks to the last 200 lines plus the new event (200 or 201
    total -- both readings of "last 200 lines + the new event" accepted; the
    ambiguity is reported upward in the handover, not resolved here), all
    lines well-formed, the oldest seeded lines evicted, the newest seeded
    line retained, and the new event present.

    Red if: no cap (file stays >6000 lines), the cap loses the new append,
    the eviction drops the tail instead of the head, or the rewrite tears a
    line (any line fails json.loads).
    """
    data_dir = tmp_path / "data"
    _make_corrupt_db(data_dir)
    ledger = data_dir / "degraded.jsonl"
    seed_lines = [
        json.dumps({"ts": "2026-08-01T00:00:00", "cmd": "seed",
                    "profile": "", "profile_source": "seed",
                    "error_class": "SeedError", "msg": "x" * 140, "i": i})
        for i in range(6000)
    ]
    ledger.write_text("\n".join(seed_lines) + "\n")
    assert ledger.stat().st_size > 1_000_000, "fixture must exceed the 1MB cap"

    r = _run(tmp_path, "prime", "--for", "hook", data_dir=data_dir,
             input_text="{}")
    assert r.returncode == 0, r.stderr
    events = _ledger_events(ledger)  # raises on any torn line -> red
    assert len(events) in (200, 201), (
        f"cap must keep last-200 (+ new event); got {len(events)} lines")
    assert any("prime" in str(e.get("cmd")) for e in events), (
        "the size-cap rewrite lost the concurrent/new append")
    seed_idx = [e["i"] for e in events if e.get("cmd") == "seed"]
    assert 0 not in seed_idx and 3000 not in seed_idx, (
        "cap must evict the oldest lines")
    assert 5999 in seed_idx, "cap must keep the newest prior lines (tail)"
    assert ledger.stat().st_size < 1_000_000


@pytest.mark.red_now
def test_r2_2_ledger_lands_in_base_pre_profile_dir_creating_it(tmp_path):
    """spec@1f0cdd71 R2.2 (BASE pre-profile data dir; pure file append that
    works when SQLite broke); strat@e58068c2 edge-case list ("degraded-ledger
    append when the base data dir does not yet exist") (red_now).

    With a configured profile whose data dir holds a corrupt DB, and a HOME
    whose base ~/.kindex does NOT yet exist: the hook degrades (exit 0,
    degraded line) and the event lands in the BASE pre-profile dir --
    created on demand by the append -- with the active profile recorded in
    the event.

    Red if: no ledger is written, the append crashes because the base dir
    is missing (FileNotFoundError), or the event is written to the profile
    data dir instead of the base pre-profile dir.
    """
    env = _env(tmp_path)
    home = Path(env["HOME"])
    profile_data = tmp_path / "profile-data"
    _make_corrupt_db(profile_data)
    cfg_dir = home / ".config" / "kindex"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "kin.yaml").write_text(
        "profiles:\n"
        "  p1:\n"
        f"    data_dir: {profile_data}\n"
        "default_profile: p1\n")
    base = home / ".kindex"
    assert not base.exists()

    r = subprocess.run(
        [sys.executable, "-m", "kindex.cli", "prime", "--for", "hook"],
        input="{}", capture_output=True, text=True, timeout=120,
        env=env, cwd=str(tmp_path))
    assert r.returncode == 0, r.stderr
    assert "# kindex degraded:" in r.stdout
    ledger = base / "degraded.jsonl"
    assert ledger.exists(), (
        "degraded event must land in the BASE (pre-profile) data dir, "
        "created on demand")
    evt = _ledger_events(ledger)[-1]
    assert evt.get("profile") == "p1"
    missing = REQUIRED_EVENT_KEYS - set(evt)
    assert not missing, f"degraded event missing required keys: {missing}"


@pytest.mark.red_now
def test_r2_1_ledger_unwritable_still_exits_zero(tmp_path):
    """spec@1f0cdd71 R2.1 + I4 (failures in the new paths never crash a
    hook); arch@59540239 degraded-ledger surface ("silent-failure regression
    if append itself throws" -- append is best-effort) (red_now).

    Point --data-dir below a path component that is a FILE: the data dir is
    unbuildable, so both the DB open and the ledger append itself must fail
    -- and the hook must STILL exit 0 with its one degraded line.

    Red if: the hook catch-all is missing (base: traceback, nonzero exit),
    or the ledger append's own exception propagates and kills the hook.
    """
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    data_dir = blocker / "data"  # unbuildable path
    r = _run(tmp_path, "prime", "--for", "hook", data_dir=data_dir,
             input_text="{}")
    assert r.returncode == 0, r.stderr
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    assert len(lines) == 1 and lines[0].startswith("# kindex degraded:"), (
        f"expected the single degraded line even with an unwritable ledger, "
        f"got {r.stdout!r}")


# -- R2.3: status / doctor surface the degraded count ---------------------


@pytest.mark.red_now
def test_r2_3_status_and_doctor_surface_degraded_events(tmp_path):
    """spec@1f0cdd71 R2.3; strat@e58068c2 oracle row R2.3 (red_now).

    After one real degraded event (produced end-to-end via a corrupt-DB
    prime, then the DB healed), `kin status` shows the 7-day degraded count
    with the most recent event's cmd + error class, and `kin doctor` treats
    count > 0 as a warning. The cmd/error-class assertions are relational:
    the spec requires status to render the most recent EVENT, so the
    expected tokens are read from the ledger the system itself wrote.

    Red if: status/doctor ignore degraded.jsonl entirely (base behavior),
    or doctor does not warn on a recent degraded event.
    """
    data_dir = tmp_path / "data"
    db = _make_corrupt_db(data_dir)
    r = _run(tmp_path, "prime", "--for", "hook", data_dir=data_dir,
             input_text="{}")
    assert r.returncode == 0, r.stderr
    ledger = data_dir / "degraded.jsonl"
    assert ledger.exists(), "precondition: R2.2 append must have happened"
    evt = _ledger_events(ledger)[-1]

    # Heal the DB so status/doctor exercise their reporting path, not the
    # degradation path.
    db.unlink()
    s = Store(Config(data_dir=str(data_dir)))
    s.close()

    r_status = _run(tmp_path, "status", data_dir=data_dir)
    assert r_status.returncode == 0, r_status.stderr
    out = r_status.stdout.lower()
    assert "degraded" in out, "status must surface the degraded count"
    assert "prime" in out, "status must show the most recent event's cmd"
    assert str(evt["error_class"]).lower() in out, (
        "status must show the most recent event's error class")

    r_doc = _run(tmp_path, "doctor", "--json", data_dir=data_dir)
    assert r_doc.returncode == 0, r_doc.stderr
    doc = json.loads(r_doc.stdout)
    assert any("degraded" in str(w).lower()
               for w in doc.get("warnings", []) + doc.get("issues", [])), (
        "doctor must warn when the 7-day degraded count is > 0")


def test_r2_3_absent_ledger_means_no_warning(tmp_path):
    """spec@1f0cdd71 R2.3 (absent file = zero events, no warning);
    strat@e58068c2 oracle row R2.3 green-now.

    Healthy store, no degraded.jsonl: doctor emits no degraded warning and
    both commands exit 0. Green-now: base never mentions degraded either.

    Red if: the degraded reporting treats a missing ledger as an error or
    warns unconditionally.
    """
    data_dir = tmp_path / "data"
    s = Store(Config(data_dir=str(data_dir)))
    s.add_node("Healthy concept", content="all good here")
    s.close()
    r_doc = _run(tmp_path, "doctor", "--json", data_dir=data_dir)
    assert r_doc.returncode == 0, r_doc.stderr
    doc = json.loads(r_doc.stdout)
    assert not any("degraded" in str(w).lower()
                   for w in doc.get("warnings", []) + doc.get("issues", []))
    r_status = _run(tmp_path, "status", data_dir=data_dir)
    assert r_status.returncode == 0, r_status.stderr


# -- R2.4: poisoned section yields partial prime, not empty ---------------


@pytest.mark.red_now
def test_r2_4_poisoned_section_yields_partial_prime(tmp_path):
    """spec@1f0cdd71 R2.4 (the two unshielded core calls in prime_context
    are individually shielded; partial > empty); strat@e58068c2 oracle row
    R2.4 (red_now).

    Healthy DB with one poisoned OPERATIONAL node: a constraint whose
    ``extra`` column is rewritten via SQL to invalid JSON (fixture
    construction per T0.4 -- fixtures are ours to construct). The poisoned
    node shares no tokens with the topic, so the search path never touches
    it; only the operational-summary pull does. prime_context must still
    return the search section (partial context), never raise.

    Red if: the operational-summary call is unshielded -- the poisoned row
    raises out of prime_context and the whole prime dies (the S2/R2.4
    defect).
    """
    from kindex.hooks import prime_context

    cfg = Config(data_dir=str(tmp_path / "data"))
    store = Store(cfg)
    try:
        store.add_node("Alphaprime resilience concept",
                       content="alphaprime survives partial priming",
                       node_type="concept", node_id="ok1")
        store.add_node("Zzquarantine rule", node_type="constraint",
                       node_id="poison1",
                       extra={"trigger": "pre-deploy", "action": "block"})
        store.conn.execute(
            "UPDATE nodes SET extra = '{\"trigger\": \"pre-deploy\", "
            "\"action\": BROKEN' WHERE id = 'poison1'")
        store.conn.commit()
        out = prime_context(store, topic="alphaprime", max_tokens=1500)
    finally:
        store.close()
    assert "Kindex Context" in out
    assert "Alphaprime resilience concept" in out, (
        "healthy sections must still render when one section is poisoned")


# -- R2.5: MCP store failure becomes a typed tool result ------------------


@pytest.mark.skip(
    reason="DESCOPED for 0.30.0 by Validator ruling (run batch0, kindex node "
           "bfed298fcb46). This test cannot isolate the MCP module's config "
           "resolution in-process: module-level caching plus HOME-time resolution "
           "mean it reads the developer's LIVE graph when one exists (observed "
           "twice during judge runs). R2.5 itself IS verified — receipted "
           "fresh-interpreter probe R-20260811T180533Z-24442 returns the typed "
           "error on a corrupt store. Remediation: rewrite with subprocess "
           "isolation (spawn python with HOME set at process start)."
)
@pytest.mark.red_now
def test_r2_5_mcp_store_open_failure_returns_typed_error(tmp_path,
                                                         monkeypatch):
    """spec@1f0cdd71 R2.5; strat@e58068c2 oracle row R2.5 (red_now);
    arch@59540239 MCP boundary component.

    A Store open/init failure on the MCP surface yields the literal
    tool-result prefix ``Error: memory unavailable (`` plus a
    degraded.jsonl event in the base data dir, instead of an unhandled
    exception.

    Fixture (Validator-verified recipe; re-opened fixture-only
    2026-08-11): the MCP module resolves its own config, so patched
    module singletons do not govern store construction. The broken store
    is therefore planted where the module's own resolution will find it:
    HOME is overridden to a tmp dir and the corrupt DB bytes are placed
    at HOME/.kindex/<dbname> BEFORE the module is imported; the module is
    imported fresh under that HOME and evicted from sys.modules again
    afterwards, so no cached config from other tests can leak a live or
    healthy store into this test, and no live graph can ever be touched.

    Reachability: the corrupt DB at the module's own resolution path
    guarantees the store accessor's failure branch is entered; the
    asserted ledger side effect proves the degraded path (not an earlier
    gate) produced the string. The base build raises out of search()
    here, which is the red form of this test.

    Red if: the store getter re-raises (unhandled exception out of the
    tool function), the literal prefix changes, or no degraded event is
    written.
    """
    home = tmp_path / "home"
    base = home / ".kindex"
    base.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    for k in list(os.environ):
        if k.startswith("KIN_"):
            monkeypatch.delenv(k, raising=False)

    # Discover the DB filename from a healthy throwaway store (declared
    # surface), then plant corrupt bytes at the module's resolution path.
    probe = Store(Config(data_dir=str(tmp_path / "probe")))
    db_name = Path(probe.db_path).name
    probe.close()
    (base / db_name).write_bytes(b"this is not a sqlite database. " * 64)

    sys.modules.pop("kindex.mcp_server", None)
    try:
        try:
            import kindex.mcp_server as mcp_mod
        except (ImportError, SystemExit):
            pytest.skip("kindex.mcp_server unavailable (mcp extra missing "
                        "or incompatible) -- MCP coverage requires a "
                        "working [mcp] extra")
        result = mcp_mod.search("anything")
    finally:
        sys.modules.pop("kindex.mcp_server", None)

    assert isinstance(result, str)
    assert result.startswith("Error: memory unavailable ("), result
    ledger = base / "degraded.jsonl"
    assert ledger.exists(), "MCP store failure must also write a degraded event"
    evt = _ledger_events(ledger)[-1]
    assert str(evt.get("error_class", "")).strip()


# -- R2.6: concurrent appends are line-atomic -----------------------------


@pytest.mark.red_now
def test_r2_6_concurrent_degraded_appends_are_line_atomic(tmp_path):
    """spec@1f0cdd71 R2.6 (single-write O_APPEND line appends; torn lines
    are not acceptable); strat@e58068c2 oracle row R2.6 (red_now).

    100 concurrently launched hook processes all fail against the same
    corrupt DB (well under the 1MB cap, so no rewrite interplay): the
    ledger must contain exactly 100 well-formed JSON lines -- zero torn or
    interleaved lines -- and every process must exit 0.

    Red if: the base build has no ledger at all (0 lines), appends are
    buffered/multi-write (interleaved fragments fail json.loads), or events
    are lost (fewer than 100 lines).
    """
    data_dir = tmp_path / "data"
    _make_corrupt_db(data_dir)
    env = _env(tmp_path)
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "kindex.cli", "prime", "--for", "hook",
             "--data-dir", str(data_dir)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, env=env, cwd=str(tmp_path))
        for _ in range(100)
    ]
    rcs = [p.wait(timeout=300) for p in procs]
    assert all(rc == 0 for rc in rcs), f"nonzero hook exits: {rcs}"

    ledger = data_dir / "degraded.jsonl"
    assert ledger.exists()
    raw_lines = ledger.read_text().splitlines()
    lines = [l for l in raw_lines if l.strip()]
    events = [json.loads(l) for l in lines]  # torn line -> ValueError -> red
    assert len(events) == 100, (
        f"expected exactly 100 events, got {len(events)}")
    for evt in events:
        assert str(evt.get("error_class", "")).strip()
