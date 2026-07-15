"""Time- and product-version-aware ranking for retrieved candidates."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any

from mini_rag.models import SearchCandidate

from ._common import (
    as_datetime,
    candidate_id,
    chunk_of,
    get_value,
    normalise_scores,
    score_of,
    with_candidate,
)


_LATEST_TERMS = ("最新", "最近", "当前", "目前", "现价", "latest", "recent", "current", "now")
_HISTORY_TERMS = ("历史", "曾经", "当时", "旧版", "过往", "history", "historical", "previous", "formerly")
_VERSION_PREFIX = re.compile(r"^(?:version|版本|release)?\s*v?", re.IGNORECASE)


def normalise_version(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = _VERSION_PREFIX.sub("", str(value).strip()).casefold()
    match = re.fullmatch(r"(\d+(?:\.\d+)*)(.*)", text)
    if not match:
        return text
    numeric, suffix = match.groups()
    parts = [int(part) for part in numeric.split(".")]
    while len(parts) > 1 and parts[-1] == 0:
        parts.pop()
    return ".".join(str(part) for part in parts) + suffix


def version_matches(candidate_version: Any, requested_versions: Sequence[Any]) -> bool:
    candidate = normalise_version(candidate_version)
    requested = {normalise_version(value) for value in requested_versions}
    return candidate is not None and candidate in requested


def _query_field(query: Any, name: str, default: Any = None) -> Any:
    return get_value(query, name, default) if not isinstance(query, str) else default


def _interval_contains(chunk: Any, instant: datetime) -> bool:
    valid_from = as_datetime(get_value(chunk, "valid_from", None))
    valid_to = as_datetime(get_value(chunk, "valid_to", None))
    # VersionStore emits adjacent half-open intervals: [valid_from, valid_to).
    return (valid_from is None or valid_from <= instant) and (valid_to is None or instant < valid_to)


class TemporalVersionRanker:
    """Apply explicit temporal/version constraints and recency-aware scoring."""

    def __init__(
        self,
        *,
        half_life_days: float = 180.0,
        temporal_weight: float = 0.25,
        version_weight: float = 0.08,
    ) -> None:
        if half_life_days <= 0:
            raise ValueError("half_life_days must be positive")
        if not 0.0 <= temporal_weight <= 1.0:
            raise ValueError("temporal_weight must be between 0 and 1")
        if not 0.0 <= version_weight <= 1.0:
            raise ValueError("version_weight must be between 0 and 1")
        if temporal_weight + version_weight > 1.0:
            raise ValueError("temporal_weight and version_weight cannot sum above 1")
        self.half_life_days = half_life_days
        self.temporal_weight = temporal_weight
        self.version_weight = version_weight

    def _recency(self, published: datetime | None, now: datetime) -> float:
        if published is None:
            return 0.25
        age_days = max(0.0, (now - published).total_seconds() / 86_400.0)
        return math.exp(-math.log(2.0) * age_days / self.half_life_days)

    def rerank(
        self,
        candidates: Sequence[Any],
        query: Any = "",
        *,
        now: datetime | str | None = None,
        as_of: datetime | str | None = None,
        current_only: bool | None = None,
        product_versions: Sequence[str] | None = None,
        start_time: datetime | str | None = None,
        end_time: datetime | str | None = None,
        strict_filters: bool = True,
        top_k: int | None = None,
    ) -> list[SearchCandidate]:
        if top_k is not None and top_k < 1:
            raise ValueError("top_k must be positive")
        now_value = as_datetime(now) or datetime.now(timezone.utc)
        as_of_value = as_datetime(as_of)
        if current_only is None:
            current_only = bool(_query_field(query, "current_only", False))
        versions = list(product_versions or _query_field(query, "product_versions", None) or [])
        start = as_datetime(start_time or _query_field(query, "start_time", None))
        end = as_datetime(end_time or _query_field(query, "end_time", None))
        question = query if isinstance(query, str) else str(_query_field(query, "question", "") or "")
        question_lower = question.casefold()
        latest_intent = current_only or any(term in question_lower for term in _LATEST_TERMS)
        historical_intent = any(term in question_lower for term in _HISTORY_TERMS)

        kept: list[Any] = []
        temporal_scores: list[float] = []
        version_scores: list[float] = []
        for candidate in candidates:
            chunk = chunk_of(candidate)
            published = as_datetime(get_value(chunk, "publish_time", None))
            is_current = bool(get_value(chunk, "is_current", False))
            version_ok = not versions or version_matches(get_value(chunk, "product_version", None), versions)
            current_ok = not current_only or is_current
            interval_ok = as_of_value is None or _interval_contains(chunk, as_of_value)
            start_ok = start is None or published is None or published >= start
            end_ok = end is None or published is None or published <= end
            if strict_filters and not (version_ok and current_ok and interval_ok and start_ok and end_ok):
                continue

            recency = self._recency(published, now_value)
            if as_of_value is not None:
                time_score = 1.0 if interval_ok else 0.0
            elif latest_intent:
                time_score = recency
            elif historical_intent:
                time_score = 0.6 if interval_ok else 0.0
            else:
                # A mild recency prior resolves ties without dominating relevance.
                time_score = 0.5 + 0.5 * recency

            if versions:
                version_score = float(version_ok)
            elif as_of_value is not None:
                version_score = float(interval_ok)
            elif latest_intent:
                version_score = float(is_current)
            elif historical_intent:
                version_score = 1.0 if not is_current else 0.2
            else:
                version_score = 1.0 if is_current else 0.5
            if not strict_filters:
                if not current_ok:
                    version_score *= 0.35
                if not version_ok or not interval_ok:
                    version_score *= 0.2
                if not start_ok or not end_ok:
                    time_score *= 0.2
            kept.append(candidate)
            temporal_scores.append(max(0.0, min(1.0, time_score)))
            version_scores.append(max(0.0, min(1.0, version_score)))

        base_scores = normalise_scores(score_of(candidate) for candidate in kept)
        ranked = [
            with_candidate(
                candidate,
                temporal_score=temporal_score,
                version_score=version_score,
                final_score=(1.0 - self.temporal_weight - self.version_weight) * base_score
                + self.temporal_weight * temporal_score
                + self.version_weight * version_score,
            )
            for candidate, temporal_score, version_score, base_score in zip(
                kept,
                temporal_scores,
                version_scores,
                base_scores,
            )
        ]
        ranked.sort(key=lambda item: (-score_of(item), candidate_id(item)))
        return ranked if top_k is None else ranked[:top_k]

    rank = rerank


TemporalRanker = TemporalVersionRanker
