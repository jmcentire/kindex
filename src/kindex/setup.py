"""System setup — install agent integrations, launchd plists, crontab entries."""

import json
import os
import shlex
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


def _kin_command_parts(kin_path: str) -> list[str]:
    """Split the fallback python -m invocation while preserving normal kin paths."""
    if " -m kindex.cli" in kin_path:
        return shlex.split(kin_path)
    return [kin_path]


def _kin_hook_command(kin_path: str, args: list[str]) -> str:
    """Build a hook command that loads shell exports before running kin."""
    command = " ".join(shlex.quote(part) for part in [*_kin_command_parts(kin_path), *args])
    script = f"source ~/.profile >/dev/null 2>&1 || true; exec {command}"
    return f"/bin/bash -lc {shlex.quote(script)}"


def _kin_stop_hook_command(kin_path: str, args: list[str]) -> str:
    """Build a Claude Stop hook command that avoids stop-hook recursion."""
    command = " ".join(shlex.quote(part) for part in [*_kin_command_parts(kin_path), *args])
    active_check = (
        "import json,sys; "
        "raw=sys.stdin.read(); "
        "\ntry:\n data=json.loads(raw or '{}') if raw.strip() else {}\n"
        "except Exception:\n data={}\n"
        "sys.exit(0 if data.get('stop_hook_active') else 1)"
    )
    script = (
        "payload=$(cat); "
        f"if printf '%s' \"$payload\" | python3 -c {shlex.quote(active_check)}; "
        "then exit 0; fi; "
        "source ~/.profile >/dev/null 2>&1 || true; "
        f"printf '%s' \"$payload\" | {command}"
    )
    return f"/bin/bash -lc {shlex.quote(script)}"


def _hook_needs_profile(entry: object) -> bool:
    return "source ~/.profile" not in str(entry)


def _hook_needs_stop_active_guard(entry: object) -> bool:
    return "stop_hook_active" not in str(entry)


def install_claude_hooks(config: "Config", dry_run: bool = False) -> list[str]:
    """Install Kindex hooks into Claude Code's settings.json.

    Adds:
    - SessionStart hook: kin prime --for hook
    - PreCompact hook: kin compact-hook --emit-context

    Preserves existing hooks. Returns list of actions taken.
    """
    settings_path = config.claude_path / "settings.json"
    actions = []

    # Read existing settings
    if settings_path.exists():
        data = json.loads(settings_path.read_text())
    else:
        data = {}

    hooks = data.setdefault("hooks", {})

    # Find kin binary path
    kin_path = _find_kin_path()

    # SessionStart hook
    session_start = hooks.setdefault("SessionStart", [])
    kindex_hook = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": _kin_hook_command(kin_path, ["prime", "--for", "hook"]),
            "timeout": 5000
        }]
    }
    # Check if already installed
    existing_idx = next(
        (i for i, h in enumerate(session_start)
         if "kin prime" in str(h) or "kindex" in str(h).lower()),
        None,
    )
    if existing_idx is None:
        session_start.append(kindex_hook)
        actions.append("Added SessionStart hook: kin prime --for hook")
    elif _hook_needs_profile(session_start[existing_idx]):
        session_start[existing_idx] = kindex_hook
        actions.append("Updated SessionStart hook to source ~/.profile")
    else:
        actions.append("SessionStart hook already installed")

    # PreCompact hook
    pre_compact = hooks.setdefault("PreCompact", [])
    compact_hook = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": _kin_hook_command(kin_path, ["compact-hook", "--emit-context"]),
            "timeout": 10000
        }]
    }
    existing_idx = next((i for i, h in enumerate(pre_compact) if "compact-hook" in str(h)), None)
    if existing_idx is None:
        pre_compact.append(compact_hook)
        actions.append("Added PreCompact hook: kin compact-hook --emit-context")
    elif _hook_needs_profile(pre_compact[existing_idx]):
        pre_compact[existing_idx] = compact_hook
        actions.append("Updated PreCompact hook to source ~/.profile")
    else:
        actions.append("PreCompact hook already installed")

    # UserPromptSubmit hook — inject due reminders mid-session
    prompt_submit = hooks.setdefault("UserPromptSubmit", [])
    prompt_hook = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": _kin_hook_command(kin_path, ["prompt-check"]),
            "timeout": 2000
        }]
    }
    existing_idx = next((i for i, h in enumerate(prompt_submit) if "prompt-check" in str(h)), None)
    if existing_idx is None:
        prompt_submit.append(prompt_hook)
        actions.append("Added UserPromptSubmit hook: kin prompt-check")
    elif _hook_needs_profile(prompt_submit[existing_idx]):
        prompt_submit[existing_idx] = prompt_hook
        actions.append("Updated UserPromptSubmit hook to source ~/.profile")
    else:
        actions.append("UserPromptSubmit hook already installed")

    # PreToolUse hook — advisory attention near actions, modeled after signet-eval INJECT.
    pre_tool = hooks.setdefault("PreToolUse", [])
    attention_hook = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": _kin_hook_command(kin_path, ["attention-hook", "--adapter", "claude", "--event", "PreToolUse"]),
            "timeout": 5000,
        }]
    }
    existing_idx = next((i for i, h in enumerate(pre_tool) if "attention-hook" in str(h)), None)
    if existing_idx is None:
        pre_tool.append(attention_hook)
        actions.append("Added PreToolUse hook: kin attention-hook")
    elif _hook_needs_profile(pre_tool[existing_idx]):
        pre_tool[existing_idx] = attention_hook
        actions.append("Updated PreToolUse hook to source ~/.profile")
    else:
        actions.append("PreToolUse attention hook already installed")

    # Stop hook — session capture. Blocking and expensive work are opt-in because
    # Claude surfaces blocking output and dream can consume noticeable CPU.
    stop_hooks = hooks.setdefault("Stop", [])
    stop_hook_commands = []
    if config.reminders.stop_guard_enabled:
        stop_hook_commands.append({
            "type": "command",
            "command": _kin_stop_hook_command(kin_path, ["stop-guard"]),
            "timeout": 5000,
        })
    stop_hook_commands.extend([
        {
            "type": "command",
            "command": _kin_stop_hook_command(kin_path, ["compact-hook", "--text", "Session ended"]),
            "timeout": 5000,
        },
        {
            # Super lightweight + silent: records the session for later
            # reinforcement grading in cron (no LLM, no output on the hot path).
            "type": "command",
            "command": _kin_stop_hook_command(kin_path, ["attention", "reinforce", "--enqueue"]),
            "timeout": 3000,
        },
    ])
    if config.reminders.dream_on_stop_enabled:
        stop_hook_commands.append({
            "type": "command",
            "command": _kin_stop_hook_command(kin_path, ["dream", "--detach", "--lightweight"]),
            "timeout": 3000,
        })
    stop_guard_entry = {
        "matcher": "",
        "hooks": stop_hook_commands,
    }
    existing_idx = next(
        (i for i, h in enumerate(stop_hooks)
         if "stop-guard" in str(h) or "compact-hook" in str(h) or "dream" in str(h)
         or "reinforce" in str(h)),
        None,
    )
    if existing_idx is None:
        stop_hooks.append(stop_guard_entry)
        if config.reminders.stop_guard_enabled:
            action = "Added Stop hook: kin stop-guard + compact-hook"
        else:
            action = "Added Stop hook: kin compact-hook"
        if config.reminders.dream_on_stop_enabled:
            action += " + dream"
        actions.append(action)
    elif (
        _hook_needs_profile(stop_hooks[existing_idx])
        or _hook_needs_stop_active_guard(stop_hooks[existing_idx])
        or ("dream" in str(stop_hooks[existing_idx]) and not config.reminders.dream_on_stop_enabled)
        or ("dream" not in str(stop_hooks[existing_idx]) and config.reminders.dream_on_stop_enabled)
        or ("stop-guard" in str(stop_hooks[existing_idx]) and not config.reminders.stop_guard_enabled)
        or ("stop-guard" not in str(stop_hooks[existing_idx]) and config.reminders.stop_guard_enabled)
    ):
        stop_hooks[existing_idx] = stop_guard_entry
        action = "Updated Stop hook to source ~/.profile and avoid recursion"
        if config.reminders.stop_guard_enabled:
            action += " with guard"
        if config.reminders.dream_on_stop_enabled:
            action += " with dream"
        actions.append(action)
    else:
        actions.append("Stop hook already installed")

    if not dry_run:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(data, indent=2) + "\n")
        actions.append(f"Wrote {settings_path}")

    return actions


def install_codex_hooks(config: "Config", dry_run: bool = False) -> list[str]:
    """Install Kindex prompt-time attention hook into ~/.codex/hooks.json."""
    hooks_path = config.codex_path / "hooks.json"
    actions = []
    if hooks_path.exists():
        data = json.loads(hooks_path.read_text())
    else:
        data = {}
    hooks = data.setdefault("hooks", {})
    prompt_submit = hooks.setdefault("UserPromptSubmit", [])
    kin_path = _find_kin_path()
    entry = {
        "hooks": [{
            "type": "command",
            "command": _kin_hook_command(kin_path, ["attention-hook", "--adapter", "codex", "--event", "UserPromptSubmit"]),
            "timeout": 5,
            "statusMessage": "Checking Kindex attention",
        }]
    }

    existing_idx = next(
        (i for i, h in enumerate(prompt_submit)
         if "prompt-check" in str(h) or "attention-hook" in str(h)),
        None,
    )
    if existing_idx is None:
        prompt_submit.append(entry)
        actions.append("Added Codex UserPromptSubmit hook: kin attention-hook")
    elif (
        _hook_needs_profile(prompt_submit[existing_idx])
        or "prompt-check" in str(prompt_submit[existing_idx])
        or "--adapter" not in str(prompt_submit[existing_idx])
    ):
        prompt_submit[existing_idx] = entry
        actions.append("Updated Codex UserPromptSubmit hook to source ~/.profile")
    else:
        actions.append("Codex UserPromptSubmit hook already installed")

    post_tool = hooks.setdefault("PostToolUse", [])
    post_entry = {
        "hooks": [{
            "type": "command",
            "command": _kin_hook_command(kin_path, ["attention-hook", "--adapter", "codex", "--event", "PostToolUse"]),
            "timeout": 5,
            "statusMessage": "Checking Kindex attention",
        }]
    }
    existing_idx = next((i for i, h in enumerate(post_tool) if "attention-hook" in str(h)), None)
    if existing_idx is None:
        post_tool.append(post_entry)
        actions.append("Added Codex PostToolUse hook: kin attention-hook")
    elif _hook_needs_profile(post_tool[existing_idx]):
        post_tool[existing_idx] = post_entry
        actions.append("Updated Codex PostToolUse hook to source ~/.profile")
    else:
        actions.append("Codex PostToolUse attention hook already installed")

    if dry_run:
        actions.append(f"Would write {hooks_path}")
        return actions

    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    hooks_path.write_text(json.dumps(data, indent=2) + "\n")
    actions.append(f"Wrote {hooks_path}")
    return actions


def uninstall_codex_hooks(config: "Config", dry_run: bool = False) -> list[str]:
    """Remove Kindex prompt-time attention hook from ~/.codex/hooks.json."""
    hooks_path = config.codex_path / "hooks.json"
    if not hooks_path.exists():
        return ["No Codex hooks.json found"]

    data = json.loads(hooks_path.read_text())
    hooks = data.get("hooks", {})
    prompt_submit = hooks.get("UserPromptSubmit", [])
    post_tool = hooks.get("PostToolUse", [])
    kept = [
        h for h in prompt_submit
        if "prompt-check" not in str(h) and "attention-hook" not in str(h)
    ]
    kept_post = [h for h in post_tool if "attention-hook" not in str(h)]
    if len(kept) == len(prompt_submit) and len(kept_post) == len(post_tool):
        return ["No Kindex Codex UserPromptSubmit hook found"]

    if dry_run:
        return [f"Would remove Codex UserPromptSubmit hook from {hooks_path}"]

    if kept:
        hooks["UserPromptSubmit"] = kept
    else:
        hooks.pop("UserPromptSubmit", None)
    if kept_post:
        hooks["PostToolUse"] = kept_post
    else:
        hooks.pop("PostToolUse", None)
    if hooks:
        data["hooks"] = hooks
    else:
        data.pop("hooks", None)
    hooks_path.write_text(json.dumps(data, indent=2) + "\n")
    return [f"Removed Codex UserPromptSubmit hook from {hooks_path}"]


def install_codex_mcp(config: "Config", dry_run: bool = False) -> list[str]:
    """Install Kindex as a Codex MCP server in ~/.codex/config.toml.

    This mirrors the config produced by:
        codex mcp add kindex -- kin-mcp

    Preserves existing Codex settings. Returns list of actions taken.
    """
    config_path = config.codex_path / "config.toml"
    actions = []

    existing = config_path.read_text() if config_path.exists() else ""

    if "[mcp_servers.kindex]" in existing:
        actions.append("Codex MCP server already installed")
        return actions

    block = '[mcp_servers.kindex]\ncommand = "kin-mcp"\n'

    if dry_run:
        actions.append(f"Would add Codex MCP server to {config_path}")
        actions.append("Would configure: codex mcp add kindex -- kin-mcp")
        return actions

    config_path.parent.mkdir(parents=True, exist_ok=True)
    prefix = existing.rstrip()
    content = f"{prefix}\n\n{block}" if prefix else block
    config_path.write_text(content)
    actions.append(f"Added Codex MCP server: kindex -> kin-mcp")
    actions.append(f"Wrote {config_path}")
    return actions


def uninstall_codex_mcp(config: "Config", dry_run: bool = False) -> list[str]:
    """Remove Kindex's Codex MCP server block from ~/.codex/config.toml."""
    config_path = config.codex_path / "config.toml"
    actions = []

    if not config_path.exists():
        return ["No Codex config.toml found"]

    text = config_path.read_text()
    lines = text.splitlines()
    out: list[str] = []
    removed = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == "[mcp_servers.kindex]":
            removed = True
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("["):
                i += 1
            continue
        out.append(line)
        i += 1

    if not removed:
        return ["No Kindex Codex MCP server found"]

    if dry_run:
        actions.append(f"Would remove Codex MCP server from {config_path}")
    else:
        config_path.write_text("\n".join(out).rstrip() + "\n")
        actions.append(f"Removed Codex MCP server from {config_path}")

    return actions


def install_gemini_mcp(config: "Config", dry_run: bool = False) -> list[str]:
    """Install Kindex MCP server config into ~/.gemini/settings.json."""
    settings_path = config.gemini_path / "settings.json"
    actions = []

    if settings_path.exists():
        data = json.loads(settings_path.read_text())
    else:
        data = {}

    mcp_servers = data.setdefault("mcpServers", {})
    if "kindex" in mcp_servers:
        return ["Gemini MCP server already installed"]

    mcp_servers["kindex"] = {"command": "kin-mcp", "args": []}

    if dry_run:
        actions.append(f"Would add Gemini MCP server to {settings_path}")
        actions.append('Would configure: mcpServers.kindex = {"command":"kin-mcp","args":[]}')
        return actions

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    actions.append("Added Gemini MCP server: kindex -> kin-mcp")
    actions.append(f"Wrote {settings_path}")
    return actions


def uninstall_gemini_mcp(config: "Config", dry_run: bool = False) -> list[str]:
    """Remove Kindex MCP config from ~/.gemini/settings.json."""
    settings_path = config.gemini_path / "settings.json"

    if not settings_path.exists():
        return ["No Gemini settings.json found"]

    data = json.loads(settings_path.read_text())
    mcp_servers = data.get("mcpServers", {})

    if "kindex" not in mcp_servers:
        return ["No Kindex Gemini MCP server found"]

    if dry_run:
        return [f"Would remove Gemini MCP server from {settings_path}"]

    del mcp_servers["kindex"]
    if mcp_servers:
        data["mcpServers"] = mcp_servers
    else:
        data.pop("mcpServers", None)

    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return [f"Removed Gemini MCP server from {settings_path}"]


def install_opencode_mcp(config: "Config", dry_run: bool = False) -> list[str]:
    """Install Kindex MCP server config into ~/.config/opencode/opencode.json."""
    settings_path = config.opencode_path / "opencode.json"
    actions = []

    if settings_path.exists():
        data = json.loads(settings_path.read_text())
    else:
        data = {"$schema": "https://opencode.ai/config.json"}

    mcp = data.setdefault("mcp", {})
    if "kindex" in mcp:
        return ["OpenCode MCP server already installed"]

    mcp["kindex"] = {
        "type": "local",
        "command": ["kin-mcp"],
        "enabled": True,
    }

    if dry_run:
        actions.append(f"Would add OpenCode MCP server to {settings_path}")
        actions.append('Would configure: mcp.kindex = {"type":"local","command":["kin-mcp"],"enabled":true}')
        return actions

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    actions.append("Added OpenCode MCP server: kindex -> kin-mcp")
    actions.append(f"Wrote {settings_path}")
    return actions


def uninstall_opencode_mcp(config: "Config", dry_run: bool = False) -> list[str]:
    """Remove Kindex MCP config from ~/.config/opencode/opencode.json."""
    settings_path = config.opencode_path / "opencode.json"

    if not settings_path.exists():
        return ["No OpenCode opencode.json found"]

    data = json.loads(settings_path.read_text())
    mcp = data.get("mcp", {})

    if "kindex" not in mcp:
        return ["No Kindex OpenCode MCP server found"]

    if dry_run:
        return [f"Would remove OpenCode MCP server from {settings_path}"]

    del mcp["kindex"]
    if mcp:
        data["mcp"] = mcp
    else:
        data.pop("mcp", None)

    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return [f"Removed OpenCode MCP server from {settings_path}"]


def install_cursor_mcp(config: "Config", dry_run: bool = False) -> list[str]:
    """Install Kindex MCP server config into ~/.cursor/mcp.json."""
    settings_path = config.cursor_path / "mcp.json"
    actions = []

    if settings_path.exists():
        data = json.loads(settings_path.read_text())
    else:
        data = {}

    mcp_servers = data.setdefault("mcpServers", {})
    if "kindex" in mcp_servers:
        return ["Cursor MCP server already installed"]

    mcp_servers["kindex"] = {
        "type": "stdio",
        "command": "kin-mcp",
    }

    if dry_run:
        actions.append(f"Would add Cursor MCP server to {settings_path}")
        actions.append('Would configure: mcpServers.kindex = {"type":"stdio","command":"kin-mcp"}')
        return actions

    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    actions.append("Added Cursor MCP server: kindex -> kin-mcp")
    actions.append(f"Wrote {settings_path}")
    return actions


def uninstall_cursor_mcp(config: "Config", dry_run: bool = False) -> list[str]:
    """Remove Kindex MCP config from ~/.cursor/mcp.json."""
    settings_path = config.cursor_path / "mcp.json"

    if not settings_path.exists():
        return ["No Cursor mcp.json found"]

    data = json.loads(settings_path.read_text())
    mcp_servers = data.get("mcpServers", {})

    if "kindex" not in mcp_servers:
        return ["No Kindex Cursor MCP server found"]

    if dry_run:
        return [f"Would remove Cursor MCP server from {settings_path}"]

    del mcp_servers["kindex"]
    if mcp_servers:
        data["mcpServers"] = mcp_servers
    else:
        data.pop("mcpServers", None)

    settings_path.write_text(json.dumps(data, indent=2) + "\n")
    return [f"Removed Cursor MCP server from {settings_path}"]


def install_launchd(config: "Config", dry_run: bool = False) -> list[str]:
    """Install macOS launchd plist for kin cron.

    Creates ~/Library/LaunchAgents/com.kindex.cron.plist
    Uses config.reminders.check_interval for the initial interval.
    """
    actions = []
    kin_path = _find_kin_path()
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_agents / "com.kindex.cron.plist"
    log_dir = config.data_path / "logs"
    interval = config.reminders.check_interval

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kindex.cron</string>
    <key>ProgramArguments</key>
    <array>
        <string>{kin_path}</string>
        <string>cron</string>
    </array>
    <key>StartInterval</key>
    <integer>{interval}</integer>
    <key>StandardOutPath</key>
    <string>{log_dir}/cron.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/cron-error.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""

    if not dry_run:
        launch_agents.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)
        # Load the plist
        subprocess.run(["launchctl", "load", str(plist_path)],
                       capture_output=True, timeout=5)
        actions.append(f"Installed launchd plist: {plist_path}")
        actions.append("Loaded with launchctl")
    else:
        actions.append(f"Would install: {plist_path}")

    return actions


def reload_launchd() -> bool:
    """Unload and reload the cron plist. Returns True if successful."""
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.kindex.cron.plist"
    if not plist_path.exists():
        return False
    subprocess.run(["launchctl", "unload", str(plist_path)],
                   capture_output=True, timeout=5)
    result = subprocess.run(["launchctl", "load", str(plist_path)],
                            capture_output=True, timeout=5)
    return result.returncode == 0


def uninstall_launchd(dry_run: bool = False) -> list[str]:
    """Remove the launchd plist."""
    actions = []
    plist_path = Path.home() / "Library" / "LaunchAgents" / "com.kindex.cron.plist"

    if plist_path.exists():
        if not dry_run:
            subprocess.run(["launchctl", "unload", str(plist_path)],
                          capture_output=True, timeout=5)
            plist_path.unlink()
            actions.append("Unloaded and removed launchd plist")
        else:
            actions.append(f"Would remove: {plist_path}")
    else:
        actions.append("No launchd plist found")

    return actions


def install_crontab(config: "Config", dry_run: bool = False) -> list[str]:
    """Install crontab entry for kin cron (for Linux/non-macOS)."""
    actions = []
    kin_path = _find_kin_path()
    log_path = config.data_path / "logs" / "cron.log"

    cron_line = f"*/30 * * * * {kin_path} cron >> {log_path} 2>&1"

    # Check existing crontab
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = result.stdout if result.returncode == 0 else ""

    if "kin cron" in existing or "kindex" in existing:
        actions.append("Crontab entry already exists")
        return actions

    if not dry_run:
        new_crontab = existing.rstrip() + "\n" + cron_line + "\n"
        proc = subprocess.run(["crontab", "-"], input=new_crontab,
                              capture_output=True, text=True)
        if proc.returncode == 0:
            actions.append(f"Added crontab: {cron_line}")
        else:
            actions.append(f"Failed to add crontab: {proc.stderr}")
    else:
        actions.append(f"Would add crontab: {cron_line}")

    return actions


def install_reminder_daemon(config: "Config", dry_run: bool = False) -> list[str]:
    """Install macOS launchd plist for reminder checks (every 5 min).

    Creates ~/Library/LaunchAgents/com.kindex.reminders.plist
    Separate from the main cron plist to keep heavy ingestion at 30 min.
    """
    actions = []
    kin_path = _find_kin_path()
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_agents / "com.kindex.reminders.plist"
    log_dir = config.data_path / "logs"
    interval = config.reminders.check_interval

    plist_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.kindex.reminders</string>
    <key>ProgramArguments</key>
    <array>
        <string>{kin_path}</string>
        <string>remind</string>
        <string>check</string>
    </array>
    <key>StartInterval</key>
    <integer>{interval}</integer>
    <key>StandardOutPath</key>
    <string>{log_dir}/reminders.log</string>
    <key>StandardErrorPath</key>
    <string>{log_dir}/reminders-error.log</string>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""

    if not dry_run:
        launch_agents.mkdir(parents=True, exist_ok=True)
        log_dir.mkdir(parents=True, exist_ok=True)
        plist_path.write_text(plist_content)
        subprocess.run(["launchctl", "load", str(plist_path)],
                       capture_output=True, timeout=5)
        actions.append(f"Installed reminder daemon: {plist_path}")
        actions.append(f"Check interval: {interval}s")
    else:
        actions.append(f"Would install: {plist_path}")

    return actions


def _find_kin_path() -> str:
    """Find the kin executable path."""
    result = subprocess.run(["which", "kin"], capture_output=True, text=True, timeout=5)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    # Fallback to python -m
    import sys
    return f"{sys.executable} -m kindex.cli"
