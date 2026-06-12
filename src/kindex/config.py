"""Configuration loading — finds and merges config from multiple sources."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, PrivateAttr


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


class ProfileEntry(BaseModel):
    """A named graph profile: its own data_dir plus the directory roots
    whose sessions/work route to it."""
    data_dir: str
    roots: list[str] = Field(default_factory=list)


class CollabConfig(BaseModel):
    """Multi-agent collaboration surfaces (conversations, locks, injection)."""
    enabled: bool = True
    display: str = "full"            # full | minimal | quiet
    prompt_cooldown_minutes: int = 10


class AgentOverrideConfig(BaseModel):
    """Behavior overrides scoped to one client family or one client instance."""
    attention: dict[str, Any] = Field(default_factory=dict)
    sim: dict[str, Any] = Field(default_factory=dict)
    collab: dict[str, Any] = Field(default_factory=dict)
    hooks: dict[str, Any] = Field(default_factory=dict)


class AgentInstanceConfig(AgentOverrideConfig):
    """Instance-scoped overrides, optionally tied to a specific client."""
    client: str = ""


class AgentsConfig(BaseModel):
    """Client/instance-specific Kindex behavior overlays.

    Root config remains the global/project default. These overlays only tune
    agent-facing behavior such as injection cadence, display, and hook budgets.
    """
    clients: dict[str, AgentOverrideConfig] = Field(default_factory=dict)
    instances: dict[str, AgentInstanceConfig] = Field(default_factory=dict)


class EmbeddingConfig(BaseModel):
    provider: str = "voyage"     # "voyage", "openai", "gemini", "local"
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


class AttentionConfig(BaseModel):
    enabled: bool = False
    tick_interval: int = 3
    max_candidates: int = 6
    min_confidence: float = 0.65
    display: str = "minimal"     # how reminders render: full | minimal | quiet
                                 # full = header+Source+Reason+budget; minimal = bare
                                 # lines; quiet = feed the model, suppress the user block
    max_context_chars: int = 1800
    max_candidate_chars: int = 500
    max_output_tokens: int = 300
    cooldown_seconds: int = 1800
    max_check_cost: float = 0.01
    max_conversation_cost: float = 0.25
    # Tool calls that are Kindex's own noise or pure inspection — attention never
    # fires on these. Names support fnmatch globs (e.g. "mcp__kindex__*"). Edits,
    # writes, web calls and everything else are real actions and DO fire, so that
    # "when you do X, always Y" reminders can trigger on arbitrary work.
    skip_tools: list[str] = Field(default_factory=lambda: [
        "Read", "Grep", "Glob", "LS", "NotebookRead", "TodoWrite",
        "view_file", "list_dir", "find_by_name", "grep_search", "read_url_content",
        "mcp__kindex__*",
    ])
    # For Bash, attention skips ONLY commands that are purely read-only
    # inspection. Anything else (curl, deploys, edits, arbitrary actions) fires —
    # an allowlist would silently drop reminders for commands we didn't predict.
    readonly_bash_commands: list[str] = Field(default_factory=lambda: [
        "ls", "cat", "head", "tail", "less", "more", "bat", "tac",
        "grep", "egrep", "fgrep", "rg", "ag", "ack", "find", "fd",
        "pwd", "echo", "printf", "which", "type", "whoami", "id",
        "env", "printenv", "wc", "sort", "uniq", "cut", "column", "tr",
        "awk", "stat", "file", "tree", "du", "df", "ps", "date", "cal",
        "uname", "hostname", "cd", "true", "false", "test", "diff",
        "jq", "yq", "xxd", "od", "basename", "dirname", "realpath",
    ])
    # `git` and `kin` are read-only only for these subcommands; any other
    # subcommand (push, commit, index, export, …) is an action and fires.
    readonly_git_subcommands: list[str] = Field(default_factory=lambda: [
        "status", "log", "diff", "show", "branch", "rev-parse", "describe",
        "blame", "ls-files", "shortlog", "reflog", "whatchanged", "remote",
        "config", "stash", "tag",
    ])
    readonly_kin_subcommands: list[str] = Field(default_factory=lambda: [
        "search", "show", "status", "list", "context", "ask", "graph-stats",
        "changelog", "list-nodes", "prime", "policy",
        "coord read", "coord list", "profile list", "profile which", "whoami",
    ])
    # Stigmergic injection pheromone (deposited when a node is injected,
    # reinforced when the agent actually used the injection, decayed over time).
    pheromone_enabled: bool = True       # accumulate trails (ranking use is gated by ranking.pheromone_weight)
    pheromone_deposit: float = 1.0       # laid per injection
    pheromone_reinforce: float = 3.0     # confirmed use of an injection (used ≈ 4× bare)
    pheromone_correction: float = 4.0    # HEAVIEST: a user correction grounds the signal (real ground truth)
    pheromone_counterfactual: float = 1.5  # would-have-helped / agent "I should have…" admission — a signal, not too heavy
    pheromone_half_life_days: float = 14.0   # aggressive: ignored trails die in weeks
    pheromone_min_deposits: int = 5      # conditioned trail must clear this before it overrides the global trail
    # Auto-ramp: lift pheromone into ranking automatically once trails are warm,
    # so users never flip a bit. Measured on GRADED, decayed signal (not bare
    # deposits) so it ramps down when the work moves on. Writes a learned weight
    # to meta; ranking.pheromone_weight (if >0) is a manual override that wins.
    pheromone_autoramp_enabled: bool = True
    pheromone_target_weight: float = 0.12    # mature target weight in the ensemble
    pheromone_min_nodes: int = 8             # distinct warm graded nodes before any ramp
    pheromone_min_signal: float = 12.0       # warm graded strength before any ramp
    pheromone_full_signal: float = 60.0      # warm graded strength at which weight hits target
    # Session-end reinforcement (LLM-grades-the-trace) budget + behavior.
    reinforce_enabled: bool = True
    reinforce_max_cost: float = 0.05     # cap per session-end grading call
    reinforce_min_confidence: float = 0.55  # grader confidence floor to act on a finding
    reinforce_counterfactual_top_k: int = 3  # graph matches considered per missed-opportunity query
    reinforce_gap_as_question: bool = True   # log a knowledge-gap when a real need matches no node


class SimConfig(BaseModel):
    """Optional async Sim (Jeremy-simulacrum) supervisory check-in.

    Sim periodically reviews a conversation WINDOW as a supervisor and, if its
    feedback self-rates at/above `threshold`, the feedback is injected into the
    conversation via the same channel as attention reminders. Opt-in and
    disable-able; the human + threshold is the whole feedback loop (no training).

    Runs OFF the agent's critical path: the prompt-tick only enqueues a window
    snapshot (cheap, SQLite-only); the LLM/Sim spend happens in the daemon drain;
    the next tick picks up any pending injection (cheap) and surfaces it if still
    fresh. Mirrors reinforce.py's queue/drain pattern.
    """
    enabled: bool = False
    tick_interval: int = 6          # enqueue a review roughly every ~6 ticks
    threshold: float = 0.7          # self-rating at/above this injects (0.0-1.0)
    window_chars: int = 12000       # conversation-window size handed to Sim
    grounding_chars: int = 1500     # budget for injected kindex knowledge (concepts +
                                    # constraints/watches) so Sim reviews grounded, not
                                    # blind; 0 disables grounding
    max_review_cost: float = 0.05   # cap per Sim review call
    max_conversation_cost: float = 0.50  # cumulative Sim spend cap per conversation
    max_output_tokens: int = 400
    max_queue: int = 20             # pending reviews retained (dedup by conversation)
    max_stale_ticks: int = 4        # drop a pending injection older than this many ticks
    min_overlap: float = 0.18       # token-overlap floor between reviewed tail and current tail
    deposit_pheromone: bool = True  # lay an injection trail like attention does
    # Supervisor model. Empty = fall back to llm.model (the cheap attention judge,
    # often too weak for a demanding lens). Set a stronger model for real review.
    model: str = ""
    # Self-drain: when no daemon is draining the queue, the prompt tick
    # fire-and-forgets a detached `kin sim drain` so Sim works without cron.
    drain_on_tick: bool = True
    display: str = "minimal"        # how Sim feedback renders: full | minimal | quiet
    # How to invoke Sim. Empty = use the configured LLM client with the
    # supervisor prompt (portable, testable). Set to a shell command that reads
    # the prompt on stdin and writes the response on stdout to wire in the real
    # Jeremy-simulacrum, e.g. "~/.claude/skills/simulacrum/run.py".
    command: str = ""
    command_timeout: int = 60


class RankingConfig(BaseModel):
    rrf_k: int = 30               # RRF smoothing parameter (lower = sharper discrimination)
    fts_weight: float = 0.40      # FTS5 BM25 signal weight
    vector_weight: float = 0.30   # Vector similarity signal weight
    graph_weight: float = 0.15    # Graph expansion signal weight
    node_weight: float = 0.10     # Stored node weight signal
    recency_weight: float = 0.05  # Recency decay signal weight
    pheromone_weight: float = 0.0  # Injection-usefulness signal — opt-in: accumulate trails, then enable once warm

    @property
    def ensemble_weights(self) -> dict[str, float]:
        weights = {
            "fts": self.fts_weight,
            "vector": self.vector_weight,
            "graph": self.graph_weight,
            "node_weight": self.node_weight,
            "recency": self.recency_weight,
        }
        if self.pheromone_weight > 0:
            weights["pheromone"] = self.pheromone_weight
        return weights


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
    # Inject the "use kindex" session directive at prime time (SessionStart hook).
    # On by default so kindex owns this reminder itself (rather than relying on an
    # external session-start injector). Set false per-project in .kin/config
    # [reminders] to suppress the nudge for repos that don't want it.
    remind_kindex_usage: bool = True
    check_interval: int = 300
    default_channels: list[str] = Field(default_factory=lambda: ["system"])
    snooze_duration: int = 900
    auto_snooze_timeout: int = 300
    idle_suppress_after: int = 600
    action_enabled: bool = True        # enable action execution on reminder fire
    stop_guard_enabled: bool = False   # block Claude exit for pending actions (noisy; opt-in)
    dream_on_stop_enabled: bool = True  # run throttled knowledge consolidation when Claude exits
    dream_min_interval: int = 3600      # seconds between scheduled/hook dream runs
    dream_max_new_suggestions: int = 100  # cap suggestion writes per dream run
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
    _project_path: Path | None = PrivateAttr(default=None)
    # Per-pass session routing predicate (set by daemon.cron_run_all and
    # cli.cmd_cron); callable(jsonl_path) -> bool. Never loaded from yaml.
    # When None, ingest builds one from profiles/active_profile (see
    # routing.effective_session_filter).
    _session_filter: Any = PrivateAttr(default=None)
    # False when an explicit --data-dir overrides a profile-resolved
    # data_dir: the store must NOT stamp an unstamped database with the
    # active profile (it still hard-refuses an existing mismatched stamp).
    _stamp_on_open: bool = PrivateAttr(default=True)
    # The pre-activation data_dir, recorded by _activate_profile so the
    # cron legacy-remainder pass can find the legacy graph even when this
    # invocation resolved to a profile.
    _legacy_data_dir: str | None = PrivateAttr(default=None)

    data_dir: str = "~/.kindex"
    user: str = ""  # current user identity (auto-detected if empty)
    project_dirs: list[str] = Field(default_factory=lambda: ["~/Code", "~/Personal"])
    claude_dir: str = "~/.claude"
    codex_dir: str = "~/.codex"
    gemini_dir: str = "~/.gemini"
    antigravity_dir: str = "~/.gemini/config"
    antigravity_cli_dir: str = "~/.gemini/antigravity-cli"
    opencode_dir: str = "~/.config/opencode"
    cursor_dir: str = "~/.cursor"
    agents: AgentsConfig = Field(default_factory=AgentsConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    attention: AttentionConfig = Field(default_factory=AttentionConfig)
    sim: SimConfig = Field(default_factory=SimConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)
    ranking: RankingConfig = Field(default_factory=RankingConfig)
    reminders: ReminderConfig = Field(default_factory=ReminderConfig)
    work_policy: WorkPolicyConfig = Field(default_factory=WorkPolicyConfig)
    # Sequestered multi-profile storage. Profiles live in the GLOBAL kin.yaml:
    #   profiles: {work: {data_dir: ~/.kindex-work, roots: [~/Work]}}
    #   default_profile: personal
    # No profiles configured => byte-identical legacy single-graph behavior.
    profiles: dict[str, ProfileEntry] = Field(default_factory=dict)
    default_profile: str | None = None
    # Stable agent identity for collab/locks (KIN_AGENT_ID env overrides).
    agent_id: str | None = None
    # Per-node-type edit class overrides: {node_type: editable|additive|managed}.
    edit_policy: dict[str, str] = Field(default_factory=dict)
    collab: CollabConfig = Field(default_factory=CollabConfig)
    # Runtime profile resolution result (set by load_config, not yaml input).
    active_profile: str | None = None
    profile_source: str = "legacy"   # flag | env | kin | roots | default | legacy

    @property
    def current_user(self) -> str:
        """Resolve current user identity. Config > git > OS."""
        if self.user:
            return self.user
        return _detect_user(self._project_path)

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
    def antigravity_path(self) -> Path:
        return Path(self.antigravity_dir).expanduser().resolve()

    @property
    def antigravity_cli_path(self) -> Path:
        return Path(self.antigravity_cli_dir).expanduser().resolve()

    @property
    def opencode_path(self) -> Path:
        return Path(self.opencode_dir).expanduser().resolve()

    @property
    def cursor_path(self) -> Path:
        return Path(self.cursor_dir).expanduser().resolve()

    @property
    def resolved_project_dirs(self) -> list[Path]:
        return [Path(d).expanduser().resolve() for d in self.project_dirs]


def _detect_user(project_path: str | Path | None = None) -> str:
    """Auto-detect user identity from repo-local/global git config or OS username."""
    import subprocess

    commands: list[list[str]] = []
    if project_path:
        repo_path = str(Path(project_path).expanduser().resolve())
        # `git config user.name` follows git's normal precedence: local, then global.
        commands.append(["git", "-C", repo_path, "config", "user.name"])
    commands.append(["git", "config", "--global", "user.name"])

    for command in commands:
        try:
            result = subprocess.run(
                command,
                capture_output=True, text=True, timeout=2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip().lower().replace(" ", "-")
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # Fall back to OS username
    return os.environ.get("USER", os.environ.get("USERNAME", "unknown"))


def _attach_project_path(cfg: Config, project_root: Path) -> Config:
    cfg._project_path = project_root
    return cfg


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
    profile: str | None = None,
) -> Config:
    """Load config with layered merging: code defaults → global → local.

    Like git config: global (~/.config/kindex/kin.yaml) is loaded first,
    then local (.kin/config / kin.yaml / conv.yaml in the current project)
    merges over it. Project resolution is explicit path, KIN_PROJECT, git
    worktree root, then cwd.
    An explicit config_path bypasses layering and loads only that file.

    Profile resolution (only when profiles are configured OR an explicit
    profile/env is given): explicit `profile` param > KIN_PROFILE env >
    `profile:` key from the .kin chain > longest-prefix cwd match against
    profile roots > default_profile > legacy (active_profile stays None and
    data_dir is untouched).
    """
    project_root = resolve_project_root(project_path)

    if config_path:
        p = Path(config_path).expanduser().resolve()
        if p.exists():
            data = yaml.safe_load(p.read_text()) or {}
            kin_profile = data.pop("profile", None)
            cfg = _resolve_profile(Config(**data), profile, kin_profile)
            return _attach_project_path(cfg, project_root)
        return _attach_project_path(_resolve_profile(Config(), profile, None),
                                    project_root)

    # Layer 1: global config (user-level)
    merged: dict = {}
    for p in _GLOBAL_PATHS:
        p = p.expanduser().resolve()
        if p.is_file():
            data = yaml.safe_load(p.read_text()) or {}
            merged = _deep_merge(merged, data)
            break  # use first global found

    project_layers = _project_config_paths(project_root)

    # Layer 2: local config (project-level) merges over global
    kin_profile = merged.pop("profile", None)
    for p in project_layers:
        if p.is_file():
            data = _load_kin_config_with_inheritance(p)
            if "profile" in data:
                kin_profile = data.pop("profile")
            merged = _deep_merge(merged, data)
            break  # use first local found

    cfg = Config(**merged) if merged else Config()
    cfg = _resolve_profile(cfg, profile, kin_profile)
    return _attach_project_path(cfg, project_root)


def _resolve_profile(cfg: Config, explicit: str | None,
                     kin_profile: str | None) -> Config:
    """Resolve the active profile on a freshly loaded Config (in place).

    No profiles configured AND no explicit/env request => legacy single-graph
    passthrough: active_profile stays None, data_dir untouched.
    Any explicit reference to an unknown profile raises ValueError.
    """
    env_profile = os.environ.get("KIN_PROFILE") or None
    if not cfg.profiles and not explicit and not env_profile:
        return cfg  # legacy: byte-identical to pre-profile behavior

    # Explicit tiers: flag > env > .kin chain key
    for name, source in ((explicit, "flag"), (env_profile, "env"),
                         (kin_profile, "kin")):
        if name:
            return _activate_profile(cfg, str(name), source)

    # Roots tier: longest-prefix match of cwd against any profile's roots
    try:
        cwd = Path.cwd().resolve()
    except OSError:
        cwd = None
    if cwd is not None:
        best: tuple[int, str] | None = None
        for name, entry in cfg.profiles.items():
            for root in entry.roots:
                rp = Path(root).expanduser()
                try:
                    rp = rp.resolve()
                except OSError:
                    continue
                if cwd == rp or rp in cwd.parents:
                    plen = len(str(rp))
                    if best is None or plen > best[0]:
                        best = (plen, name)
        if best is not None:
            return _activate_profile(cfg, best[1], "roots")

    if cfg.default_profile:
        return _activate_profile(cfg, cfg.default_profile, "default")

    return cfg  # profiles exist but nothing matched -> legacy passthrough


def _activate_profile(cfg: Config, name: str, source: str) -> Config:
    if name not in cfg.profiles:
        known = ", ".join(sorted(cfg.profiles)) or "(none)"
        raise ValueError(
            f"Unknown kindex profile '{name}' (from {source}); "
            f"known profiles: {known}"
        )
    cfg._legacy_data_dir = cfg.data_dir
    cfg.data_dir = str(Path(cfg.profiles[name].data_dir).expanduser())
    cfg.active_profile = name
    cfg.profile_source = source
    return cfg


def resolve_agent_id(config: Config) -> str:
    """Stable agent identity for collab/locks/claims.

    Precedence: KIN_AGENT_ID env > config.agent_id > user@shorthost.
    """
    import socket

    env = os.environ.get("KIN_AGENT_ID")
    if env:
        return env
    configured = getattr(config, "agent_id", None)
    if configured:
        return configured
    host = socket.gethostname().split(".")[0]
    return f"{config.current_user}@{host}"


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
