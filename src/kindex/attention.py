"""Conversation-attention checks for prompt-time reminder injection."""

from __future__ import annotations

import datetime as _dt
import fnmatch
import hashlib
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass
from typing import Any

from .budget import BudgetLedger
from .config import Config

if False:  # pragma: no cover - type checking without runtime imports
    from .store import Store


ATTENTION_PURPOSE = "attention"
_STOP_TRIGGERS = {
    "a", "an", "and", "at", "be", "by", "for", "if", "in", "it", "of", "on",
    "or", "the", "this", "that", "to", "we", "you",
}
_STOP_TOKENS = _STOP_TRIGGERS | {
    "can", "could", "let", "lets", "need", "needs", "now", "should", "want",
    "wants", "will", "would",
}


@dataclass
class AttentionCandidate:
    id: str
    kind: str
    title: str
    text: str
    score: float
    reason: str
    priority: str = "normal"


@dataclass
class AttentionInjection:
    id: str
    title: str
    message: str
    reason: str = ""
    confidence: float = 0.0


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def _hash(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]


def _truthy(value: str | None) -> bool | None:
    if value is None or value == "":
        return None
    lowered = str(value).strip().lower()
    if lowered in {"1", "true", "yes", "on", "enabled"}:
        return True
    if lowered in {"0", "false", "no", "off", "disabled"}:
        return False
    return None


def _state_key(conversation_id: str) -> str:
    return f"attention.conversation.{_hash(conversation_id)}"


def _load_state(store: "Store", conversation_id: str) -> dict[str, Any]:
    raw = store.get_meta(_state_key(conversation_id))
    if not raw:
        return {"conversation_id": conversation_id, "ticks": 0, "injected": {}}
    try:
        state = json.loads(raw)
    except json.JSONDecodeError:
        state = {}
    state.setdefault("conversation_id", conversation_id)
    state.setdefault("ticks", 0)
    state.setdefault("injected", {})
    return state


def _save_state(store: "Store", conversation_id: str, state: dict[str, Any]) -> None:
    store.set_meta(_state_key(conversation_id), json.dumps(state, sort_keys=True))


def set_runtime_enabled(
    store: "Store",
    enabled: bool,
    *,
    conversation_id: str | None = None,
) -> None:
    """Set a runtime attention override globally or for one conversation."""
    if conversation_id:
        state = _load_state(store, conversation_id)
        state["enabled"] = enabled
        state["updated_at"] = _now()
        _save_state(store, conversation_id, state)
        return
    store.set_meta("attention.enabled", "true" if enabled else "false")


def clear_runtime_enabled(
    store: "Store",
    *,
    conversation_id: str | None = None,
) -> None:
    """Clear a runtime override by setting it to inherit."""
    if conversation_id:
        state = _load_state(store, conversation_id)
        state.pop("enabled", None)
        state["updated_at"] = _now()
        _save_state(store, conversation_id, state)
        return
    store.set_meta("attention.enabled", "")


def runtime_status(store: "Store", config: Config, conversation_id: str | None = None) -> dict:
    """Return effective attention state and budget-relevant configuration."""
    global_override = _truthy(store.get_meta("attention.enabled"))
    state: dict[str, Any] = {}
    conversation_override = None
    if conversation_id:
        state = _load_state(store, conversation_id)
        if "enabled" in state:
            conversation_override = bool(state["enabled"])

    effective = config.attention.enabled
    if global_override is not None:
        effective = global_override
    if conversation_override is not None:
        effective = conversation_override

    from .llm import is_configured as _llm_is_configured
    from .llm import resolve_api_key as _resolve_api_key

    llm_configured = _llm_is_configured(config)
    _, key_env = _resolve_api_key(config)

    return {
        "enabled": effective,
        "config_default": config.attention.enabled,
        "global_override": global_override,
        "conversation_override": conversation_override,
        "conversation_id": conversation_id or "",
        "ticks": state.get("ticks", 0),
        "llm_configured": llm_configured,
        "llm_provider": config.llm.provider,
        "llm_model": config.llm.model,
        "llm_api_key_env": key_env,
        "tick_interval": config.attention.tick_interval,
        "max_candidates": config.attention.max_candidates,
        "max_check_cost": config.attention.max_check_cost,
        "max_conversation_cost": config.attention.max_conversation_cost,
    }


def resolve_conversation_id(
    explicit: str | None = None,
    hook_payload: dict[str, Any] | None = None,
    *,
    fallback_to_cwd: bool = True,
) -> str:
    """Resolve a stable-ish conversation id from args, hook JSON, env, or cwd."""
    if explicit:
        return str(explicit)

    payload = hook_payload or {}
    for key in (
        "conversation_id", "conversationId",
        "chat_id", "chatId",
        "session_id", "sessionId",
        "thread_id", "threadId",
        "transcript_path", "transcriptPath",
    ):
        value = payload.get(key)
        if value:
            return str(value)

    for parent_key in ("conversation", "chat", "session", "thread"):
        child = payload.get(parent_key)
        if isinstance(child, dict):
            for key in ("id", "conversation_id", "chat_id", "session_id", "thread_id"):
                value = child.get(key)
                if value:
                    return str(value)

    for key in (
        "KIN_CONVERSATION_ID",
        "KIN_CHAT_ID",
        "CLAUDE_SESSION_ID",
        "CODEX_SESSION_ID",
        "CODEX_CONVERSATION_ID",
        "OPENCODE_SESSION_ID",
        "OPENCODE_CHAT_ID",
        "CURSOR_SESSION_ID",
        "CURSOR_CHAT_ID",
    ):
        value = os.environ.get(key)
        if value:
            return value

    if not fallback_to_cwd:
        return ""
    return f"cwd:{os.getcwd()}"


def extract_conversation_text(
    explicit: str | None = None,
    hook_payload: dict[str, Any] | None = None,
) -> str:
    """Extract the current user-visible conversation snippet."""
    if explicit:
        return explicit

    payload = hook_payload or {}
    for key in ("prompt", "message", "text", "user_prompt", "input"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    tool_name = payload.get("tool_name") or payload.get("toolName")
    tool_input = payload.get("tool_input") or payload.get("toolInput") or payload.get("parameters")
    if tool_name or tool_input:
        try:
            tool_text = json.dumps(tool_input, ensure_ascii=False, sort_keys=True)
        except TypeError:
            tool_text = str(tool_input)
        return f"tool_name: {tool_name or ''}\ntool_input: {tool_text}".strip()
    return ""


_BASH_SEGMENT_RE = re.compile(r"\|\||&&|\||;|&")
_LEADING_NOISE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")  # FOO=bar env prefix
_BASH_WRAPPERS = {"sudo", "command", "time", "nohup", "env", "exec", "builtin", "xargs", "nice", "stdbuf"}


def _bash_segment_is_readonly(segment: str, config: Config) -> bool:
    """True if a single pipeline segment is pure read-only inspection."""
    tokens = segment.strip().split()
    # Strip env-var assignments and benign wrappers (sudo/time/...) off the front.
    while tokens and (_LEADING_NOISE_RE.match(tokens[0]) or tokens[0] in _BASH_WRAPPERS):
        tokens = tokens[1:]
    if not tokens:
        return True  # nothing left (e.g. bare `FOO=bar`) — no action
    cmd = tokens[0].lstrip("(")
    sub = tokens[1] if len(tokens) > 1 else ""
    if cmd == "git":
        return sub in config.attention.readonly_git_subcommands
    if cmd == "kin":
        subs = config.attention.readonly_kin_subcommands
        # Entries may be two-word ("coord read", "profile list"): a parent
        # subcommand whose read-only-ness depends on its action argument.
        two = f"{sub} {tokens[2]}" if len(tokens) > 2 else ""
        return sub in subs or (bool(two) and two in subs)
    return cmd in config.attention.readonly_bash_commands


def is_background_action(
    hook_payload: dict[str, Any] | None,
    config: Config,
) -> bool:
    """True when a tool call is Kindex's own noise or pure read-only inspection.

    Attention is a reminder about *actions* the agent takes ("when you do X,
    always Y"). It should fire on real actions — edits, deploys, curl/API I/O,
    arbitrary commands — and stay silent only on (a) Kindex's own tool calls and
    (b) pure inspection. We use a read-only *denylist*, not an action allowlist:
    an allowlist would silently drop reminders for commands we didn't predict.

    Kindex's own LLM/API traffic (the attention judge, dream, extraction) runs in
    Kindex's runtime, not as an agent tool call, so it never reaches this hook —
    there is nothing to filter for it here.

    Returns False for non-tool events (e.g. a real user prompt) so those still run.
    """
    payload = hook_payload or {}
    tool_name = payload.get("tool_name") or payload.get("toolName")
    if not tool_name:
        return False  # not a tool event (user prompt, etc.) — let it run

    for pattern in config.attention.skip_tools:
        if fnmatch.fnmatchcase(tool_name, pattern):
            return True

    if tool_name == "Bash":
        tool_input = (
            payload.get("tool_input")
            or payload.get("toolInput")
            or payload.get("parameters")
            or {}
        )
        command = ""
        if isinstance(tool_input, dict):
            command = str(tool_input.get("command") or "")
        else:
            command = str(tool_input or "")
        if not command.strip():
            return True
        if ">" in command:  # redirection writes a file — that's an action
            return False
        segments = [s for s in _BASH_SEGMENT_RE.split(command) if s.strip()]
        # Background only if EVERY segment is read-only inspection.
        return all(_bash_segment_is_readonly(s, config) for s in segments)

    return False


def pheromone_context(config: Config) -> str:
    """Coarse, cross-session context fingerprint for conditioned trails.

    v1 = the project basename (stable across sessions, isolates trails per repo).
    Intra-project regime conditioning can refine this later without schema change.
    """
    path = getattr(config, "_project_path", None)
    if not path:
        return ""
    try:
        return os.path.basename(str(path).rstrip("/")) or ""
    except Exception:
        return ""


def injection_node_id(injection_id: str) -> str | None:
    """Map a candidate/injection id to the bare graph node id it ranks against.

    Candidates are namespaced 'node:<id>' / 'reminder:<id>'. Pheromone tracks
    graph nodes only (reminders are ephemeral), and retrieval looks up bare ids,
    so we strip the 'node:' prefix and ignore reminders.
    """
    if not injection_id:
        return None
    if injection_id.startswith("node:"):
        return injection_id[len("node:"):]
    if ":" in injection_id:
        return None  # reminder:* or other namespaces — not a graph node
    return injection_id  # already bare


def _deposit_injection_pheromone(store: "Store", config: Config, node_id: str,
                                 context: str) -> None:
    """Lay a deposit on the global trail and (if known) the conditioned trail."""
    bare = injection_node_id(node_id)
    if not bare:
        return
    try:
        store.deposit_pheromone(
            bare, context="",
            amount=config.attention.pheromone_deposit,
            half_life_days=config.attention.pheromone_half_life_days,
        )
        if context:
            store.deposit_pheromone(
                bare, context=context,
                amount=config.attention.pheromone_deposit,
                half_life_days=config.attention.pheromone_half_life_days,
            )
    except Exception:
        pass  # pheromone is advisory — never break the hook


def parse_hook_payload(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {"prompt": raw}


def read_hook_payload() -> dict[str, Any]:
    """Best-effort noninteractive stdin read for Claude Code hook payloads."""
    if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
        return {}
    raw = sys.stdin.read()
    return parse_hook_payload(raw)


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9][a-z0-9_-]*", text.lower()))


def _split_phrases(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        phrases: list[str] = []
        for item in value:
            phrases.extend(_split_phrases(item))
        return phrases
    text = str(value)
    parts = re.split(r"[,;\n]", text)
    return [p.strip().lower() for p in parts if p.strip()]


def _candidate_triggers(extra: dict[str, Any], text: str) -> list[str]:
    triggers: list[str] = []
    for key in (
        "attention_triggers",
        "triggers",
        "trigger_terms",
        "keywords",
        "match",
        "matches",
        "trigger",
    ):
        triggers.extend(_split_phrases(extra.get(key)))

    for trigger in list(triggers):
        if trigger.startswith("pre-"):
            triggers.append(trigger[4:])
        if trigger.startswith("post-"):
            triggers.append(trigger[5:])

    lowered = text.lower()
    for pattern in (
        r"\b(?:any time|anytime|when|before|after|while|if)\s+you\s+([a-z][a-z0-9_-]+)\b",
        r"\b(?:any time|anytime|when|before|after|while|if)\s+we\s+([a-z][a-z0-9_-]+)\b",
        r"\b(?:before|after|during)\s+(?:a\s+|an\s+|the\s+)?([a-z][a-z0-9_-]+)\b",
    ):
        triggers.extend(match.group(1) for match in re.finditer(pattern, lowered))

    seen: set[str] = set()
    out: list[str] = []
    for trigger in triggers:
        trigger = trigger.strip().lower()
        if len(trigger) < 3 or trigger in _STOP_TRIGGERS:
            continue
        if trigger and trigger not in seen:
            seen.add(trigger)
            out.append(trigger)
    return out


def _match_score(snippet: str, candidate_text: str, triggers: list[str]) -> tuple[float, str]:
    lowered = snippet.lower()
    snippet_tokens = _tokens(snippet)
    reasons: list[str] = []
    score = 0.0

    for trigger in triggers:
        if not trigger:
            continue
        if trigger in lowered:
            score += 4.0 if " " in trigger else 3.0
            reasons.append(f"trigger:{trigger}")

    cand_tokens = _tokens(candidate_text)
    overlap = snippet_tokens & cand_tokens
    if overlap:
        useful = sorted(t for t in overlap if len(t) > 2 and t not in _STOP_TOKENS)
        if useful:
            score += min(2.0, 0.35 * len(useful))
            reasons.append("overlap:" + ",".join(useful[:5]))

    return score, "; ".join(reasons)


def _priority_score(priority: str | int | None) -> float:
    if isinstance(priority, int):
        return {1: 1.0, 2: 0.6, 3: 0.25}.get(priority, 0.0)
    return {"urgent": 1.0, "high": 0.7, "normal": 0.25, "low": 0.0}.get(
        str(priority or "normal").lower(),
        0.0,
    )


def _node_to_candidate(node: dict, snippet: str, config: Config) -> AttentionCandidate | None:
    extra = node.get("extra") or {}
    title = node.get("title", "")
    content = node.get("content") or ""
    text = " ".join(
        part for part in (
            title,
            content,
            " ".join(str(v) for v in extra.values() if isinstance(v, (str, int, float))),
        )
        if part
    )
    triggers = _candidate_triggers(extra, text)
    score, reason = _match_score(snippet, text, triggers)
    if score <= 0:
        return None
    if "trigger:" not in reason and score < 0.7:
        return None

    kind = node.get("type", "node")
    if kind in {"constraint", "checkpoint"}:
        score += 0.7
    elif kind in {"directive", "task"}:
        score += 0.4

    priority = str(extra.get("priority", "normal"))
    score += _priority_score(extra.get("priority"))
    score += min(0.5, float(node.get("weight") or 0.0) * 0.25)

    return AttentionCandidate(
        id=f"node:{node['id']}",
        kind=kind,
        title=title,
        text=text[: config.attention.max_candidate_chars],
        score=round(score, 3),
        reason=reason or "semantic candidate",
        priority=priority,
    )


def _reminder_to_candidate(reminder: dict, snippet: str, config: Config) -> AttentionCandidate | None:
    extra = reminder.get("extra") or {}
    title = reminder.get("title", "")
    body = reminder.get("body") or ""
    parts = [
        title,
        body,
        reminder.get("tags", ""),
        extra.get("action_instructions", ""),
        extra.get("action_command", ""),
    ]
    text = " ".join(str(p) for p in parts if p)
    triggers = _candidate_triggers(extra, text)
    score, reason = _match_score(snippet, text, triggers)
    if score <= 0:
        return None
    if "trigger:" not in reason and score < 0.7:
        return None
    priority = reminder.get("priority", "normal")
    score += _priority_score(priority)
    return AttentionCandidate(
        id=f"reminder:{reminder['id']}",
        kind="reminder",
        title=title,
        text=text[: config.attention.max_candidate_chars],
        score=round(score, 3),
        reason=reason or "reminder candidate",
        priority=priority,
    )


def select_candidates(
    store: "Store",
    snippet: str,
    config: Config,
    *,
    conversation_id: str | None = None,
) -> list[AttentionCandidate]:
    """Select a compact candidate set before asking the LLM."""
    snippet = snippet.strip()[: config.attention.max_context_chars]
    if not snippet:
        return []

    from .reminders import reminder_matches_conversation
    from .scoping import item_matches_conversation

    by_id: dict[str, AttentionCandidate] = {}
    include_legacy_scoped_items = conversation_id is None

    for node in store.fts_search(snippet, limit=max(12, config.attention.max_candidates * 3)):
        if node.get("type") not in {"constraint", "directive", "checkpoint", "watch", "task"}:
            continue
        if node.get("type") == "task" and not item_matches_conversation(
            node,
            conversation_id,
            include_global=True,
            include_legacy=include_legacy_scoped_items,
        ):
            continue
        candidate = _node_to_candidate(node, snippet, config)
        if candidate:
            by_id[candidate.id] = candidate

    for node_type in ("constraint", "directive", "checkpoint", "task"):
        for node in store.all_nodes(node_type=node_type, status="active", limit=100):
            if node_type == "task" and not item_matches_conversation(
                node,
                conversation_id,
                include_global=True,
                include_legacy=include_legacy_scoped_items,
            ):
                continue
            candidate = _node_to_candidate(node, snippet, config)
            if candidate and (
                candidate.id not in by_id or candidate.score > by_id[candidate.id].score
            ):
                by_id[candidate.id] = candidate

    for node in store.active_watches()[:100]:
        candidate = _node_to_candidate(node, snippet, config)
        if candidate and (
            candidate.id not in by_id or candidate.score > by_id[candidate.id].score
        ):
            by_id[candidate.id] = candidate

    for status in ("active", "fired"):
        for reminder in store.list_reminders(status=status, limit=100):
            if not reminder_matches_conversation(
                reminder,
                conversation_id,
                include_global=True,
                include_legacy=include_legacy_scoped_items,
            ):
                continue
            candidate = _reminder_to_candidate(reminder, snippet, config)
            if candidate and (
                candidate.id not in by_id or candidate.score > by_id[candidate.id].score
            ):
                by_id[candidate.id] = candidate

    return sorted(by_id.values(), key=lambda c: c.score, reverse=True)[
        : config.attention.max_candidates
    ]


def _filter_cooldown(
    candidates: list[AttentionCandidate],
    state: dict[str, Any],
    config: Config,
) -> list[AttentionCandidate]:
    injected = state.get("injected") or {}
    now = _dt.datetime.now()
    kept: list[AttentionCandidate] = []
    for candidate in candidates:
        last = injected.get(candidate.id)
        if last:
            try:
                last_dt = _dt.datetime.fromisoformat(last)
                age = (now - last_dt).total_seconds()
                if age < config.attention.cooldown_seconds:
                    continue
            except (ValueError, TypeError):
                pass
        kept.append(candidate)
    return kept


def build_attention_prompt(snippet: str, candidates: list[AttentionCandidate]) -> str:
    candidate_json = json.dumps([asdict(c) for c in candidates], ensure_ascii=False)
    return f"""Decide whether Kindex should inject any of these preselected reminders into the active conversation.

Conversation/action snippet:
{snippet}

Candidate reminders/rules:
{candidate_json}

Inject only if it is likely to change the agent's next useful action. Prefer silence over noise.
If the snippet describes a tool/action being attempted, judge relevance to that action before it runs.
Reject broad policy reminders unless the conversation directly triggers that specific policy.
Use confidence as a relevance/urgency scale: 1.0 = absolutely must point this out now, 0.0 = nothing matters.
Return JSON only:
{{
  "inject": [
    {{"id": "candidate id", "message": "short context to inject", "reason": "why now", "confidence": 0.0}}
  ]
}}
"""


def estimate_prompt_cost(config: Config, prompt: str) -> dict:
    from .llm import estimate_cost

    tokens_in = max(1, len(prompt) // 4)
    tokens_out = config.attention.max_output_tokens
    amount = estimate_cost(config.llm.model, tokens_in, tokens_out)
    return {
        "model": config.llm.model,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "amount": round(amount, 8),
    }


def estimate_message_window(
    config: Config,
    *,
    messages: int = 100,
    observed_entries: list[dict] | None = None,
) -> dict:
    """Estimate attention spend over a message window."""
    messages = max(1, int(messages or 1))
    interval = max(1, int(config.attention.tick_interval or 1))
    checks = math.ceil(messages / interval)
    candidate_count = max(1, int(config.attention.max_candidates or 1))
    snippet = "x" * config.attention.max_context_chars
    candidates = [
        AttentionCandidate(
            id=f"estimate:{i}",
            kind="directive",
            title=f"Estimate candidate {i}",
            text="x" * config.attention.max_candidate_chars,
            score=1.0,
            reason="estimate",
        )
        for i in range(candidate_count)
    ]
    prompt = build_attention_prompt(snippet, candidates)
    per_check = estimate_prompt_cost(config, prompt)
    estimated_window = per_check["amount"] * checks

    result = {
        "model": config.llm.model,
        "messages": messages,
        "tick_interval": interval,
        "estimated_llm_checks": checks,
        "max_candidates": candidate_count,
        "per_check_estimate": per_check,
        "window_estimate": round(estimated_window, 6),
        "per_message_estimate": round(estimated_window / messages, 8),
    }

    entries = [
        e for e in (observed_entries or [])
        if e.get("purpose") == ATTENTION_PURPOSE and e.get("amount", 0) > 0
    ]
    if entries:
        avg = sum(e.get("amount", 0) for e in entries) / len(entries)
        result["observed"] = {
            "checks": len(entries),
            "average_per_check": round(avg, 6),
            "window_projection": round(avg * checks, 6),
            "per_message_projection": round((avg * checks) / messages, 8),
        }
    return result


def _parse_json_response(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if not stripped:
        return {}
    if "```" in stripped:
        parts = stripped.split("```")
        for part in parts:
            part = part.removeprefix("json").strip()
            if part.startswith("{") or part.startswith("["):
                stripped = part
                break
    if not (stripped.startswith("{") or stripped.startswith("[")):
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start >= 0 and end > start:
            stripped = stripped[start : end + 1]
    parsed = json.loads(stripped)
    if isinstance(parsed, list):
        return {"inject": parsed}
    if isinstance(parsed, dict):
        return parsed
    return {}


def judge_candidates(
    config: Config,
    ledger: BudgetLedger,
    snippet: str,
    candidates: list[AttentionCandidate],
    conversation_id: str,
    *,
    client: Any | None = None,
) -> tuple[list[AttentionInjection], dict]:
    """Ask the configured LLM to arbitrate a small candidate set."""
    if not candidates:
        return [], {"status": "no_candidates"}
    if not ledger.can_spend():
        return [], {"status": "over_global_budget"}

    prompt = build_attention_prompt(snippet, candidates)
    estimate = estimate_prompt_cost(config, prompt)
    conversation_spend = ledger.conversation_spend(
        conversation_id,
        purpose=ATTENTION_PURPOSE,
    )

    if estimate["amount"] > config.attention.max_check_cost:
        return [], {"status": "estimate_exceeds_check_budget", "estimate": estimate}
    if conversation_spend + estimate["amount"] > config.attention.max_conversation_cost:
        return [], {
            "status": "estimate_exceeds_conversation_budget",
            "estimate": estimate,
            "conversation_spend": round(conversation_spend, 6),
        }

    if client is None:
        from .llm import get_client

        client = get_client(config)
    if client is None:
        return [], {"status": "llm_unavailable", "estimate": estimate}

    try:
        response = client.messages.create(
            model=config.llm.model,
            max_tokens=config.attention.max_output_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = response.usage
        from .llm import calculate_cost

        cost = calculate_cost(config.llm.model, usage)
        ledger.record(
            cost["amount"],
            model=config.llm.model,
            purpose=ATTENTION_PURPOSE,
            tokens_in=cost["tokens_in"],
            tokens_out=cost["tokens_out"],
            cache_creation_tokens=cost.get("cache_creation_tokens", 0),
            cache_read_tokens=cost.get("cache_read_tokens", 0),
            conversation_id=conversation_id,
            estimate=estimate["amount"],
            metadata={"candidate_count": len(candidates)},
        )

        text_out = response.content[0].text
        parsed = _parse_json_response(text_out)
    except Exception as exc:
        return [], {"status": "llm_error", "error": str(exc), "estimate": estimate}

    candidate_by_id = {c.id: c for c in candidates}
    injections: list[AttentionInjection] = []
    for item in parsed.get("inject", []):
        if not isinstance(item, dict):
            continue
        cid = str(item.get("id") or "")
        candidate = candidate_by_id.get(cid)
        if not candidate:
            continue
        message = str(item.get("message") or candidate.text or candidate.title).strip()
        reason = str(item.get("reason") or candidate.reason).strip()
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < config.attention.min_confidence:
            continue
        injections.append(
            AttentionInjection(
                id=cid,
                title=candidate.title,
                message=message[: config.attention.max_candidate_chars],
                reason=reason[:200],
                confidence=confidence,
            )
        )

    return injections, {
        "status": "ok",
        "estimate": estimate,
        "actual": cost,
        "candidate_count": len(candidates),
    }


def run_attention_check(
    store: "Store",
    config: Config,
    ledger: BudgetLedger,
    snippet: str,
    conversation_id: str,
    *,
    force: bool = False,
    client: Any | None = None,
) -> dict:
    """Run one attention tick and return selected injections plus accounting."""
    if not conversation_id:
        return {"status": "missing_conversation_id", "injections": []}

    status = runtime_status(store, config, conversation_id)
    if not status["enabled"]:
        return {"status": "disabled", "injections": [], "runtime": status}
    if not status["llm_configured"]:
        return {"status": "llm_not_configured", "injections": [], "runtime": status}

    state = _load_state(store, conversation_id)
    state["ticks"] = int(state.get("ticks", 0)) + 1
    state["last_tick_at"] = _now()

    interval = max(1, int(config.attention.tick_interval or 1))
    should_run = force or state["ticks"] % interval == 0
    if not should_run:
        _save_state(store, conversation_id, state)
        return {
            "status": "waiting_for_tick",
            "injections": [],
            "runtime": status,
            "ticks": state["ticks"],
        }

    candidates = select_candidates(store, snippet, config, conversation_id=conversation_id)
    candidates = _filter_cooldown(candidates, state, config)
    injections, judge = judge_candidates(
        config,
        ledger,
        snippet[: config.attention.max_context_chars],
        candidates,
        conversation_id,
        client=client,
    )

    if injections:
        injected = state.setdefault("injected", {})
        now = _now()
        ctx = pheromone_context(config)
        deposit = state.setdefault("pheromone_deposits", {})
        for injection in injections:
            injected[injection.id] = now
            # Stigmergic trace: the injection itself is the deposit. Lay on both
            # the coarse global trail and the context-conditioned trail.
            if config.attention.pheromone_enabled:
                _deposit_injection_pheromone(store, config, injection.id, ctx)
                deposit[injection.id] = {"at": now, "context": ctx}
        state["last_injection_at"] = now
    _save_state(store, conversation_id, state)

    return {
        "status": judge.get("status", "ok"),
        "injections": [asdict(i) for i in injections],
        "candidates": [asdict(c) for c in candidates],
        "judge": judge,
        "runtime": status,
        "ticks": state["ticks"],
    }


def format_attention_injections(result: dict, display: str = "full") -> list[str]:
    """Render attention injections.

    display:
      full    — labelled "KINDEX ATTENTION" block with Reason/Source + budget line
      minimal — one bare line per injection (no header, no chrome, no budget)
      quiet   — same bare lines (the user-facing block is suppressed at the hook
                layer via suppressOutput; the model still receives this text)
    """
    injections = result.get("injections") or []
    if not injections:
        return []

    if display in ("minimal", "quiet"):
        return [f"- {item['message']}" for item in injections]

    lines = ["KINDEX ATTENTION"]
    for item in injections:
        conf = item.get("confidence", 0)
        marker = f" ({conf:.2f})" if isinstance(conf, (int, float)) and conf else ""
        lines.append(f"  - {item['message']}{marker}")
        if item.get("reason"):
            lines.append(f"    Reason: {item['reason']}")
        lines.append(f"    Source: {item.get('title', item.get('id', ''))}")
    judge = result.get("judge") or {}
    estimate = judge.get("estimate") or {}
    if estimate:
        lines.append(
            "  Budget estimate: "
            f"${estimate.get('amount', 0):.6f} "
            f"({estimate.get('tokens_in', 0)} in / {estimate.get('tokens_out', 0)} out)"
        )
    return lines
