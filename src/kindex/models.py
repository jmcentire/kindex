"""Pydantic models for the Conv knowledge graph."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, PrivateAttr, field_validator


class DurableSourceRef(BaseModel):
    """Durable evidence locator, never an authorization handle for writes."""

    version: Literal[1] = 1
    node_id: str = Field(min_length=1)
    graph_id: UUID
    db_path: str
    project_path: str = ""
    profile: str = ""

    @field_validator("db_path")
    @classmethod
    def absolute_locator(cls, value: str) -> str:
        if not Path(value).is_absolute():
            raise ValueError("Source database locator must be absolute")
        return value


class Edge(BaseModel):
    """A weighted, annotated connection between two nodes."""

    target: str
    weight: float = 0.5
    reason: str = ""


class TopicNode(BaseModel, extra="allow"):
    """A knowledge graph node parsed from a topic markdown file.

    Uses extra='allow' so domain-specific fields (date_filed, application_number,
    etc.) survive round-trip without data loss.
    """

    topic: str = ""
    title: str = ""
    standing: str = "unruled"
    weight: float = 0.0
    domains: list[str] = Field(default_factory=list)
    status: str = ""
    connects_to: list[Edge] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)

    # Internal — set by Vault after parsing, excluded from serialization
    body: str = Field(default="", exclude=True)
    path: Path | None = Field(default=None, exclude=True)
    slug: str = Field(default="", exclude=True)
    _has_frontmatter: bool = PrivateAttr(default=True)

    @property
    def has_frontmatter(self) -> bool:
        return self._has_frontmatter

    def edge_to(self, target: str) -> Edge | None:
        for edge in self.connects_to:
            if edge.target == target:
                return edge
        return None

    def frontmatter_dict(self) -> dict[str, Any]:
        """Return dict for YAML serialization (excludes body/path/slug)."""
        d = self.model_dump(exclude={"body", "path", "slug"}, exclude_none=True)
        if "connects_to" in d:
            d["connects_to"] = [
                {k: v for k, v in e.items() if v} for e in d["connects_to"]
            ]
        for key, value in (self.__pydantic_extra__ or {}).items():
            d[key] = value
        return d


class SkillNode(BaseModel):
    """A skill or ability — first-class graph citizen."""

    skill: str = ""
    title: str = ""
    level: str = ""  # e.g. "expert", "proficient", "learning"
    domains: list[str] = Field(default_factory=list)
    connects_to: list[Edge] = Field(default_factory=list)
    evidence: list[str] = Field(default_factory=list)  # sessions/projects that demonstrate this

    # Internal
    body: str = Field(default="", exclude=True)
    path: Path | None = Field(default=None, exclude=True)
    slug: str = Field(default="", exclude=True)

    def frontmatter_dict(self) -> dict[str, Any]:
        d = self.model_dump(exclude={"body", "path", "slug"}, exclude_none=True)
        if "connects_to" in d:
            d["connects_to"] = [
                {k: v for k, v in e.items() if v} for e in d["connects_to"]
            ]
        return d


class InboxItem(BaseModel):
    """A queued discovery from a Claude session."""

    content: str
    source: str = ""
    timestamp: str = ""
    topic_hint: str = ""
    skill_hint: str = ""
    processed: bool = False
