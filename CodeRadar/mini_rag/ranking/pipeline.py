"""Composable ranking pipeline used by the Mini-RAG service."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from mini_rag.models import SearchCandidate

from ._common import as_candidate, candidate_id, get_value, normalise_scores, score_of, with_candidate
from .evidence_ranker import EvidenceRanker
from .reranker import CrossEncoderReranker
from .temporal_ranker import TemporalVersionRanker


class RankingPipeline:
    """Run semantic/lexical, temporal-version, and evidence stages in order."""

    def __init__(
        self,
        *,
        reranker: Any | None = None,
        temporal_ranker: TemporalVersionRanker | None = None,
        evidence_ranker: EvidenceRanker | None = None,
        semantic_weight: float = 0.70,
        temporal_weight: float = 0.12,
        version_weight: float = 0.08,
        evidence_weight: float = 0.10,
    ) -> None:
        weights = (semantic_weight, temporal_weight, version_weight, evidence_weight)
        if any(weight < 0.0 or weight > 1.0 for weight in weights):
            raise ValueError("ranking weights must be between 0 and 1")
        if abs(sum(weights) - 1.0) > 1e-9:
            raise ValueError("semantic, temporal, version, and evidence weights must sum to 1")
        self.reranker = reranker or CrossEncoderReranker()
        self.temporal_ranker = temporal_ranker or TemporalVersionRanker()
        self.evidence_ranker = evidence_ranker or EvidenceRanker()
        self.semantic_weight = semantic_weight
        self.temporal_weight = temporal_weight
        self.version_weight = version_weight
        self.evidence_weight = evidence_weight

    def rank(
        self,
        query: Any,
        candidates: Sequence[Any],
        *,
        top_k: int | None = None,
        use_reranker: bool = True,
        use_temporal_version: bool = True,
        use_evidence_ranking: bool = True,
        **temporal_options: Any,
    ) -> list[SearchCandidate]:
        question = query if isinstance(query, str) else getattr(query, "question", "")
        reranked = (
            self.reranker.rerank(question, candidates)
            if use_reranker
            else [as_candidate(candidate) for candidate in candidates]
        )
        semantic_values = normalise_scores(score_of(candidate) for candidate in reranked)
        semantic_scores = {
            candidate_id(candidate): value
            for candidate, value in zip(reranked, semantic_values)
        }
        temporal = (
            self.temporal_ranker.rerank(reranked, query, **temporal_options)
            if use_temporal_version
            else [
                with_candidate(candidate, temporal_score=0.0, version_score=0.0)
                for candidate in reranked
            ]
        )
        evidence_ranked = (
            self.evidence_ranker.rerank(temporal)
            if use_evidence_ranking
            else [with_candidate(candidate, evidence_score=0.0) for candidate in temporal]
        )
        final: list[SearchCandidate] = []
        for candidate in evidence_ranked:
            identifier = candidate_id(candidate)
            semantic_score = semantic_scores.get(identifier, 0.0)
            temporal_score = float(get_value(candidate, "temporal_score", 0.0) or 0.0)
            version_score = float(get_value(candidate, "version_score", 0.0) or 0.0)
            evidence_score = float(get_value(candidate, "evidence_score", 0.0) or 0.0)
            metadata = dict(get_value(candidate, "metadata", {}) or {})
            metadata["semantic_score"] = semantic_score
            final_score = (
                self.semantic_weight * semantic_score
                + self.temporal_weight * temporal_score
                + self.version_weight * version_score
                + self.evidence_weight * evidence_score
            )
            final.append(
                with_candidate(
                    candidate,
                    final_score=final_score,
                    metadata=metadata,
                )
            )
        final.sort(key=lambda item: (-score_of(item), candidate_id(item)))
        return final if top_k is None else final[:top_k]


BusinessRanker = RankingPipeline
