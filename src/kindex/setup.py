"""System setup — install Claude Code hooks, launchd plists, crontab entries."""

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config


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
            "command": f"{kin_path} prime --for hook",
            "timeout": 5000
        }]
    }
    # Check if already installed
    if not any("kin prime" in str(h) or "kindex" in str(h).lower() for h in session_start):
        session_start.append(kindex_hook)
        actions.append("Added SessionStart hook: kin prime --for hook")
    else:
        actions.append("SessionStart hook already installed")

    # PreCompact hook
    pre_compact = hooks.setdefault("PreCompact", [])
    compact_hook = {
        "matcher": "",
        "hooks": [{
            "type": "command",
            "command": f"{kin_path} compact-hook --emit-context",
            "timeout": 10000
        }]
    }
    if not any("compact-hook" in str(h) for h in pre_compact):
        pre_compact.append(compact_hook)
        actions.append("Added PreCompact hook: kin compact-hook --emit-context")
    else:
        actions.append("PreCompact hook already installed")

    # Stop hook — guard for actionable reminders + session capture
    stop_hooks = hooks.setdefault("Stop", [])
    stop_guard_entry = {
        "matcher": "",
        "hooks": [
            {
                "type": "command",
                "command": f"{kin_path} stop-guard",
                "timeout": 5000,
            },
            {
                "type": "command",
                "command": f'{kin_path} compact-hook --text "Session ended"',
                "timeout": 5000,
            },
        ]
    }
    if not any("stop-guard" in str(h) for h in stop_hooks):
        # Replace existing Stop hooks with the combined guard + compact entry
        hooks["Stop"] = [stop_guard_entry]
        actions.append("Added Stop hook: kin stop-guard + compact-hook")
    else:
        actions.append("Stop hook already installed")

    if not dry_run:
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(data, indent=2) + "\n")
        actions.append(f"Wrote {settings_path}")

    return actions


def install_launchd(config: "Config", dry_run: bool = False) -> list[str]:
    """Install macOS launchd plist for kin cron (every 30 min).

    Creates ~/Library/LaunchAgents/com.kindex.cron.plist
    """
    actions = []
    kin_path = _find_kin_path()
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    plist_path = launch_agents / "com.kindex.cron.plist"
    log_dir = config.data_path / "logs"

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
    <integer>1800</integer>
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
