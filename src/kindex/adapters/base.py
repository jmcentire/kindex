"""Adapter protocol for kindex ingestion system.

All adapters — built-in or third-party — satisfy the Adapter protocol.
A new adapter is one file, one class, three methods.

Example::

    from kindex.adapters.base import AdapterMeta, AdapterOption, IngestResult

    class MyAdapter:
        meta = AdapterMeta(
            name="myservice",
            description="Ingest from MyService API",
            requires_auth=True,
            auth_hint="Set MYSERVICE_API_KEY env var",
            options=[AdapterOption("project", "Project key", required=True)],
        )

        def is_available(self) -> bool:
            import os
            return bool(os.environ.get("MYSERVICE_API_KEY"))

        def ingest(self, store, *, limit=50, since=None, verbose=False, **kwargs):
            # ... create nodes via store.add_node() ...
            return IngestResult(created=10)

    adapter = MyAdapter()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..store import Store


@dataclass
class AdapterOption:
    """CLI option an adapter accepts (e.g. --repo, --team)."""

    name: str
    description: str
    required: bool = False
    default: Any = None


@dataclass
class AdapterMeta:
    """Adapter metadata for discovery and CLI help."""

    name: str
    description: str
    version: str = "0.1.0"
    requires_auth: bool = False
    auth_hint: str = ""
    options: list[AdapterOption] = field(default_factory=list)


@dataclass
class IngestResult:
    """What an adapter returns after ingestion."""

    created: int = 0
    updated: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.created + self.updated

    def __str__(self) -> str:
        parts = []
        if self.created:
            parts.append(f"{self.created} created")
        if self.updated:
            parts.append(f"{self.updated} updated")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return ", ".join(parts) if parts else "no changes"


@runtime_checkable
class Adapter(Protocol):
    """Protocol that all kindex adapters must satisfy.

    Uses structural subtyping — adapters don't need to import or inherit
    from this class. Just implement the same shape.
    """

    meta: AdapterMeta

    def is_available(self) -> bool:
        """Check if adapter prerequisites are met (CLI tools, API keys, etc.)."""
        ...

    def ingest(
        self,
        store: Store,
        *,
        limit: int = 50,
        since: str | None = None,
        verbose: bool = False,
        **kwargs: Any,
    ) -> IngestResult:
        """Ingest data into the knowledge graph.

        Args:
            store: Kindex Store instance for creating nodes/edges.
            limit: Maximum items to ingest.
            since: ISO date string — only ingest items after this date.
            verbose: Print progress to stdout.
            **kwargs: Adapter-specific options (repo, team, path, etc.).

        Returns:
            IngestResult with created/updated/skipped counts.
        """
        ...
