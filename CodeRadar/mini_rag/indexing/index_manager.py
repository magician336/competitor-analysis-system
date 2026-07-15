"""Versioned index lifecycle and atomic read-alias switching."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from mini_rag.embedding import EmbeddingService

from .elasticsearch_client import SearchBackend
from .index_builder import IndexBuilder, IndexingReport
from .index_mapping import build_index_mapping


class IndexManager:
    """Manage physical index versions behind one stable read alias."""

    def __init__(
        self,
        backend: SearchBackend,
        *,
        base_name: str = "coderadar_chunks",
        alias: str = "coderadar_chunks_current",
        shards: int = 1,
        replicas: int = 0,
    ) -> None:
        if shards < 1 or replicas < 0:
            raise ValueError("invalid shard or replica count")
        self.backend = backend
        self.base_name = base_name.rstrip("_-")
        self.alias = alias
        self.shards = shards
        self.replicas = replicas

    def versioned_name(self, version: str | None = None) -> str:
        suffix = version or datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        safe_suffix = "".join(char for char in suffix.casefold() if char.isalnum() or char in "-_")
        if not safe_suffix:
            raise ValueError("index version must contain an alphanumeric character")
        return f"{self.base_name}_v{safe_suffix}"

    def create(
        self,
        embedding_service: EmbeddingService,
        *,
        version: str | None = None,
    ) -> str:
        index_name = self.versioned_name(version)
        self.backend.create_index(
            index_name,
            build_index_mapping(
                embedding_service.dimension,
                embedding_model=embedding_service.model_name,
                shards=self.shards,
                replicas=self.replicas,
            ),
        )
        return index_name

    def activate(self, index_name: str) -> None:
        self.backend.swap_alias(self.alias, index_name)

    def current_index(self) -> str | None:
        return self.backend.resolve_alias(self.alias)

    def rebuild(
        self,
        chunks: Iterable[Any],
        embedding_service: EmbeddingService,
        *,
        version: str | None = None,
        activate: bool = True,
    ) -> IndexingReport:
        index_name = self.create(embedding_service, version=version)
        builder = IndexBuilder(self.backend, embedding_service, index_name)
        report = builder.build(chunks, incremental=False, refresh=True)
        report.created_index = True
        if activate and report.ok:
            self.activate(index_name)
            report.alias = self.alias
        return report

    def status(self) -> dict[str, Any]:
        current = self.current_index()
        return {
            "alias": self.alias,
            "index": current,
            "document_count": self.backend.count(current) if current else 0,
            "backend_health": self.backend.health() if hasattr(self.backend, "health") else {},
        }


__all__ = ["IndexManager"]
