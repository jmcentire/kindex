"""Example kindex adapter — ingest knowledge from a CSV file.

This is a minimal, working adapter that demonstrates the protocol.
Use it as a starting point for building custom adapters.

Install:
    pip install -e examples/adapter-template

Then:
    kin ingest csv --path data.csv
"""

from __future__ import annotations

import csv
from typing import TYPE_CHECKING, Any

from kindex.adapters.base import AdapterMeta, AdapterOption, IngestResult

if TYPE_CHECKING:
    from kindex.store import Store


class CSVAdapter:
    """Ingest knowledge nodes from a CSV file.

    Expected CSV columns: title, content, type (optional).
    Any extra columns are stored in the node's extra dict.
    """

    meta = AdapterMeta(
        name="csv",
        description="Ingest knowledge from a CSV file",
        options=[
            AdapterOption("path", "Path to CSV file", required=True),
        ],
    )

    def is_available(self) -> bool:
        """CSV adapter has no external dependencies."""
        return True

    def ingest(
        self,
        store: Store,
        *,
        limit: int = 50,
        since: str | None = None,
        verbose: bool = False,
        **kwargs: Any,
    ) -> IngestResult:
        path = kwargs.get("path")
        if not path:
            return IngestResult(errors=["--path required for csv adapter"])

        created = 0
        skipped = 0

        with open(path, newline="", encoding="utf-8") as f:
            for i, row in enumerate(csv.DictReader(f)):
                if i >= limit:
                    break

                title = row.get("title", f"row-{i}")
                content = row.get("content", "")
                node_type = row.get("type", "concept")

                # Check for duplicates
                node_id = f"csv-{title.lower().replace(' ', '-')[:40]}"
                if store.get_node(node_id):
                    skipped += 1
                    continue

                # Extra columns become metadata
                extra = {k: v for k, v in row.items()
                         if k not in ("title", "content", "type") and v}

                store.add_node(
                    node_id=node_id,
                    title=title,
                    content=content,
                    node_type=node_type,
                    prov_activity="csv-ingest",
                    prov_source=path,
                    extra=extra or None,
                )
                created += 1

                if verbose:
                    print(f"  {node_type}: {title}")

        return IngestResult(created=created, skipped=skipped)


# This is what the entry point loads
adapter = CSVAdapter()
