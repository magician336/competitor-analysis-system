"""Incremental synchronization facade."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .index_builder import IndexBuilder, IndexingReport


class IncrementalIndexer:
    """Synchronize changed chunks while optionally removing stale IDs."""

    def __init__(self, builder: IndexBuilder) -> None:
        self.builder = builder

    def sync(
        self,
        chunks: Iterable[Any],
        *,
        delete_missing: bool = False,
        refresh: bool = True,
    ) -> IndexingReport:
        return self.builder.build(
            chunks,
            incremental=True,
            delete_missing=delete_missing,
            refresh=refresh,
        )

    def index(self, chunks: Iterable[Any], **kwargs: Any) -> IndexingReport:
        return self.sync(chunks, **kwargs)


__all__ = ["IncrementalIndexer"]
