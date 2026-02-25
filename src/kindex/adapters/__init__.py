"""Kindex adapter system — extensible ingestion from any source.

Built-in adapters: github, linear, commits, files, projects, sessions.
Third-party adapters discovered via entry points (group: kindex.adapters).

Quick start for building an adapter::

    from kindex.adapters.base import AdapterMeta, IngestResult

    class MyAdapter:
        meta = AdapterMeta(name="myservice", description="Ingest from MyService")
        def is_available(self) -> bool: return True
        def ingest(self, store, *, limit=50, since=None, verbose=False, **kw):
            # store.add_node(title="...", content="...", node_type="document")
            return IngestResult(created=1)

    adapter = MyAdapter()
"""

from .base import Adapter, AdapterMeta, AdapterOption, IngestResult
from .registry import available, discover, get, register

__all__ = [
    "Adapter",
    "AdapterMeta",
    "AdapterOption",
    "IngestResult",
    "discover",
    "get",
    "register",
    "available",
]
