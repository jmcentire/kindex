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
    Path(".kin") / "config",                           # cwd (repo-local, .kin/ directory)
    Path("kin.yaml"),                                  # cwd (explicit)
    Path("conv.yaml"),                                 # legacy
]
# Flat list for backward compat (used by config set to find first existing)
_SEARCH_PATHS = _LOCAL_PATHS + _GLOBAL_PATHS


class EmbeddingConfig(BaseModel):
    provider: str = "local"      # "local", "openai", "gemini"
    model: str = ""              # empty = provider default
    api_key_env: str = ""        # empty = provider default env var
    dimensions: int = 0          # 0 = provider default


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


class RankingConfig(BaseModel):
    rrf_k: int = 30               # RRF smoothing parameter (lower = sharper discrimination)
    fts_weight: float = 0.40      # FTS5 BM25 signal weight
    vector_weight: float = 0.30   # Vector similarity signal weight
    graph_weight: float = 0.15    # Graph expansion signal weight
    node_weight: float = 0.10     # Stored node weight signal
    recency_weight: float = 0.05  # Recency decay signal weight

    @property
    def ensemble_weights(self) -> dict[str, float]:
        return {
            "fts": self.fts_weight,
            "vector": self.vector_weight,
            "graph": self.graph_weight,
            "node_weight": self.node_weight,
            "recency": self.recency_weight,
        }


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


class TelegramChannelConfig(BaseModel):
    enabled: bool = False
    bot_token: str = ""           # from @BotFather
    chat_id: str = ""             # user or group chat ID
    bot_token_keychain: str = ""  # macOS Keychain service name (alternative to plaintext)


class ChannelsConfig(BaseModel):
    system: SystemChannelConfig = Field(default_factory=SystemChannelConfig)
    slack: SlackChannelConfig = Field(default_factory=SlackChannelConfig)
    email: EmailChannelConfig = Field(default_factory=EmailChannelConfig)
    claude: ClaudeChannelConfig = Field(default_factory=ClaudeChannelConfig)
    telegram: TelegramChannelConfig = Field(default_factory=TelegramChannelConfig)


class ScheduleTier(BaseModel):
    threshold: int   # seconds until nearest reminder
    interval: int    # check interval to use


_DEFAULT_TIERS = [
    ScheduleTier(threshold=604800, interval=86400),   # > 7 days -> daily
    ScheduleTier(threshold=86400, interval=3600),     # > 1 day -> hourly
    ScheduleTier(threshold=3600, interval=600),       # > 1 hour -> 10 min
    ScheduleTier(threshold=0, interval=300),          # <= 1 hour -> 5 min
]


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
    adaptive_scheduling: bool = True   # dynamically adjust cron interval
    min_interval: int = 300            # floor for adaptive scheduling (5 min)
    schedule_tiers: list[ScheduleTier] = Field(default_factory=lambda: list(_DEFAULT_TIERS))


class LinearPolicyConfig(BaseModel):
    enabled: bool = False
    require_issue: bool = False
    team: str = ""


class GitPolicyConfig(BaseModel):
    block_commit_without_tag: bool = False
    block_commit_without_linear: bool = False
    block_push_without_tag: bool = False
    block_push_without_linear: bool = False


class WorkPolicyConfig(BaseModel):
    require_active_tag: bool = False
    linear: LinearPolicyConfig = Field(default_factory=LinearPolicyConfig)
    git: GitPolicyConfig = Field(default_factory=GitPolicyConfig)


class Config(BaseModel):
    data_dir: str = "~/.kindex"
    user: str = ""  # current user identity (auto-detected if empty)
    project_dirs: list[str] = Field(default_factory=lambda: ["~/Code", "~/Personal"])
    claude_dir: str = "~/.claude"
    codex_dir: str = "~/.codex"
    gemini_dir: str = "~/.gemini"
    opencode_dir: str = "~/.config/opencode"
    cursor_dir: str = "~/.cursor"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    reminders: ReminderConfig = Field(default_factory=ReminderConfig)
    work_policy: WorkPolicyConfig = Field(default_factory=WorkPolicyConfig)

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
    def codex_path(self) -> Path:
        return Path(self.codex_dir).expanduser().resolve()

    @property
    def gemini_path(self) -> Path:
        return Path(self.gemini_dir).expanduser().resolve()

    @property
    def opencode_path(self) -> Path:
        return Path(self.opencode_dir).expanduser().resolve()

    @property
    def cursor_path(self) -> Path:
        return Path(self.cursor_dir).expanduser().resolve()

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


def load_config(
    config_path: str | Path | None = None,
    project_path: str | Path | None = None,
) -> Config:
    """Load config with layered merging: code defaults → global → local.

    Like git config: global (~/.config/kindex/kin.yaml) is loaded first,
    then local (.kin/config / kin.yaml / conv.yaml in the current project)
    merges over it. Project resolution is explicit path, KIN_PROJECT, git
    worktree root, then cwd.
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
        if p.is_file():
            data = yaml.safe_load(p.read_text()) or {}
            merged = _deep_merge(merged, data)
            break  # use first global found

    project_root = resolve_project_root(project_path)
    project_layers = _project_config_paths(project_root)

    # Layer 2: local config (project-level) merges over global
    for p in project_layers:
        if p.is_file():
            data = _load_kin_config_with_inheritance(p)
            merged = _deep_merge(merged, data)
            break  # use first local found

    return Config(**merged) if merged else Config()


def resolve_project_root(project_path: str | Path | None = None) -> Path:
    """Resolve the project root for config/policy lookup.

    Resolution order:
    1. explicit project_path
    2. KIN_PROJECT
    3. git worktree root for cwd
    4. cwd
    """
    raw = project_path or os.environ.get("KIN_PROJECT")
    start = Path(raw).expanduser() if raw else Path.cwd()
    start = start.resolve()
    if start.is_file():
        start = start.parent

    git_root = _git_root(start)
    return git_root or start


def _git_root(start: Path) -> Path | None:
    import subprocess
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if result.returncode == 0 and result.stdout.strip():
        return Path(result.stdout.strip()).resolve()
    return None


def _project_config_paths(project_root: Path) -> list[Path]:
    # Prefer git/project root, then parent .kin/config walk for non-git trees,
    # then legacy cwd-local files for backward compatibility.
    candidates: list[Path] = [
        project_root / ".kin" / "config",
        project_root / "kin.yaml",
        project_root / "conv.yaml",
    ]

    current = project_root
    for _ in range(10):
        kin_entry = current / ".kin"
        if kin_entry.is_file():
            upgraded = _maybe_upgrade_kin_file(kin_entry)
            if upgraded:
                candidates.append(upgraded)
        elif kin_entry.is_dir():
            candidates.append(kin_entry / "config")
        parent = current.parent
        if parent == current:
            break
        current = parent

    seen: set[Path] = set()
    out: list[Path] = []
    for path in candidates:
        resolved = path.expanduser().resolve()
        if resolved not in seen:
            seen.add(resolved)
            out.append(resolved)
    return out


def _load_kin_config_with_inheritance(path: Path) -> dict:
    """Load a .kin config, resolving inherits and merging ancestors."""
    if path.name != "config" or path.parent.name != ".kin":
        return yaml.safe_load(path.read_text()) or {}

    chain = _resolve_kin_chain(path)
    return _merge_kin_chain(chain)


def _resolve_kin_chain(path: Path, remaining: int = 5, seen: set[str] | None = None) -> list[dict]:
    seen = seen or set()
    resolved = resolve_kin_config(path)
    key = str(resolved)
    if remaining <= 0 or key in seen or not resolved.is_file():
        return []
    seen.add(key)

    data = yaml.safe_load(resolved.read_text()) or {}
    data["_source"] = key
    chain = [data]

    for parent_ref in data.get("inherits", []):
        parent = (resolved.parent / parent_ref).expanduser().resolve()
        chain.extend(_resolve_kin_chain(parent, remaining - 1, seen))
    return chain


def _merge_kin_chain(chain: list[dict]) -> dict:
    merged: dict = {}
    for layer in reversed(chain):
        clean = {k: v for k, v in layer.items() if not k.startswith("_") and k != "inherits"}
        merged = _deep_merge_with_lists(merged, clean)
    return merged


def _deep_merge_with_lists(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge_with_lists(merged[key], val)
        elif key in merged and isinstance(merged[key], list) and isinstance(val, list):
            seen: set[str] = set()
            items = []
            for item in merged[key] + val:
                marker = str(item)
                if marker not in seen:
                    seen.add(marker)
                    items.append(item)
            merged[key] = items
        else:
            merged[key] = val
    return merged


def _maybe_upgrade_kin_file(path: Path) -> Path | None:
    """If path is a plain file named .kin, migrate it to .kin/config.

    Returns the new config path, or None if no upgrade was needed.
    """
    if not path.is_file() or path.name != ".kin":
        return None
    try:
        content = path.read_bytes()
        path.unlink()
        kin_dir = path.parent / ".kin"
        kin_dir.mkdir(exist_ok=True)
        config_path = kin_dir / "config"
        config_path.write_bytes(content)
        return config_path
    except OSError:
        return None


def resolve_kin_config(path: Path) -> Path:
    """Resolve a .kin reference to the actual config file.

    Handles both old-style (.kin file) and new-style (.kin/config).
    Auto-upgrades old files on discovery.
    """
    path = path.expanduser().resolve()
    if path.is_file():
        if path.name == ".kin":
            upgraded = _maybe_upgrade_kin_file(path)
            return upgraded if upgraded else path
        return path
    if path.is_dir() and path.name == ".kin":
        return path / "config"
    return path
