"""Evidence-authority and corroboration-aware final ranking."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from mini_rag.models import SearchCandidate

from ._common import (
    candidate_id,
    chunk_of,
    get_value,
    metadata_of,
    normalise_scores,
    score_of,
    with_candidate,
)


DEFAULT_EVIDENCE_WEIGHTS: dict[str, float] = {"A": 1.0, "B": 0.8, "C": 0.55, "D": 0.3}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _fact_signature(candidate: Any) -> tuple[str, str, str] | None:
    chunk = chunk_of(candidate)
    metadata = metadata_of(candidate)
    field = metadata.get("fact_field") or metadata.get("field")
    value = metadata.get("fact_value", metadata.get("value"))
    if not field or value in (None, ""):
        return None
    competitor = str(get_value(chunk, "competitor", "")).casefold()
    serialised = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return competitor, str(field).casefold(), serialised.casefold()


class EvidenceRanker:
    """Blend retrieval relevance with source authority and corroboration."""

    def __init__(
        self,
        *,
        evidence_weight: float = 0.2,
        level_weights: Mapping[str, float] | None = None,
        corroboration_weight: float = 0.15,
    ) -> None:
        if not 0.0 <= evidence_weight <= 1.0:
            raise ValueError("evidence_weight must be between 0 and 1")
        if not 0.0 <= corroboration_weight <= 1.0:
            raise ValueError("corroboration_weight must be between 0 and 1")
        self.evidence_weight = evidence_weight
        self.level_weights = {key.upper(): float(value) for key, value in (level_weights or DEFAULT_EVIDENCE_WEIGHTS).items()}
        self.corroboration_weight = corroboration_weight

    def rerank(
        self,
        candidates: Sequence[Any],
        *,
        top_k: int | None = None,
    ) -> list[SearchCandidate]:
        if top_k is not None and top_k < 1:
            raise ValueError("top_k must be positive")
        signatures = [_fact_signature(candidate) for candidate in candidates]
        signature_counts = Counter(signature for signature in signatures if signature is not None)
        base_scores = normalise_scores(score_of(candidate) for candidate in candidates)
        ranked: list[SearchCandidate] = []
        for candidate, signature, base_score in zip(candidates, signatures, base_scores):
            chunk = chunk_of(candidate)
            level = _enum_value(get_value(chunk, "evidence_level", "D")).upper()
            authority = max(0.0, min(1.0, self.level_weights.get(level, 0.0)))
            corroboration = 1.0 if signature is not None and signature_counts[signature] > 1 else 0.0
            evidence_score = (1.0 - self.corroboration_weight) * authority + self.corroboration_weight * corroboration
            final = (1.0 - self.evidence_weight) * base_score + self.evidence_weight * evidence_score
            ranked.append(with_candidate(candidate, evidence_score=evidence_score, final_score=final))
        ranked.sort(key=lambda item: (-score_of(item), candidate_id(item)))
        return ranked if top_k is None else ranked[:top_k]

    rank = rerank


EvidenceAwareRanker = EvidenceRanker

