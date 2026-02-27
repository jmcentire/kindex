"""Configuration loading — finds and merges config from multiple sources."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


# Config layers, loaded bottom-up and merged (like git config).
# Global (user-level) is loaded first, then local (project-level) overrides.
_GLOBAL_PATHS = [
    Path.home() / ".config" / "kindex" / "kin.yaml",  # XDG-ish
    Path.home() / ".config" / "conv" / "conv.yaml",   # legacy
]
_LOCAL_PATHS = [
    Path(".kin"),                                      # cwd (repo-local)
    Path("kin.yaml"),                                  # cwd (explicit)
    Path("conv.yaml"),                                 # legacy
]
# Flat list for backward compat (used by config set to find first existing)
_SEARCH_PATHS = _LOCAL_PATHS + _GLOBAL_PATHS


class LLMConfig(BaseModel):
    enabled: bool = False
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    api_key_env: str = "ANTHROPIC_API_KEY"
    cache_control: bool = True
    codebook_min_weight: float = 0.5
    tier2_max_tokens: int = 4000


class BudgetConfig(BaseModel):
    daily: float = 0.50
    weekly: float = 2.00
    monthly: float = 5.00


class DefaultsConfig(BaseModel):
    hops: int = 2
    min_weight: float = 0.1
    mode: str = "bfs"


class SystemChannelConfig(BaseModel):
    enabled: bool = True
    sound: str = "default"


class SlackChannelConfig(BaseModel):
    enabled: bool = False
    webhook_url: str = ""


class EmailChannelConfig(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_pass_keychain: str = ""
    from_addr: str = ""
    to_addr: str = ""


class ClaudeChannelConfig(BaseModel):
    enabled: bool = True
    headless_model: str = ""          # model for claude -p; empty = Claude default
    max_budget_usd: float = 0.50      # spending cap per headless invocation


class ChannelsConfig(BaseModel):
    system: SystemChannelConfig = Field(default_factory=SystemChannelConfig)
    slack: SlackChannelConfig = Field(default_factory=SlackChannelConfig)
    email: EmailChannelConfig = Field(default_factory=EmailChannelConfig)
    claude: ClaudeChannelConfig = Field(default_factory=ClaudeChannelConfig)


class ReminderConfig(BaseModel):
    enabled: bool = True
    check_interval: int = 300
    default_channels: list[str] = Field(default_factory=lambda: ["system"])
    snooze_duration: int = 900
    auto_snooze_timeout: int = 300
    idle_suppress_after: int = 600
    action_enabled: bool = True        # enable action execution on reminder fire
    stop_guard_window: int = 7200      # seconds (2h) — block exit if actions due within
    channels: ChannelsConfig = Field(default_factory=ChannelsConfig)


class Config(BaseModel):
    data_dir: str = "~/.kindex"
    user: str = ""  # current user identity (auto-detected if empty)
    project_dirs: list[str] = Field(default_factory=lambda: ["~/Code", "~/Personal"])
    claude_dir: str = "~/.claude"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    reminders: ReminderConfig = Field(default_factory=ReminderConfig)

    @property
    def current_user(self) -> str:
        """Resolve current user identity. Config > git > OS."""
        if self.user:
            return self.user
        return _detect_user()

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir).expanduser().resolve()

    @property
    def topics_dir(self) -> Path:
        return self.data_path / "topics"

    @property
    def skills_dir(self) -> Path:
        return self.data_path / "skills"

    @property
    def inbox_dir(self) -> Path:
        return self.data_path / "inbox"

    @property
    def ledger_path(self) -> Path:
        return self.data_path / "budget.yaml"

    @property
    def tmp_dir(self) -> Path:
        return self.data_path / ".tmp"

    @property
    def claude_path(self) -> Path:
        return Path(self.claude_dir).expanduser().resolve()

    @property
    def resolved_project_dirs(self) -> list[Path]:
        return [Path(d).expanduser().resolve() for d in self.project_dirs]


def _detect_user() -> str:
    """Auto-detect user identity from git config or OS username."""
    import subprocess
    # Try git config
    try:
        result = subprocess.run(
            ["git", "config", "--global", "user.name"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip().lower().replace(" ", "-")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    # Fall back to OS username
    return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base (override wins)."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def load_config(config_path: str | Path | None = None) -> Config:
    """Load config with layered merging: code defaults → global → local.

    Like git config: global (~/.config/kindex/kin.yaml) is loaded first,
    then local (.kin / kin.yaml / conv.yaml in cwd) merges over it.
    An explicit config_path bypasses layering and loads only that file.
    """
    if config_path:
        p = Path(config_path).expanduser().resolve()
        if p.exists():
            data = yaml.safe_load(p.read_text()) or {}
            return Config(**data)
        return Config()

    # Layer 1: global config (user-level)
    merged: dict = {}
    for p in _GLOBAL_PATHS:
        p = p.expanduser().resolve()
        if p.exists():
            data = yaml.safe_load(p.read_text()) or {}
            merged = _deep_merge(merged, data)
            break  # use first global found

    # Layer 2: local config (project-level) merges over global
    for p in _LOCAL_PATHS:
        p = p.expanduser().resolve()
        if p.exists():
            data = yaml.safe_load(p.read_text()) or {}
            merged = _deep_merge(merged, data)
            break  # use first local found

    return Config(**merged) if merged else Config()
