"""Understand-Anything graph adapter."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import AdapterMeta, AdapterOption, IngestResult

if TYPE_CHECKING:
    from ..store import Store


class UnderstandAnythingAdapter:
    meta = AdapterMeta(
        name="understand-anything",
        description="Ingest .understand-anything/knowledge-graph.json code maps",
        options=[
            AdapterOption("directory", "Repository directory or knowledge-graph.json path", required=True),
        ],
    )

    def is_available(self) -> bool:
        return True

    def ingest(
        self,
        store: "Store",
        *,
        limit: int = 10000,
        since: str | None = None,
        verbose: bool = False,
        **kwargs: Any,
    ) -> IngestResult:
        directory = kwargs.get("directory")
        if not directory:
            return IngestResult(errors=["--directory is required"])

        from ..code_map import ingest_understand_anything

        return ingest_understand_anything(
            store,
            Path(directory),
            limit=limit,
            verbose=verbose,
        )


adapter = UnderstandAnythingAdapter()
