"""Configuration loading — finds and merges config from multiple sources."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


_SEARCH_PATHS = [
    Path(".kin"),                                      # cwd (repo-local)
    Path("kin.yaml"),                                  # cwd (explicit)
    Path("conv.yaml"),                                 # legacy
    Path.home() / ".config" / "kindex" / "kin.yaml",  # XDG-ish
    Path.home() / ".config" / "conv" / "conv.yaml",   # legacy
]


class LLMConfig(BaseModel):
    enabled: bool = False
    provider: str = "anthropic"
    model: str = "claude-haiku-4-5-20251001"
    api_key_env: str = "ANTHROPIC_API_KEY"


class BudgetConfig(BaseModel):
    daily: float = 0.50
    weekly: float = 2.00
    monthly: float = 5.00


class DefaultsConfig(BaseModel):
    hops: int = 2
    min_weight: float = 0.1
    mode: str = "bfs"


class Config(BaseModel):
    data_dir: str = "~/.kindex"
    project_dirs: list[str] = Field(default_factory=lambda: ["~/Code", "~/Personal", "~/WanderRepos"])
    claude_dir: str = "~/.claude"
    llm: LLMConfig = Field(default_factory=LLMConfig)
    budget: BudgetConfig = Field(default_factory=BudgetConfig)
    defaults: DefaultsConfig = Field(default_factory=DefaultsConfig)

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


def load_config(config_path: str | Path | None = None) -> Config:
    """Load config from explicit path, or search standard locations."""
    paths = [Path(config_path)] if config_path else _SEARCH_PATHS

    for p in paths:
        p = p.expanduser().resolve()
        if p.exists():
            data = yaml.safe_load(p.read_text()) or {}
            return Config(**data)

    return Config()
