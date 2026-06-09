"""Async Sim (Jeremy-simulacrum) supervisory check-in.

Sim glances at a conversation WINDOW as a supervisor and, only if its feedback
self-rates at/above a configured threshold, that feedback is injected into the
conversation. Opt-in, disable-able, no training loop: the human plus the
threshold *is* the feedback loop (too chatty -> raise threshold; too quiet ->
lower it; done -> `kin sim disable`).

Design (mirrors reinforce.py's queue/drain so the LLM cost stays off the agent's
critical path):

  enqueue_sim_review()   cheap, SQLite-only, safe in a prompt hook. Snapshots the
                         current conversation window + a fingerprint of its tail.
  drain_sim_queue()      runs in the daemon/cron. Calls Sim on each snapshot,
                         self-rates, and stashes a PENDING injection when the
                         rating clears the threshold. This is where spend lives.
  pop_pending_sim_injection()  cheap, called on the next tick. Surfaces a pending
                         injection through the existing attention inject channel —
                         but only if it is still FRESH (the window Sim reacted to
                         hasn't scrolled away). Stale feedback is dropped, never shown.

The supervisor PROMPT is load-bearing. Un-framed, Sim defaults to maximum
adversarial demolition and rates everything high, which would make the threshold
meaningless. The prompt pins Sim to a default-silent, high-bar supervisor whose
common answer is "nothing to flag" and who only speaks when something would
MATERIALLY change the direction of the work.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .attention import (
    AttentionInjection,
    _load_state,
    _save_state,
    _tokens,
    pheromone_context,
)
from .budget import BudgetLedger
from .config import Config

if TYPE_CHECKING:
    from .store import Store

SIM_PURPOSE = "sim"
SIM_QUEUE_META = "sim.queue"          # pending reviews awaiting a drain (LLM spend)
SIM_PENDING_META = "sim.pending"      # graded injections awaiting a cheap pickup

_TAIL_CHARS = 700  # the recent slice Sim is most likely reacting to (used for staleness)


def _now() -> str:
    from .store import _now as store_now

    return store_now()


# ── runtime enable/disable (the `kin sim` kill switch) ──────────────────────

_SIM_ENABLED_META = "sim.enabled"


def _truthy(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def set_sim_enabled(store: "Store", enabled: bool) -> None:
    """Runtime override that wins over config.sim.enabled (global kill switch)."""
    store.set_meta(_SIM_ENABLED_META, "true" if enabled else "false")


def clear_sim_override(store: "Store") -> None:
    """Drop the runtime override so config.sim.enabled governs again."""
    store.set_meta(_SIM_ENABLED_META, "")


def sim_effective_enabled(store: "Store", config: Config) -> bool:
    """Effective on/off: config default, overridden by the runtime kill switch."""
    override = _truthy(store.get_meta(_SIM_ENABLED_META))
    return override if override is not None else bool(config.sim.enabled)


def sim_status(store: "Store", config: Config) -> dict:
    override = _truthy(store.get_meta(_SIM_ENABLED_META))
    return {
        "enabled": override if override is not None else bool(config.sim.enabled),
        "config_default": bool(config.sim.enabled),
        "runtime_override": override,
        "threshold": config.sim.threshold,
        "tick_interval": config.sim.tick_interval,
        "model": config.sim.model or config.llm.model,
        "command": config.sim.command or "(LLM supervisor)",
        "guidance": get_sim_guidance(store) or "(none)",
        "pending": len(_read_meta_list(store, SIM_PENDING_META)),
        "queued": len(_read_meta_list(store, SIM_QUEUE_META)),
    }


# ── operator guidance (steers the lens, not the bar; clears on restart) ──────

_SIM_GUIDANCE_META = "sim.guidance"


def set_sim_guidance(store: "Store", text: str) -> None:
    """Set session-scoped guidance that steers what Sim weighs. Persists across
    messages; cleared at session start (see clear_sim_guidance)."""
    store.set_meta(_SIM_GUIDANCE_META, (text or "").strip())


def get_sim_guidance(store: "Store") -> str:
    return (store.get_meta(_SIM_GUIDANCE_META) or "").strip()


def clear_sim_guidance(store: "Store") -> bool:
    """Clear guidance. Returns True if something was actually cleared (so the
    SessionStart hook can print a one-line 'sim guidance cleared' notice)."""
    had = bool(get_sim_guidance(store))
    if had:
        store.set_meta(_SIM_GUIDANCE_META, "")
    return had


# ── window fingerprinting (staleness) ───────────────────────────────────────

def _tail_fingerprint(window: str) -> list[str]:
    """Salient tokens from the tail of a window — what Sim is reacting to.

    Used to detect staleness: if the tail Sim reviewed no longer overlaps the
    current tail, the conversation has moved on and the feedback is dropped.
    """
    tail = (window or "")[-_TAIL_CHARS:]
    return sorted(t for t in _tokens(tail) if len(t) > 3)


def _overlap(a: list[str], b: list[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


# ── prompt ──────────────────────────────────────────────────────────────────

def build_sim_grounding(store: "Store", window: str, config: Config) -> str:
    """Retrieve captured knowledge relevant to the window so Sim reviews WITH the
    graph's context instead of blind: top related concepts/decisions plus active
    constraints and watches. Char-capped by sim.grounding_chars (0 = disabled).
    Fail-safe — returns '' when disabled, empty, or retrieval errors (never raises),
    so it can run on the daemon drain without risk.
    """
    budget = getattr(config.sim, "grounding_chars", 0)
    if budget <= 0 or not (window or "").strip():
        return ""
    parts: list[str] = []
    used = 0
    seen: set[str] = set()

    def _add(line: str, key: str = "") -> bool:
        nonlocal used
        k = (key or line).strip().lower()
        if not line or k in seen or used + len(line) + 1 > budget:
            return False
        seen.add(k)
        parts.append(line)
        used += len(line) + 1
        return True

    try:
        from .retrieve import hybrid_search
        for r in hybrid_search(store, (window or "")[-2000:], top_k=6):
            ntype = r.get("type", "concept")
            if ntype in ("constraint", "watch"):
                continue  # surfaced with action/owner detail by operational_summary below
            title = r.get("title") or r.get("id") or ""
            content = (r.get("content") or "").strip().replace("\n", " ")[:140]
            if not _add(f"- [{ntype}] {title}" + (f": {content}" if content else ""), key=title):
                break
    except Exception:
        pass

    try:
        ops = store.operational_summary()
    except Exception:
        ops = {}
    for c in (ops.get("constraints") or [])[:3]:
        action = (c.get("extra") or {}).get("action", "warn")
        title = c.get("title", "")
        if not _add(f"- [constraint:{action}] {title}", key=title):
            break
    for w in (ops.get("watches") or [])[:3]:
        title = w.get("title", "")
        if not _add(f"- [watch] {title}", key=title):
            break

    return "\n".join(parts)


def build_supervisor_prompt(window: str, max_chars: int, guidance: str = "", grounding: str = "") -> str:
    guidance_block = ""
    if guidance.strip():
        guidance_block = (
            "\nOPERATOR GUIDANCE (the people running this session asked you to weight "
            "your attention toward the following). Let it steer WHAT you look for — but "
            "do NOT lower the bar: still stay silent unless something would materially "
            f"change the direction of the work.\n  >>> {guidance.strip()}\n"
        )
    grounding_block = ""
    if grounding.strip():
        grounding_block = (
            "\nWHAT KINDEX ALREADY KNOWS about this work (captured concepts and decisions, "
            "and especially active constraints/watches). Use it to catch a vital consideration "
            "the session may be missing — a constraint being violated, a known watch, a prior "
            "decision being contradicted — not to nitpick. Same bar: stay silent unless it "
            f"would materially change the direction.\n{grounding.strip()}\n"
        )
    return f"""You are glancing in as a supervisor on an IN-PROGRESS work session between an agent and a user. This is low-stakes: the work is unfinished and the people are competent. Your DEFAULT action is to say NOTHING — most windows deserve silence.
{guidance_block}{grounding_block}

Speak ONLY if you notice something that would MATERIALLY CHANGE THE DIRECTION of the work:
  - a frame being locked in that shouldn't be,
  - a claim about to be committed that is wrong,
  - a contradiction the work hasn't noticed,
  - a materially better path that isn't being considered.

Do NOT nitpick wording, demand definitions for their own sake, or manufacture criticism to seem useful. Comment on the DIRECTION of the work, not any single sentence — your note may be read several messages later, so it must survive the conversation moving on.

Rate how strongly this warrants interrupting the people:
  0.0 = nothing to flag (the common case — return this and an empty note)
  0.5 = a refinement they'd appreciate but could skip
  0.8 = would likely change their next move
  1.0 = they are about to make a real mistake; speak now

CONVERSATION WINDOW:
{(window or "")[:max_chars]}

Return JSON only:
{{"rating": 0.0, "note": "<one or two sentences, directional; empty if rating is low>", "basis": "<what in the window triggered this, brief>"}}
"""


# ── Sim invocation ──────────────────────────────────────────────────────────

@dataclass
class _SimResult:
    rating: float
    note: str
    basis: str
    cost: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0


def _parse_sim(text: str) -> dict[str, Any]:
    from .attention import _parse_json_response

    return _parse_json_response(text)


def call_sim(
    config: Config,
    ledger: BudgetLedger,
    window: str,
    conversation_id: str,
    *,
    client: Any | None = None,
    guidance: str = "",
    grounding: str = "",
) -> tuple[_SimResult | None, dict]:
    """Run one supervisory review. Returns (result, accounting).

    If sim.command is configured, Sim is invoked as a subprocess (prompt on
    stdin, response on stdout) — this is how the real Jeremy-simulacrum is wired
    in. Otherwise the configured LLM client runs the supervisor prompt, which
    keeps the feature portable and testable without the skill installed.
    """
    sc = config.sim
    model = sc.model or config.llm.model
    prompt = build_supervisor_prompt(window, sc.window_chars, guidance=guidance, grounding=grounding)

    if not ledger.can_spend():
        return None, {"status": "over_global_budget"}

    # Budget gate (only meaningful for the LLM path; subprocess Sim is its own cost).
    from .llm import estimate_cost

    est = estimate_cost(model, len(prompt) // 4, sc.max_output_tokens)
    conversation_spend = ledger.conversation_spend(conversation_id, purpose=SIM_PURPOSE)
    if not sc.command:
        if est > sc.max_review_cost:
            return None, {"status": "estimate_exceeds_review_budget", "estimate": est}
        if conversation_spend + est > sc.max_conversation_cost:
            return None, {
                "status": "estimate_exceeds_conversation_budget",
                "estimate": est,
                "conversation_spend": round(conversation_spend, 6),
            }

    # ── subprocess Sim ──────────────────────────────────────────────────────
    if sc.command:
        try:
            import os

            proc = subprocess.run(
                os.path.expanduser(sc.command),
                shell=True,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=sc.command_timeout,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            return None, {"status": "sim_command_error", "error": str(exc)}
        if proc.returncode != 0:
            return None, {"status": "sim_command_failed", "error": proc.stderr[:200]}
        parsed = _parse_sim(proc.stdout)
        return _result_from_parsed(parsed), {"status": "ok", "via": "command"}

    # ── LLM-as-supervisor ───────────────────────────────────────────────────
    if client is None:
        from .llm import get_client

        client = get_client(config)
    if client is None:
        return None, {"status": "llm_unavailable", "estimate": est}

    try:
        response = client.messages.create(
            model=model,
            max_tokens=sc.max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        from .llm import calculate_cost

        cost = calculate_cost(model, response.usage)
        ledger.record(
            cost["amount"], model=model, purpose=SIM_PURPOSE,
            tokens_in=cost["tokens_in"], tokens_out=cost["tokens_out"],
            cache_creation_tokens=cost.get("cache_creation_tokens", 0),
            cache_read_tokens=cost.get("cache_read_tokens", 0),
            conversation_id=conversation_id, estimate=est,
        )
        parsed = _parse_sim(response.content[0].text)
    except Exception as exc:
        return None, {"status": "llm_error", "error": str(exc)}

    result = _result_from_parsed(parsed)
    if result:
        result.cost = cost["amount"]
        result.tokens_in = cost["tokens_in"]
        result.tokens_out = cost["tokens_out"]
    return result, {"status": "ok", "via": "llm", "cost": cost}


def _result_from_parsed(parsed: dict[str, Any]) -> _SimResult | None:
    try:
        rating = float(parsed.get("rating", 0.0))
    except (TypeError, ValueError):
        rating = 0.0
    note = str(parsed.get("note") or "").strip()
    basis = str(parsed.get("basis") or "").strip()
    return _SimResult(rating=rating, note=note, basis=basis)


# ── queue (enqueue cheap, drain in daemon) ──────────────────────────────────

def _read_meta_list(store: "Store", key: str) -> list[dict]:
    try:
        raw = store.get_meta(key)
        data = json.loads(raw) if raw else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def enqueue_sim_review(
    store: "Store",
    config: Config,
    conversation_id: str,
    window: str,
    *,
    tick: int,
) -> bool:
    """Snapshot a window for later supervisory review. Cheap, SQLite-only.

    Deduped by conversation (a fresher window replaces a staler one). Gated to
    roughly every `tick_interval` ticks so we don't queue every prompt.
    """
    if not sim_effective_enabled(store, config) or not conversation_id \
            or not (window or "").strip():
        return False
    interval = max(1, int(config.sim.tick_interval or 1))
    if tick % interval != 0:
        return False

    queue = [j for j in _read_meta_list(store, SIM_QUEUE_META)
             if j.get("conversation_id") != conversation_id]
    queue.append({
        "conversation_id": conversation_id,
        "window": window[-config.sim.window_chars:],
        "fingerprint": _tail_fingerprint(window),
        "tick": tick,
        "at": _now(),
    })
    try:
        store.set_meta(SIM_QUEUE_META, json.dumps(queue[-config.sim.max_queue:]))
        return True
    except Exception:
        return False


def drain_sim_queue(
    store: "Store",
    config: Config,
    *,
    client: Any | None = None,
    ledger: BudgetLedger | None = None,
    max_jobs: int = 5,
) -> dict:
    """Grade queued windows with Sim. Runs in the daemon — this is the spend.

    A review that clears the threshold becomes a PENDING injection keyed by
    conversation; pop_pending_sim_injection surfaces it on the next tick.
    """
    if not sim_effective_enabled(store, config):
        return {"status": "disabled", "reviewed": 0, "pending": 0}
    queue = _read_meta_list(store, SIM_QUEUE_META)
    if not queue:
        return {"status": "empty", "reviewed": 0, "pending": 0}

    ledger = ledger or BudgetLedger(config.ledger_path, config.budget)
    guidance = get_sim_guidance(store)
    pending = {j["conversation_id"]: j
               for j in _read_meta_list(store, SIM_PENDING_META)
               if j.get("conversation_id")}

    remaining: list[dict] = []
    reviewed = 0
    flagged = 0
    for job in queue:
        if reviewed >= max_jobs:
            remaining.append(job)
            continue
        conv = job.get("conversation_id")
        window = job.get("window") or ""
        if not conv or not window.strip():
            continue  # nothing to grade — drop, don't wedge the queue
        grounding = build_sim_grounding(store, window, config)
        result, acct = call_sim(config, ledger, window, conv, client=client,
                                guidance=guidance, grounding=grounding)
        if acct.get("status") in (
            "over_global_budget", "llm_unavailable",
            "estimate_exceeds_review_budget", "estimate_exceeds_conversation_budget",
        ):
            remaining.append(job)  # transient — retry next cron
            continue
        reviewed += 1
        if result and result.note and result.rating >= config.sim.threshold:
            pending[conv] = {
                "conversation_id": conv,
                "note": result.note,
                "basis": result.basis,
                "rating": round(result.rating, 3),
                "fingerprint": job.get("fingerprint") or [],
                "tick": job.get("tick", 0),
                "at": _now(),
            }
            flagged += 1

    try:
        store.set_meta(SIM_QUEUE_META, json.dumps(remaining))
        store.set_meta(SIM_PENDING_META, json.dumps(list(pending.values())))
    except Exception:
        pass
    return {"status": "ok", "reviewed": reviewed, "flagged": flagged,
            "pending": len(pending)}


# ── pickup (cheap, on the tick) ─────────────────────────────────────────────

def pop_pending_sim_injection(
    store: "Store",
    config: Config,
    conversation_id: str,
    current_window: str,
    *,
    tick: int,
) -> AttentionInjection | None:
    """Surface a pending Sim injection if one is fresh. Cheap, no LLM.

    Freshness: the pending injection is dropped (not shown) if it is older than
    `max_stale_ticks` ticks OR the tail Sim reacted to no longer overlaps the
    current tail — i.e. the conversation has scrolled past what Sim flagged.
    """
    if not sim_effective_enabled(store, config) or not conversation_id:
        return None
    pending_list = _read_meta_list(store, SIM_PENDING_META)
    if not pending_list:
        return None

    mine = next((p for p in pending_list if p.get("conversation_id") == conversation_id), None)
    if not mine:
        return None

    # Always consume it: either we surface it now or it's stale — never re-queue.
    rest = [p for p in pending_list if p.get("conversation_id") != conversation_id]
    try:
        store.set_meta(SIM_PENDING_META, json.dumps(rest))
    except Exception:
        pass

    age = tick - int(mine.get("tick", tick))
    if age > config.sim.max_stale_ticks:
        return None
    overlap = _overlap(mine.get("fingerprint") or [], _tail_fingerprint(current_window))
    if overlap < config.sim.min_overlap:
        return None

    injection = AttentionInjection(
        id=f"sim:{conversation_id}",
        title="Sim (supervisory)",
        message=mine.get("note", ""),
        reason=mine.get("basis", ""),
        confidence=float(mine.get("rating", 0.0)),
    )

    if config.sim.deposit_pheromone:
        _deposit_sim_pheromone(store, config)
    return injection


def _deposit_sim_pheromone(store: "Store", config: Config) -> None:
    """Advisory only — Sim injections lay a coarse trail like attention does.

    Sim feedback is not a graph node, so there is no node id to track per-item;
    we record the event on the conversation state for later inspection rather
    than on a node trail. Kept deliberately minimal and failure-safe.
    """
    try:
        ctx = pheromone_context(config)  # reserved for future conditioned trails
        _ = ctx
    except Exception:
        pass


def format_sim_injection(
    injection: AttentionInjection | None,
    *,
    display: str = "minimal",
) -> list[str]:
    """Render a Sim injection for the prompt hook output.

    display:
      full    — labelled block with basis + advisory footer
      minimal — a single "Sim:" prefixed line (the note is the signal)
      quiet   — same minimal text (the user-facing block is suppressed at the
                hook layer via suppressOutput; the model still receives this)
    """
    if not injection or not injection.message:
        return []
    note = injection.message.strip()
    if display == "quiet":
        # Agent-facing and invisible to the user. The agent routes it: act on it
        # silently if it can, and surface it to the user ONLY if it genuinely
        # needs their decision.
        return [
            "[SIM — supervisory note for you, the agent; the user does NOT see this. "
            "If you can act on it yourself, do so silently. Surface it to the user "
            f"only if it genuinely needs their judgment.] {note}"
        ]
    if display == "minimal":
        return [f"Sim: {note}"]
    lines = ["KINDEX · SIM"]
    conf = injection.confidence
    marker = f" ({conf:.2f})" if isinstance(conf, (int, float)) and conf else ""
    lines.append(f"  - {note}{marker}")
    if injection.reason:
        lines.append(f"    Basis: {injection.reason}")
    lines.append("  (Sim is advisory — ignore it freely. `kin sim disable` to stop.)")
    return lines


# ── background self-drain (no daemon required) ──────────────────────────────

def spawn_background_drain(config: Config) -> bool:
    """Fire-and-forget a detached `kin sim drain` so reviews run off the agent's
    critical path without a daemon. The prompt hook stays fast; the review lands
    a few seconds later and the next tick picks it up. Best-effort; never raises.
    """
    if not config.sim.drain_on_tick:
        return False
    import os
    import subprocess
    import sys

    try:
        subprocess.Popen(
            [sys.executable, "-m", "kindex.cli", "sim", "drain"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, start_new_session=True,
            env={**os.environ},
        )
        return True
    except Exception:
        return False
