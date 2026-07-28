"""Full and incremental chunk indexing."""

from __future__ import annotations

import hashlib
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from itertools import islice
from typing import Any

from mini_rag.embedding import EmbeddingService

from .elasticsearch_client import BulkResult, SearchBackend
from .index_mapping import build_index_mapping, embedding_metadata


@dataclass
class IndexingReport:
    """Deterministic accounting for one indexing operation."""

    index_name: str
    input_count: int
    indexed_count: int = 0
    skipped_count: int = 0
    deleted_count: int = 0
    failed_count: int = 0
    embedding_model: str = ""
    embedding_dimension: int = 0
    created_index: bool = False
    alias: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed_count == 0

    @property
    def changed_count(self) -> int:
        return self.indexed_count + self.deleted_count

    def model_dump(self, **_: Any) -> dict[str, Any]:
        """Pydantic-style serialization convenience for API code."""

        return asdict(self)


def _plain(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        parsed = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(item) for item in value]
    return value


def chunk_to_document(chunk: Any) -> dict[str, Any]:
    """Serialize a Chunk or compatible mapping for backend storage."""

    if hasattr(chunk, "model_dump"):
        raw = chunk.model_dump(mode="python")
    elif isinstance(chunk, Mapping):
        raw = dict(chunk)
    else:
        raw = dict(vars(chunk))
    raw.pop("source_locator", None)
    document = _plain(raw)
    identifier = str(document.get("chunk_id") or "").strip()
    if not identifier:
        raise ValueError("chunk_id is required for indexing")
    content = str(document.get("content") or "")
    if not content.strip():
        raise ValueError(f"chunk {identifier} has empty content")
    if not document.get("chunk_hash"):
        document["chunk_hash"] = f"sha256:{hashlib.sha256(content.encode('utf-8')).hexdigest()}"
    if not document.get("content_hash"):
        document["content_hash"] = document["chunk_hash"]
    return document


class IndexBuilder:
    """Create a fixed mapping and synchronize chunks by stable ``chunk_id``."""

    def __init__(
        self,
        backend: SearchBackend,
        embedding_service: EmbeddingService,
        index_name: str = "coderadar_chunks_v1",
        *,
        bulk_batch_size: int = 200,
    ) -> None:
        self.backend = backend
        self.embedding_service = embedding_service
        self.index_name = index_name
        self.bulk_batch_size = bulk_batch_size

    def _ensure_index(self, recreate: bool) -> bool:
        exists = self.backend.index_exists(self.index_name)
        if recreate and exists:
            self.backend.delete_index(self.index_name, ignore_missing=False)
            exists = False
        if not exists:
            self.backend.create_index(
                self.index_name,
                build_index_mapping(
                    self.embedding_service.dimension,
                    embedding_model=self.embedding_service.model_name,
                ),
            )
            return True

        configured_model, configured_dimension = embedding_metadata(
            self.backend.get_mapping(self.index_name)
        )
        if configured_dimension != self.embedding_service.dimension:
            raise ValueError(
                f"index {self.index_name} expects embedding dimension "
                f"{configured_dimension}, provider returns {self.embedding_service.dimension}"
            )
        if configured_model and configured_model != self.embedding_service.model_name:
            raise ValueError(
                f"index {self.index_name} expects embedding model {configured_model!r}, "
                f"provider is {self.embedding_service.model_name!r}; build a versioned index"
            )
        return False

    @staticmethod
    def _embedding_compatible(
        existing: Mapping[str, Any], incoming: Mapping[str, Any], model: str, dimension: int
    ) -> bool:
        return (
            existing.get("chunk_hash") == incoming.get("chunk_hash")
            and existing.get("content_hash") == incoming.get("content_hash")
            and existing.get("embedding_model") == model
            and int(existing.get("embedding_dimension") or 0) == dimension
            and isinstance(existing.get("embedding"), list)
            and len(existing.get("embedding", [])) == dimension
        )

    @classmethod
    def _unchanged(
        cls, existing: Mapping[str, Any], incoming: Mapping[str, Any], model: str, dimension: int
    ) -> bool:
        if not cls._embedding_compatible(existing, incoming, model, dimension):
            return False
        index_only = {"embedding", "embedding_model", "embedding_dimension", "indexed_at"}
        # Content hashes alone do not capture lifecycle and evidence metadata.
        # Reindex a chunk when fields such as is_current, valid_to, title, or
        # evidence_level change while the source text remains identical.
        return all(
            existing.get(key) == value
            for key, value in incoming.items()
            if key not in index_only
        )

    def build(
        self,
        chunks: Iterable[Any],
        *,
        recreate: bool = False,
        incremental: bool = True,
        delete_missing: bool = False,
        refresh: bool = True,
    ) -> IndexingReport:
        documents = [chunk_to_document(chunk) for chunk in chunks]
        identifiers = [str(document["chunk_id"]) for document in documents]
        duplicate_ids = sorted(
            identifier for identifier, count in Counter(identifiers).items() if count > 1
        )
        if duplicate_ids:
            raise ValueError(f"duplicate chunk_id values: {duplicate_ids[:5]}")

        created = self._ensure_index(recreate)
        report = IndexingReport(
            index_name=self.index_name,
            input_count=len(documents),
            embedding_model=self.embedding_service.model_name,
            embedding_dimension=self.embedding_service.dimension,
            created_index=created,
        )

        pending: list[dict[str, Any]] = []
        needs_embedding: list[dict[str, Any]] = []
        for document in documents:
            existing = (
                self.backend.get_document(self.index_name, str(document["chunk_id"]))
                if incremental and not recreate
                else None
            )
            if existing and self._unchanged(
                existing,
                document,
                self.embedding_service.model_name,
                self.embedding_service.dimension,
            ):
                report.skipped_count += 1
            else:
                pending.append(document)
                if existing and self._embedding_compatible(
                    existing,
                    document,
                    self.embedding_service.model_name,
                    self.embedding_service.dimension,
                ):
                    document["embedding"] = list(existing["embedding"])
                    document["embedding_model"] = self.embedding_service.model_name
                    document["embedding_dimension"] = self.embedding_service.dimension
                else:
                    needs_embedding.append(document)

        if pending:
            vectors = self.embedding_service.embed_documents(
                [str(document["content"]) for document in needs_embedding]
            )
            indexed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            for document, vector in zip(needs_embedding, vectors, strict=True):
                document["embedding"] = vector
                document["embedding_model"] = self.embedding_service.model_name
                document["embedding_dimension"] = self.embedding_service.dimension
            for document in pending:
                document["indexed_at"] = indexed_at
            # 分批写入，避免 Elasticsearch HTTP 413 (Request Entity Too Large)
            offset = 0
            while offset < len(pending):
                batch = pending[offset : offset + self.bulk_batch_size]
                bulk = self.backend.bulk_index(
                    self.index_name,
                    batch,
                    id_field="chunk_id",
                    refresh=False,
                )
                report.indexed_count += bulk.indexed
                report.failed_count += bulk.failed
                report.errors.extend(bulk.errors)
                offset += self.bulk_batch_size

        if delete_missing and report.failed_count == 0:
            desired = set(identifiers)
            stale = [
                str(document["chunk_id"])
                for document in self.backend.iter_documents(self.index_name)
                if str(document.get("chunk_id")) not in desired
            ]
            deletion = self.backend.delete_documents(
                self.index_name, stale, refresh=False
            )
            report.deleted_count += deletion.deleted
            report.failed_count += deletion.failed
            report.errors.extend(deletion.errors)

        if refresh:
            self.backend.refresh(self.index_name)
        return report

    def index_chunks(self, chunks: Iterable[Any], **kwargs: Any) -> IndexingReport:
        """Compatibility alias for :meth:`build`."""

        return self.build(chunks, **kwargs)

    def rebuild(self, chunks: Iterable[Any], **kwargs: Any) -> IndexingReport:
        kwargs["recreate"] = True
        kwargs["incremental"] = False
        return self.build(chunks, **kwargs)


__all__ = ["IndexBuilder", "IndexingReport", "chunk_to_document"]
