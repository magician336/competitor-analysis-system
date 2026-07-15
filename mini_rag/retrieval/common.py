"""Shared query and candidate conversion helpers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mini_rag.indexing import BackendHit, normalize_filters
from mini_rag.models import Chunk, RAGQuery, SearchCandidate


def query_text(query: str | RAGQuery) -> str:
    return query.question if isinstance(query, RAGQuery) else str(query).strip()


def query_filters(query: str | RAGQuery, extra: Any = None) -> dict[str, Any]:
    values = query.filters(exclude_empty=True) if isinstance(query, RAGQuery) else {}
    values.update(normalize_filters(extra))
    return values


def query_limit(query: str | RAGQuery, top_k: int | None, default: int) -> int:
    if top_k is not None:
        limit = int(top_k)
    elif isinstance(query, RAGQuery):
        limit = query.top_k
    else:
        limit = default
    if limit < 1:
        raise ValueError("top_k must be at least 1")
    return limit


def as_rag_query(
    query: str | RAGQuery,
    filters: Any = None,
    *,
    top_k: int | None = None,
) -> RAGQuery:
    """Return a validated query with explicit filters taking precedence."""

    values = query_filters(query, filters)
    if top_k is not None:
        values["top_k"] = top_k
    elif isinstance(query, RAGQuery):
        values["top_k"] = query.top_k
    return RAGQuery(question=query_text(query), **values)


def chunk_from_source(source: Mapping[str, Any]) -> Chunk:
    """Discard index-only metadata and reconstruct the shared Chunk model."""

    fields = set(Chunk.model_fields)
    values = {name: source[name] for name in fields if name in source}
    return Chunk.model_validate(values)


def hit_parts(hit: BackendHit | Mapping[str, Any] | Any) -> tuple[dict[str, Any], float]:
    if isinstance(hit, BackendHit):
        return hit.source, float(hit.score)
    if isinstance(hit, Mapping):
        source = hit.get("source", hit.get("_source", hit))
        score = hit.get("score", hit.get("_score", 0.0))
        return dict(source), float(score or 0.0)
    source = getattr(hit, "source", getattr(hit, "_source", {}))
    score = getattr(hit, "score", getattr(hit, "_score", 0.0))
    return dict(source), float(score or 0.0)


def bm25_candidate(hit: BackendHit | Mapping[str, Any] | Any, rank: int) -> SearchCandidate:
    source, score = hit_parts(hit)
    return SearchCandidate(
        chunk=chunk_from_source(source),
        bm25_rank=rank,
        bm25_score=score,
        final_score=score,
        retrieval_methods=["bm25"],
    )


def dense_candidate(hit: BackendHit | Mapping[str, Any] | Any, rank: int) -> SearchCandidate:
    source, score = hit_parts(hit)
    return SearchCandidate(
        chunk=chunk_from_source(source),
        dense_rank=rank,
        dense_score=score,
        final_score=score,
        retrieval_methods=["dense"],
    )


__all__ = [
    "as_rag_query",
    "bm25_candidate",
    "chunk_from_source",
    "dense_candidate",
    "hit_parts",
    "query_filters",
    "query_limit",
    "query_text",
]
