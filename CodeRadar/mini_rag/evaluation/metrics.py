"""Pure offline retrieval, filtering, citation, conflict, and latency metrics."""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence, Set
from typing import Any

from mini_rag.models import CitationValidationResult, Conflict
from mini_rag.ranking._common import as_datetime, chunk_of, get_value
from mini_rag.ranking.temporal_ranker import version_matches


def _unique(values: Sequence[Any]) -> list[str]:
    return list(dict.fromkeys(str(value) for value in values))


def recall_at_k(retrieved_ids: Sequence[Any], relevant_ids: Sequence[Any] | Set[Any], k: int) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    relevant = {str(value) for value in relevant_ids}
    if not relevant:
        return 0.0
    retrieved = set(_unique(retrieved_ids[:k]))
    return len(retrieved & relevant) / len(relevant)


def precision_at_k(retrieved_ids: Sequence[Any], relevant_ids: Sequence[Any] | Set[Any], k: int) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    actual = _unique(retrieved_ids[:k])
    if not actual:
        return 0.0
    relevant = {str(value) for value in relevant_ids}
    return sum(identifier in relevant for identifier in actual) / len(actual)


def reciprocal_rank(retrieved_ids: Sequence[Any], relevant_ids: Sequence[Any] | Set[Any]) -> float:
    relevant = {str(value) for value in relevant_ids}
    for rank, identifier in enumerate(retrieved_ids, start=1):
        if str(identifier) in relevant:
            return 1.0 / rank
    return 0.0


def mean_reciprocal_rank(
    retrieved_rankings: Sequence[Sequence[Any]],
    relevant_sets: Sequence[Sequence[Any] | Set[Any]],
) -> float:
    if len(retrieved_rankings) != len(relevant_sets):
        raise ValueError("retrieved_rankings and relevant_sets must have equal length")
    if not retrieved_rankings:
        return 0.0
    return statistics.fmean(
        reciprocal_rank(retrieved, relevant)
        for retrieved, relevant in zip(retrieved_rankings, relevant_sets)
    )


def dcg_at_k(relevances: Sequence[float], k: int) -> float:
    if k < 1:
        raise ValueError("k must be positive")
    return sum(
        (2.0 ** float(relevance) - 1.0) / math.log2(rank + 1.0)
        for rank, relevance in enumerate(relevances[:k], start=1)
    )


def ndcg_at_k(
    retrieved_ids_or_relevances: Sequence[Any],
    relevance_grades: Mapping[Any, float] | Sequence[Any] | Set[Any] | None = None,
    k: int = 10,
) -> float:
    """Calculate nDCG from numeric gains or retrieved IDs plus labelled gains."""

    if k < 1:
        raise ValueError("k must be positive")
    if relevance_grades is None:
        relevances = [float(value) for value in retrieved_ids_or_relevances]
        ideal = sorted(relevances, reverse=True)
    else:
        if isinstance(relevance_grades, Mapping):
            grade_map = {str(key): float(value) for key, value in relevance_grades.items()}
        else:
            grade_map = {str(key): 1.0 for key in relevance_grades}
        relevances = [grade_map.get(str(identifier), 0.0) for identifier in retrieved_ids_or_relevances]
        ideal = sorted(grade_map.values(), reverse=True)
    ideal_dcg = dcg_at_k(ideal, k)
    return dcg_at_k(relevances, k) / ideal_dcg if ideal_dcg > 0 else 0.0


def _normalise(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().casefold()


def _matches_filter(item: Any, name: str, expected: Any) -> bool:
    source = chunk_of(item)
    if name in {"competitor", "expected_competitor"}:
        return _normalise(get_value(source, "competitor", None)) == _normalise(expected)
    if name in {"event_type", "event_types", "expected_event_type"}:
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return _normalise(get_value(source, "event_type", None)) in {_normalise(value) for value in values}
    if name in {"dimension_tags", "expected_dimension_tags"}:
        actual = {_normalise(value) for value in (get_value(source, "dimension_tags", []) or [])}
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return {_normalise(value) for value in values}.issubset(actual)
    if name in {"product_version", "product_versions", "expected_version"}:
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return version_matches(get_value(source, "product_version", None), list(values))
    if name in {"evidence_level", "evidence_levels"}:
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return _normalise(get_value(source, "evidence_level", None)) in {_normalise(value) for value in values}
    if name in {"source_type", "source_types"}:
        values = expected if isinstance(expected, (list, tuple, set)) else [expected]
        return _normalise(get_value(source, "source_type", None)) in {_normalise(value) for value in values}
    if name == "current_only":
        return not bool(expected) or bool(get_value(source, "is_current", False))
    if name == "start_time":
        published, boundary = as_datetime(get_value(source, "publish_time", None)), as_datetime(expected)
        return published is not None and boundary is not None and published >= boundary
    if name == "end_time":
        published, boundary = as_datetime(get_value(source, "publish_time", None)), as_datetime(expected)
        return published is not None and boundary is not None and published <= boundary
    return True


def metadata_filter_accuracy(results: Sequence[Any], expected_filters: Mapping[str, Any]) -> float:
    active_filters = {
        name: value
        for name, value in expected_filters.items()
        if value not in (None, "", [], (), {}, False)
    }
    if not active_filters:
        return 1.0
    if not results:
        return 0.0
    matched = sum(
        all(_matches_filter(item, name, expected) for name, expected in active_filters.items())
        for item in results
    )
    return matched / len(results)


filter_accuracy = metadata_filter_accuracy


def old_version_false_recall_rate(results: Sequence[Any], *, current_only: bool = True) -> float:
    if not current_only or not results:
        return 0.0
    stale = sum(not bool(get_value(chunk_of(item), "is_current", False)) for item in results)
    return stale / len(results)


def citation_accuracy(validations: Sequence[CitationValidationResult | bool]) -> float:
    if not validations:
        return 0.0
    return sum(bool(item if isinstance(item, bool) else item.valid) for item in validations) / len(validations)


def _conflict_key(conflict: Conflict | Mapping[str, Any]) -> tuple[str, frozenset[str]]:
    field = _normalise(get_value(conflict, "field", ""))
    values = frozenset(_normalise(value) for value in (get_value(conflict, "values", []) or []))
    return field, values


def conflict_detection_metrics(
    predicted: Sequence[Conflict | Mapping[str, Any]],
    expected: Sequence[Conflict | Mapping[str, Any]],
) -> dict[str, float]:
    predicted_keys = {_conflict_key(value) for value in predicted}
    expected_keys = {_conflict_key(value) for value in expected}
    true_positive = len(predicted_keys & expected_keys)
    precision = true_positive / len(predicted_keys) if predicted_keys else float(not expected_keys)
    recall = true_positive / len(expected_keys) if expected_keys else float(not predicted_keys)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    exact = float(predicted_keys == expected_keys)
    return {
        "conflict_precision": precision,
        "conflict_recall": recall,
        "conflict_f1": f1,
        "conflict_detection_accuracy": exact,
    }


def percentile(values: Sequence[float], percentile_value: float) -> float:
    if not 0.0 <= percentile_value <= 100.0:
        raise ValueError("percentile must be between 0 and 100")
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * percentile_value / 100.0
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def latency_metrics(latencies_ms: Sequence[float]) -> dict[str, float]:
    if not latencies_ms:
        return {
            "mean_latency_ms": 0.0,
            "p50_latency_ms": 0.0,
            "p95_latency_ms": 0.0,
            "max_latency_ms": 0.0,
        }
    values = [float(value) for value in latencies_ms]
    if any(value < 0 for value in values):
        raise ValueError("latencies must be non-negative")
    return {
        "mean_latency_ms": statistics.fmean(values),
        "p50_latency_ms": percentile(values, 50),
        "p95_latency_ms": percentile(values, 95),
        "max_latency_ms": max(values),
    }


mrr = mean_reciprocal_rank
ndcg = ndcg_at_k

