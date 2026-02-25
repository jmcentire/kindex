"""Shared ingestion pipeline — wraps adapter execution with common config.

Provides a uniform entry point for running any adapter with shared
constraints (date filters, dry-run mode, verbose output).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .base import IngestResult

if TYPE_CHECKING:
    from .base import Adapter
    from ..store import Store


@dataclass
class IngestConfig:
    """Shared ingestion constraints applied to all adapters."""

    since: str | None = None
    limit: int = 50
    dry_run: bool = False
    verbose: bool = False
    domain_overrides: list[str] | None = None


def run_adapter(
    adapter: Adapter,
    store: Store,
    config: IngestConfig | None = None,
    **kwargs: Any,
) -> IngestResult:
    """Execute an adapter with shared pipeline wrapping.

    1. Check availability
    2. Run the adapter's ingest() method
    3. Report results

    Args:
        adapter: The adapter to run.
        store: Kindex Store instance.
        config: Shared ingestion configuration.
        **kwargs: Adapter-specific options (repo, team, path, etc.).

    Returns:
        IngestResult with created/updated/skipped/error counts.
    """
    if config is None:
        config = IngestConfig()

    name = adapter.meta.name

    # Check prerequisites
    if not adapter.is_available():
        hint = adapter.meta.auth_hint or "prerequisites not met"
        msg = f"{name}: not available ({hint})"
        if config.verbose:
            print(msg, file=sys.stderr)
        return IngestResult(errors=[msg])

    if config.dry_run:
        if config.verbose:
            print(f"[dry-run] Would run adapter: {name}")
        return IngestResult()

    if config.verbose:
        print(f"Ingesting from {name}...")

    # Run the adapter
    try:
        result = adapter.ingest(
            store,
            limit=config.limit,
            since=config.since,
            verbose=config.verbose,
            **kwargs,
        )
    except Exception as e:
        msg = f"{name}: {type(e).__name__}: {e}"
        if config.verbose:
            print(f"  Error: {msg}", file=sys.stderr)
        return IngestResult(errors=[msg])

    if config.verbose:
        print(f"  {name}: {result}")

    return result


def run_all(
    store: Store,
    config: IngestConfig | None = None,
    **kwargs: Any,
) -> dict[str, IngestResult]:
    """Run all available adapters and return per-adapter results."""
    from .registry import discover

    if config is None:
        config = IngestConfig()

    results: dict[str, IngestResult] = {}
    for name, adapter in sorted(discover().items()):
        if adapter.is_available():
            results[name] = run_adapter(adapter, store, config, **kwargs)

    return results
