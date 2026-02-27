"""Action execution for actionable reminders.

Reminders can optionally carry a shell command and/or natural-language
instructions.  When due, the daemon (or manual ``kin remind exec``) runs:

* **shell** — ``subprocess.run(command, shell=True)``
* **claude** — ``claude -p <prompt>`` with assembled context
* **auto** (default) — shell if only command, claude if instructions present

Action metadata lives in the reminder's ``extra`` JSON field (no schema
migration required).
"""

from __future__ import annotations

import datetime
import subprocess
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .store import Store


# ── Field helpers ──────────────────────────────────────────────────


def get_action_fields(reminder: dict) -> dict:
    """Extract action fields from a reminder's extra dict.

    Returns a dict with normalised keys; missing keys get safe defaults.
    """
    extra = reminder.get("extra") or {}
    return {
        "action_command": extra.get("action_command", ""),
        "action_instructions": extra.get("action_instructions", ""),
        "action_mode": extra.get("action_mode", "auto"),
        "action_status": extra.get("action_status", "pending"),
        "action_result": extra.get("action_result", ""),
    }


def has_action(reminder: dict) -> bool:
    """True if the reminder has any action defined (command or instructions)."""
    fields = get_action_fields(reminder)
    return bool(fields["action_command"] or fields["action_instructions"])


def resolve_mode(fields: dict) -> str:
    """Resolve ``auto`` mode into ``shell`` or ``claude``.

    auto = shell when only a command is present, claude when instructions exist.
    """
    mode = fields.get("action_mode", "auto")
    if mode != "auto":
        return mode
    if fields.get("action_instructions"):
        return "claude"
    return "shell"


# ── Execution ──────────────────────────────────────────────────────


def execute_action(
    store: Store,
    reminder: dict,
    config: Config,
    *,
    timeout: int = 300,
) -> dict:
    """Execute a reminder's action.  Returns ``{"status": ..., "output": ...}``.

    Updates the reminder's ``extra`` with ``action_status`` and ``action_result``.
    """
    fields = get_action_fields(reminder)
    if not has_action(reminder):
        return {"status": "skipped", "reason": "no action defined"}

    if fields["action_status"] in ("completed", "running"):
        return {"status": "skipped", "reason": f"already {fields['action_status']}"}

    mode = resolve_mode(fields)
    rid = reminder["id"]

    # Mark as running (race guard for concurrent daemon cycles)
    _update_action_status(store, rid, reminder, "running", "")

    try:
        if mode == "shell":
            result = _run_shell(fields["action_command"], timeout=timeout)
        elif mode == "claude":
            result = _run_claude(reminder, fields, config, store, timeout=timeout)
        else:
            result = {"ok": False, "output": f"Unknown mode: {mode}"}

        status = "completed" if result["ok"] else "failed"
        _update_action_status(store, rid, reminder, status, result["output"])
        return {"status": status, "output": result["output"]}

    except Exception as e:
        _update_action_status(store, rid, reminder, "failed", str(e))
        return {"status": "failed", "output": str(e)}


# ── Internal helpers ───────────────────────────────────────────────


def _update_action_status(
    store: Store, rid: str, reminder: dict, status: str, result: str,
) -> None:
    """Write ``action_status`` and ``action_result`` into the reminder's extra."""
    extra = dict(reminder.get("extra") or {})
    extra["action_status"] = status
    extra["action_result"] = result[:4000]
    extra["action_executed_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    store.update_reminder(rid, extra=extra)


def _run_shell(command: str, *, timeout: int = 300) -> dict:
    """Run a shell command.  Returns ``{"ok": bool, "output": str}``."""
    try:
        proc = subprocess.run(
            command, shell=True,
            capture_output=True, text=True, timeout=timeout,
        )
        output = proc.stdout
        if proc.stderr:
            output += "\n[stderr]\n" + proc.stderr
        return {"ok": proc.returncode == 0, "output": output.strip()}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"Timed out after {timeout}s"}


def _build_claude_prompt(reminder: dict, fields: dict, store: Store) -> str:
    """Assemble the prompt string for a headless ``claude -p`` invocation."""
    parts = [f"# Reminder Action: {reminder['title']}"]
    if reminder.get("body"):
        parts.append(f"\n{reminder['body']}")
    if fields["action_instructions"]:
        parts.append(f"\n## Instructions\n{fields['action_instructions']}")
    if fields["action_command"]:
        parts.append(
            f"\n## Shell Command Available\n```\n{fields['action_command']}\n```"
        )
        parts.append("You may run this command if it helps accomplish the instructions.")

    # Include related knowledge node when present
    related_id = reminder.get("related_node_id")
    if related_id:
        node = store.get_node(related_id)
        if node:
            content = (node.get("content") or "")[:500]
            parts.append(f"\n## Related Knowledge\n**{node['title']}**: {content}")

    return "\n".join(parts)


def _run_claude(
    reminder: dict,
    fields: dict,
    config: Config,
    store: Store,
    *,
    timeout: int = 300,
) -> dict:
    """Launch ``claude -p`` with assembled context.  Returns ``{"ok": bool, "output": str}``."""
    prompt = _build_claude_prompt(reminder, fields, store)

    cmd = ["claude", "-p", prompt]

    model = config.reminders.channels.claude.headless_model
    if model:
        cmd.extend(["--model", model])

    budget = config.reminders.channels.claude.max_budget_usd
    if budget:
        cmd.extend(["--max-turns", "5"])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        return {"ok": proc.returncode == 0, "output": proc.stdout.strip()[:4000]}
    except subprocess.TimeoutExpired:
        return {"ok": False, "output": f"claude -p timed out after {timeout}s"}
    except FileNotFoundError:
        return {"ok": False, "output": "claude CLI not found in PATH"}
