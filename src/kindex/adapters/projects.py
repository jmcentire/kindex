"""Projects adapter — scan project directories and .kin files."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import AdapterMeta, IngestResult

if TYPE_CHECKING:
    from ..store import Store


class ProjectsAdapter:
    meta = AdapterMeta(
        name="projects",
        description="Scan project directories and .kin files for knowledge",
    )

    def is_available(self) -> bool:
        return True

    def ingest(self, store, *, limit=50, since=None, verbose=False, **kwargs):
        from ..config import load_config
        from ..ingest import scan_kin_files, scan_projects

        cfg = kwargs.get("_config") or load_config()
        p_count = scan_projects(cfg, store, verbose=verbose)
        k_count = scan_kin_files(cfg, store, verbose=verbose)
        return IngestResult(created=p_count + k_count)


adapter = ProjectsAdapter()
