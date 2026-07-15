"""Reciprocal-rank fusion of BM25 and dense retrieval."""

from __future__ import annotations

from time import perf_counter
from typing import Any

from mini_rag.models import RAGQuery, RetrievalTrace, SearchCandidate

from .bm25_retriever import BM25Retriever
from .common import query_limit
from .dense_retriever import DenseRetriever


class HybridRetriever:
    """Run independent lexical and semantic recall, then fuse by rank."""

    def __init__(
        self,
        bm25_retriever: BM25Retriever,
        dense_retriever: DenseRetriever,
        *,
        candidate_k: int = 20,
        rrf_k: int = 60,
        bm25_weight: float = 1.0,
        dense_weight: float = 1.0,
        default_top_k: int = 8,
    ) -> None:
        if candidate_k < 1 or rrf_k < 1 or default_top_k < 1:
            raise ValueError("candidate_k, rrf_k, and default_top_k must be positive")
        if bm25_weight < 0 or dense_weight < 0 or bm25_weight + dense_weight == 0:
            raise ValueError("at least one retrieval weight must be positive")
        self.bm25_retriever = bm25_retriever
        self.dense_retriever = dense_retriever
        self.candidate_k = candidate_k
        self.rrf_k = rrf_k
        self.bm25_weight = float(bm25_weight)
        self.dense_weight = float(dense_weight)
        self.default_top_k = default_top_k
        self.last_trace = RetrievalTrace(
            retrieval_config=self.configuration(),
        )

    def configuration(self) -> dict[str, Any]:
        return {
            "method": "bm25+dense+rrf",
            "candidate_k": self.candidate_k,
            "rrf_k": self.rrf_k,
            "bm25_weight": self.bm25_weight,
            "dense_weight": self.dense_weight,
        }

    def search(
        self,
        query: str | RAGQuery,
        filters: Any = None,
        *,
        top_k: int | None = None,
    ) -> list[SearchCandidate]:
        limit = query_limit(query, top_k, self.default_top_k)
        started = perf_counter()

        stage = perf_counter()
        bm25 = self.bm25_retriever.search(
            query, filters, top_k=max(self.candidate_k, limit)
        )
        bm25_ms = (perf_counter() - stage) * 1_000

        stage = perf_counter()
        dense = self.dense_retriever.search(
            query, filters, top_k=max(self.candidate_k, limit)
        )
        dense_ms = (perf_counter() - stage) * 1_000

        fused: dict[str, SearchCandidate] = {}
        for candidate in bm25:
            fused[candidate.chunk_id] = candidate.model_copy(deep=True)
        for candidate in dense:
            existing = fused.get(candidate.chunk_id)
            if existing is None:
                fused[candidate.chunk_id] = candidate.model_copy(deep=True)
                continue
            existing.dense_rank = candidate.dense_rank
            existing.dense_score = candidate.dense_score
            existing.retrieval_methods = list(
                dict.fromkeys([*existing.retrieval_methods, *candidate.retrieval_methods])
            )

        for candidate in fused.values():
            score = 0.0
            if candidate.bm25_rank is not None:
                score += self.bm25_weight / (self.rrf_k + candidate.bm25_rank)
            if candidate.dense_rank is not None:
                score += self.dense_weight / (self.rrf_k + candidate.dense_rank)
            candidate.rrf_score = score
            candidate.final_score = score
            if "rrf" not in candidate.retrieval_methods:
                candidate.retrieval_methods.append("rrf")

        ordered = sorted(
            fused.values(),
            key=lambda candidate: (
                -(candidate.rrf_score or 0.0),
                candidate.bm25_rank or 10**9,
                candidate.dense_rank or 10**9,
                candidate.chunk_id,
            ),
        )
        returned = ordered[:limit]
        elapsed_ms = (perf_counter() - started) * 1_000
        self.last_trace = RetrievalTrace(
            bm25_candidates=len(bm25),
            dense_candidates=len(dense),
            fused_candidates=len(ordered),
            returned_candidates=len(returned),
            latency_ms=elapsed_ms,
            stage_latency_ms={"bm25": bm25_ms, "dense": dense_ms},
            retrieval_config=self.configuration(),
        )
        return returned

    def search_with_trace(
        self,
        query: str | RAGQuery,
        filters: Any = None,
        *,
        top_k: int | None = None,
    ) -> tuple[list[SearchCandidate], RetrievalTrace]:
        candidates = self.search(query, filters, top_k=top_k)
        return candidates, self.last_trace.model_copy(deep=True)

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


__all__ = ["HybridRetriever"]
