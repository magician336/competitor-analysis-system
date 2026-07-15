"""Dense-vector semantic retriever."""

from __future__ import annotations

from typing import Any

from mini_rag.embedding import EmbeddingService
from mini_rag.indexing import SearchBackend, embedding_metadata
from mini_rag.models import RAGQuery, SearchCandidate

from .common import dense_candidate, query_filters, query_limit, query_text
from .query_rewriter import QueryRewriter


class DenseRetriever:
    """Embed a question and retrieve chunks by cosine similarity."""

    def __init__(
        self,
        backend: SearchBackend,
        index_name: str | EmbeddingService = "coderadar_chunks_current",
        embedding_service: EmbeddingService | str | None = None,
        *,
        default_top_k: int = 20,
        query_rewriter: QueryRewriter | None = None,
        validate_mapping: bool = True,
    ) -> None:
        if default_top_k < 1:
            raise ValueError("default_top_k must be at least 1")
        self.backend = backend
        # Accept both common construction forms:
        # DenseRetriever(backend, "alias", embedding_service=service)
        # DenseRetriever(backend, service, "alias")
        if isinstance(index_name, EmbeddingService):
            service = index_name
            resolved_index = (
                embedding_service
                if isinstance(embedding_service, str)
                else "coderadar_chunks_current"
            )
        else:
            resolved_index = index_name
            service = embedding_service
        if not isinstance(service, EmbeddingService):
            raise TypeError("embedding_service must be an EmbeddingService instance")
        self.embedding_service = service
        self.index_name = resolved_index
        self.default_top_k = default_top_k
        self.query_rewriter = query_rewriter
        self.validate_mapping = validate_mapping
        self._mapping_validated = False
        self._validated_physical_index: str | None = None

    def _physical_index(self) -> str:
        resolver = getattr(self.backend, "resolve_alias", None)
        if callable(resolver):
            resolved = resolver(self.index_name)
            if resolved:
                return str(resolved)
        return str(self.index_name)

    def _validate_mapping_once(self) -> None:
        if not self.validate_mapping:
            return
        physical_index = self._physical_index()
        if self._mapping_validated and physical_index == self._validated_physical_index:
            return
        model, dimension = embedding_metadata(self.backend.get_mapping(physical_index))
        if dimension != self.embedding_service.dimension:
            raise ValueError(
                f"dense query dimension {self.embedding_service.dimension} does not match "
                f"index dimension {dimension}"
            )
        if model and model != self.embedding_service.model_name:
            raise ValueError(
                f"dense query model {self.embedding_service.model_name!r} does not match "
                f"index model {model!r}"
            )
        self._mapping_validated = True
        self._validated_physical_index = physical_index

    def search(
        self,
        query: str | RAGQuery,
        filters: Any = None,
        *,
        top_k: int | None = None,
    ) -> list[SearchCandidate]:
        limit = query_limit(query, top_k, self.default_top_k)
        text = query_text(query)
        if not text:
            raise ValueError("query must not be blank")
        if self.query_rewriter:
            text = self.query_rewriter.rewrite(query).semantic_query
        self._validate_mapping_once()
        vector = self.embedding_service.embed_query(text)
        hits = self.backend.search_dense(
            self.index_name,
            vector,
            query_filters(query, filters),
            top_k=limit,
        )
        return [dense_candidate(hit, rank) for rank, hit in enumerate(hits, start=1)]

    def retrieve(
        self,
        query: str | RAGQuery,
        filters: Any = None,
        *,
        top_k: int | None = None,
    ) -> list[SearchCandidate]:
        return self.search(query, filters, top_k=top_k)

    def __call__(self, query: str | RAGQuery, **kwargs: Any) -> list[SearchCandidate]:
        return self.search(query, **kwargs)


__all__ = ["DenseRetriever"]
