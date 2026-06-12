"""Agent/client adapter helpers for hook protocols and tool payloads."""

from __future__ import annotations

import json
import re
import shlex
from typing import Any


ADAPTER_ALIASES = {
    "claude": "claude",
    "claude-code": "claude",
    "codex": "codex",
    "gemini": "gemini",
    "gemini-cli": "gemini",
    "opencode": "opencode",
    "open-code": "opencode",
    "cursor": "cursor",
    "antigravity": "antigravity",
    "ag": "antigravity",
    "plain": "plain",
    "claude-plain": "plain",
}


def normalize_adapter(adapter: str | None) -> str:
    """Return Kindex's canonical client/adapter name."""
    value = (adapter or "plain").strip().lower()
    return ADAPTER_ALIASES.get(value, value)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


def render_hook_context(
    context: str,
    *,
    adapter: str,
    event: str,
    suppress: bool = False,
) -> str:
    """Render context for a client's hook protocol.

    Core Kindex produces the same context block regardless of client. This
    adapter boundary only translates that block into the client's hook envelope.
    """
    if not context.strip():
        return ""

    canonical = normalize_adapter(adapter)
    hook_event = event or "UserPromptSubmit"

    if canonical == "plain":
        return context

    if canonical == "antigravity":
        if hook_event == "PreToolUse":
            return _json({"decision": "allow", "reason": context})
        if hook_event == "Stop":
            return _json({"decision": "", "reason": context})
        payload: dict[str, Any] = {
            "injectSteps": [{"ephemeralMessage": context}],
        }
        if hook_event == "PostInvocation":
            payload["terminationBehavior"] = ""
        return _json(payload)

    hook_output = {
        "hookEventName": hook_event,
        "additionalContext": context,
    }
    if canonical == "claude" and hook_event == "PreToolUse":
        hook_output["permissionDecision"] = "allow"
    payload = {"hookSpecificOutput": hook_output}
    if suppress:
        payload["suppressOutput"] = True
    return _json(payload)


def antigravity_allow() -> str:
    """PreToolUse allow response for Antigravity when Kindex has no advisory."""
    return _json({"decision": "allow"})


def extract_tool_call(payload: dict[str, Any] | None) -> tuple[str, Any]:
    """Extract a normalized tool name/input pair from known hook payloads."""
    data = payload or {}
    tool_name = data.get("tool_name") or data.get("toolName")
    tool_input = (
        data.get("tool_input")
        or data.get("toolInput")
        or data.get("parameters")
    )

    tool_call = data.get("toolCall")
    if isinstance(tool_call, dict):
        tool_name = tool_name or tool_call.get("name") or tool_call.get("toolName")
        tool_input = (
            tool_input
            or tool_call.get("args")
            or tool_call.get("arguments")
            or tool_call.get("parameters")
        )

    return str(tool_name or ""), tool_input


def extract_shell_command(payload: dict[str, Any] | None) -> str:
    """Return the shell command from Claude/Codex/Antigravity tool payloads."""
    tool_name, tool_input = extract_tool_call(payload)
    if tool_name not in {"Bash", "run_command"}:
        return ""
    if isinstance(tool_input, dict):
        for key in ("command", "CommandLine", "cmd", "script"):
            value = tool_input.get(key)
            if value:
                return str(value)
    return str(tool_input or "")


_CONFIG_WRITE_RE = re.compile(
    r"(?:^|[;&|]\s*)"
    r"(?:"
    r"(?:[A-Za-z0-9_./-]*kin)|"
    r"(?:python3?|[A-Za-z0-9_./-]*python3?)\s+-m\s+kindex\.cli"
    r")\s+"
    r"(?:agent-config\s+set|config\s+set)\b"
)


def is_kindex_config_write(payload: dict[str, Any] | None) -> bool:
    """True when a tool call is trying to mutate Kindex configuration."""
    command = extract_shell_command(payload)
    if not command.strip():
        return False
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    for idx, token in enumerate(tokens):
        base = token.rsplit("/", 1)[-1]
        if base == "kin" and tokens[idx + 1:idx + 3] == ["agent-config", "set"]:
            return True
        if base == "kin" and tokens[idx + 1:idx + 3] == ["config", "set"]:
            return True
        if (
            base in {"python", "python3"}
            and tokens[idx + 1:idx + 4] == ["-m", "kindex.cli", "agent-config"]
            and len(tokens) > idx + 4
            and tokens[idx + 4] == "set"
        ):
            return True
    return bool(_CONFIG_WRITE_RE.search(command))


def permission_gate_output(
    *,
    adapter: str,
    event: str,
    payload: dict[str, Any] | None,
) -> str:
    """Return a client permission-gate response, if Kindex should force one."""
    if normalize_adapter(adapter) != "antigravity" or event != "PreToolUse":
        return ""
    if not is_kindex_config_write(payload):
        return ""
    return _json({
        "decision": "force_ask",
        "reason": (
            "This changes Kindex behavior. Approve only if you want this agent "
            "to tune Kindex settings for the requested scope."
        ),
    })
