"""BM25 full-text retriever."""

from __future__ import annotations

from typing import Any

from mini_rag.indexing import SearchBackend
from mini_rag.models import RAGQuery, SearchCandidate

from .common import bm25_candidate, query_filters, query_limit, query_text
from .query_rewriter import QueryRewriter


class BM25Retriever:
    """Retrieve exact names, versions, prices, and technical terms."""

    def __init__(
        self,
        backend: SearchBackend,
        index_name: str = "coderadar_chunks_current",
        *,
        default_top_k: int = 20,
        query_rewriter: QueryRewriter | None = None,
    ) -> None:
        if default_top_k < 1:
            raise ValueError("default_top_k must be at least 1")
        self.backend = backend
        self.index_name = index_name
        self.default_top_k = default_top_k
        self.query_rewriter = query_rewriter

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
            text = self.query_rewriter.rewrite(query).keyword_query
        hits = self.backend.search_bm25(
            self.index_name,
            text,
            query_filters(query, filters),
            top_k=limit,
        )
        return [bm25_candidate(hit, rank) for rank, hit in enumerate(hits, start=1)]

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


__all__ = ["BM25Retriever"]
