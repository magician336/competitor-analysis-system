"""Reciprocal Rank Fusion for heterogeneous retrieval result lists."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from mini_rag.models import SearchCandidate

from ._common import as_candidate, candidate_id, get_value, with_candidate


_RANK_FIELDS = {
    "bm25": "bm25_rank",
    "lexical": "bm25_rank",
    "dense": "dense_rank",
    "vector": "dense_rank",
}


def _source_field(source: str) -> str | None:
    lowered = source.casefold()
    return next((field for prefix, field in _RANK_FIELDS.items() if prefix in lowered), None)


class RRFFusion:
    """Fuse ordered result lists without comparing their raw score scales."""

    def __init__(self, k: int = 60, weights: Mapping[str, float] | None = None) -> None:
        if k < 0:
            raise ValueError("RRF k must be non-negative")
        self.k = k
        self.weights = dict(weights or {})

    def fuse(
        self,
        rankings: Mapping[str, Sequence[Any]] | Sequence[Sequence[Any]],
        *,
        top_k: int | None = None,
    ) -> list[SearchCandidate]:
        if top_k is not None and top_k < 1:
            raise ValueError("top_k must be positive")
        if isinstance(rankings, Mapping):
            named_rankings = list(rankings.items())
        else:
            named_rankings = [(f"ranking_{index}", ranking) for index, ranking in enumerate(rankings)]

        candidates: dict[str, SearchCandidate] = {}
        scores: dict[str, float] = {}
        best_ranks: dict[str, int] = {}
        first_seen: dict[str, int] = {}
        sequence_number = 0

        for source, ranking in named_rankings:
            weight = float(self.weights.get(source, 1.0))
            if weight < 0:
                raise ValueError("RRF source weights must be non-negative")
            rank_field = _source_field(source)
            seen_in_source: set[str] = set()
            for position, raw_candidate in enumerate(ranking, start=1):
                identifier = candidate_id(raw_candidate)
                if identifier in seen_in_source:
                    continue
                seen_in_source.add(identifier)
                candidate = as_candidate(raw_candidate)
                rank_value = get_value(candidate, rank_field, None) if rank_field else None
                try:
                    effective_rank = int(rank_value) if rank_value is not None else position
                except (TypeError, ValueError):
                    effective_rank = position
                if effective_rank < 1:
                    effective_rank = position

                updates: dict[str, Any] = {}
                if rank_field:
                    updates[rank_field] = effective_rank
                incoming_methods = list(get_value(candidate, "retrieval_methods", []) or [])
                source_method = "bm25" if rank_field == "bm25_rank" else "dense" if rank_field == "dense_rank" else source
                incoming_methods.append(source_method)
                existing = candidates.get(identifier)
                if existing is None:
                    first_seen[identifier] = sequence_number
                    sequence_number += 1
                    updates["retrieval_methods"] = list(dict.fromkeys(incoming_methods))
                    candidates[identifier] = with_candidate(candidate, **updates)
                else:
                    # Preserve ranks and raw scores contributed by all retrieval paths.
                    for field in (
                        "bm25_rank",
                        "dense_rank",
                        "bm25_score",
                        "dense_score",
                    ):
                        incoming = updates.get(field, get_value(candidate, field, None))
                        if incoming is not None:
                            updates[field] = incoming
                    updates["retrieval_methods"] = list(
                        dict.fromkeys(
                            [*(get_value(existing, "retrieval_methods", []) or []), *incoming_methods]
                        )
                    )
                    candidates[identifier] = with_candidate(existing, **updates)
                scores[identifier] = scores.get(identifier, 0.0) + weight / (self.k + effective_rank)
                best_ranks[identifier] = min(best_ranks.get(identifier, effective_rank), effective_rank)

        ordered_ids = sorted(
            candidates,
            key=lambda identifier: (
                -scores[identifier],
                best_ranks[identifier],
                first_seen[identifier],
                identifier,
            ),
        )
        if top_k is not None:
            ordered_ids = ordered_ids[:top_k]
        return [
            with_candidate(
                candidates[identifier],
                rrf_score=scores[identifier],
                final_score=scores[identifier],
                retrieval_methods=list(
                    dict.fromkeys([*(get_value(candidates[identifier], "retrieval_methods", []) or []), "rrf"])
                ),
            )
            for identifier in ordered_ids
        ]


def reciprocal_rank_fusion(
    rankings: Mapping[str, Sequence[Any]] | Sequence[Sequence[Any]],
    *,
    k: int = 60,
    weights: Mapping[str, float] | None = None,
    top_k: int | None = None,
) -> list[SearchCandidate]:
    """Functional RRF interface used by retrievers and offline evaluation."""

    return RRFFusion(k=k, weights=weights).fuse(rankings, top_k=top_k)


def rrf_fuse(
    bm25_results: Sequence[Any],
    dense_results: Sequence[Any],
    *,
    k: int = 60,
    rrf_k: int | None = None,
    bm25_weight: float = 1.0,
    dense_weight: float = 1.0,
    top_k: int | None = None,
) -> list[SearchCandidate]:
    """Convenience interface for the standard BM25 and dense pair."""

    constant = k if rrf_k is None else rrf_k
    return reciprocal_rank_fusion(
        {"bm25": bm25_results, "dense": dense_results},
        k=constant,
        weights={"bm25": bm25_weight, "dense": dense_weight},
        top_k=top_k,
    )
