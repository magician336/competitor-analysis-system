"""Reproducible offline evaluator for the public Mini-RAG query contract."""

from __future__ import annotations

import csv
import inspect
import json
import math
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mini_rag.evidence.citation_validator import CitationValidator
from mini_rag.models import (
    EvaluationCase,
    EvaluationCaseResult,
    EvaluationResult,
    RAGQuery,
    RAGResponse,
)
from mini_rag.ranking._common import chunk_of, get_value

from .metrics import (
    latency_metrics,
    metadata_filter_accuracy,
    ndcg_at_k,
    old_version_false_recall_rate,
    recall_at_k,
    reciprocal_rank,
)


def _parse_list(value: Any) -> list[Any]:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    text = str(value).strip()
    if text.startswith("["):
        parsed = json.loads(text)
        return parsed if isinstance(parsed, list) else [parsed]
    delimiter = ";" if ";" in text else "|" if "|" in text else ","
    return [item.strip() for item in text.split(delimiter) if item.strip()]


def _parse_mapping(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise ValueError("mapping field must contain a JSON object")
    return parsed


def load_evaluation_cases(path: str | Path) -> list[EvaluationCase]:
    """Load the documented CSV evaluation format with JSON/list fields."""

    cases: list[EvaluationCase] = []
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            payload: dict[str, Any] = {key: value for key, value in row.items() if value not in (None, "")}
            for field in (
                "relevant_document_ids",
                "expected_document_ids",
                "relevant_chunk_ids",
                "expected_chunk_ids",
                "expected_dimension_tags",
            ):
                if field in payload:
                    payload[field] = _parse_list(payload[field])
            for field in ("relevance_grades", "query_filters"):
                if field in payload:
                    payload[field] = _parse_mapping(payload[field])
            cases.append(EvaluationCase.model_validate(payload))
    return cases


def validate_evaluation_cases(cases: Sequence[EvaluationCase]) -> None:
    """Reject structurally invalid retrieval gold sets before model execution."""

    if not cases:
        raise ValueError("evaluation dataset contains no cases")
    seen_case_ids: set[str] = set()
    query_fields = set(RAGQuery.model_fields) - {"question"}
    allowed_filter_fields = query_fields | {"expected_conflicts"}
    for index, case in enumerate(cases, start=1):
        if case.case_id in seen_case_ids:
            raise ValueError(f"duplicate evaluation case_id: {case.case_id}")
        seen_case_ids.add(case.case_id)
        relevant_ids = case.relevant_chunk_ids or case.relevant_document_ids
        if not relevant_ids:
            raise ValueError(
                f"evaluation case {case.case_id or index} has no relevant IDs; "
                "no-answer evaluation is not supported"
            )
        unknown_filters = set(case.query_filters) - allowed_filter_fields
        if unknown_filters:
            raise ValueError(
                f"evaluation case {case.case_id} has unknown query filters: "
                f"{sorted(unknown_filters)}"
            )
        if case.relevance_grades:
            invalid_grades = {
                identifier: grade
                for identifier, grade in case.relevance_grades.items()
                if not math.isfinite(float(grade)) or float(grade) < 0
            }
            if invalid_grades:
                raise ValueError(
                    f"evaluation case {case.case_id} has invalid relevance grades: "
                    f"{invalid_grades}"
                )
            missing_grades = [
                identifier
                for identifier in relevant_ids
                if float(case.relevance_grades.get(identifier, 0.0)) <= 0
            ]
            if missing_grades:
                raise ValueError(
                    f"evaluation case {case.case_id} has relevant IDs without positive "
                    f"grades: {missing_grades}"
                )


def _response_items(response: Any) -> list[Any]:
    if isinstance(response, RAGResponse):
        return list(response.evidence)
    if isinstance(response, Mapping):
        for key in ("evidence", "results", "candidates", "hits"):
            if key in response:
                return list(response[key] or [])
        return []
    if isinstance(response, Sequence) and not isinstance(response, (str, bytes)):
        return list(response)
    evidence = get_value(response, "evidence", None)
    return list(evidence or [])


def _response_conflicts(response: Any) -> list[Any]:
    return list(get_value(response, "conflicts", []) or [])


def _invoke_query(query_fn: Callable[..., Any], query: RAGQuery) -> Any:
    target = getattr(query_fn, "query", query_fn)
    try:
        signature = inspect.signature(target)
        parameters = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
    except (TypeError, ValueError):
        parameters = []
    first_name = parameters[0].name if parameters else "query"
    if first_name in {"question", "text"}:
        result = target(query.question, **query.filters())
    else:
        result = target(query)
    if inspect.isawaitable(result):
        raise TypeError("RetrievalEvaluator requires a synchronous query function")
    return result


def _expected_filters(case: EvaluationCase) -> dict[str, Any]:
    filters = dict(case.query_filters)
    if case.expected_competitor:
        filters.setdefault("competitor", case.expected_competitor)
    if case.expected_event_type:
        filters.setdefault("event_types", [case.expected_event_type])
    if case.expected_dimension_tags:
        filters.setdefault("dimension_tags", case.expected_dimension_tags)
    if case.expected_version:
        filters.setdefault("product_versions", [case.expected_version])
    return filters


def _content_contains(items: Sequence[Any], expected_quote: str | None) -> bool | None:
    if not expected_quote:
        return None
    needle = " ".join(expected_quote.casefold().split())
    return any(
        needle in " ".join(str(get_value(item, "content", get_value(chunk_of(item), "content", ""))).casefold().split())
        for item in items
    )


class RetrievalEvaluator:
    """Evaluate the same synchronous callable used by Agents and HTTP routes."""

    def __init__(
        self,
        query_fn: Callable[..., Any],
        *,
        citation_validator: CitationValidator | None = None,
        top_ks: Sequence[int] = (5, 10),
        ndcg_k: int = 10,
        raise_on_error: bool = False,
    ) -> None:
        if not top_ks or any(value < 1 for value in top_ks):
            raise ValueError("top_ks must contain positive values")
        if ndcg_k < 1:
            raise ValueError("ndcg_k must be positive")
        self.query_fn = query_fn
        self.citation_validator = citation_validator or CitationValidator()
        self.top_ks = tuple(dict.fromkeys(int(value) for value in top_ks))
        self.ndcg_k = ndcg_k
        self.raise_on_error = raise_on_error

    def evaluate(
        self,
        cases: Sequence[EvaluationCase | Mapping[str, Any]],
        *,
        config: Mapping[str, Any] | None = None,
    ) -> EvaluationResult:
        normalised_cases = [
            case if isinstance(case, EvaluationCase) else EvaluationCase.model_validate(case)
            for case in cases
        ]
        started = datetime.now(timezone.utc)
        details: list[EvaluationCaseResult] = []
        latencies: list[float] = []

        for case in normalised_cases:
            start = time.perf_counter()
            errors: list[str] = []
            response: Any = None
            try:
                query_fields = set(RAGQuery.model_fields) - {"question"}
                query_filters = {
                    name: value
                    for name, value in case.query_filters.items()
                    if name in query_fields
                }
                evaluation_top_k = max((*self.top_ks, self.ndcg_k))
                query_filters["top_k"] = max(
                    evaluation_top_k,
                    int(query_filters.get("top_k", 0) or 0),
                )
                response = _invoke_query(
                    self.query_fn,
                    RAGQuery(question=case.question, **query_filters),
                )
            except Exception as exc:
                if self.raise_on_error:
                    raise
                errors.append(f"{type(exc).__name__}: {exc}")
            latency_ms = (time.perf_counter() - start) * 1000.0
            latencies.append(latency_ms)
            items = _response_items(response) if response is not None else []
            chunk_ids = [str(get_value(item, "chunk_id", get_value(chunk_of(item), "chunk_id", ""))) for item in items]
            document_ids = [
                str(get_value(item, "document_id", get_value(chunk_of(item), "document_id", "")))
                for item in items
            ]
            if case.relevant_chunk_ids:
                retrieved_ids, relevant_ids = chunk_ids, case.relevant_chunk_ids
            else:
                retrieved_ids, relevant_ids = document_ids, case.relevant_document_ids
            grades = dict(case.relevance_grades)
            if not grades:
                grades = {identifier: 1.0 for identifier in relevant_ids}

            case_metrics = {
                f"recall@{k}": recall_at_k(retrieved_ids, relevant_ids, k)
                for k in self.top_ks
            }
            case_metrics.update(
                {
                    "mrr": reciprocal_rank(retrieved_ids, relevant_ids),
                    f"ndcg@{self.ndcg_k}": ndcg_at_k(retrieved_ids, grades, self.ndcg_k),
                    "metadata_filter_accuracy": metadata_filter_accuracy(items, _expected_filters(case)),
                    "old_version_false_recall_rate": old_version_false_recall_rate(
                        items,
                        current_only=bool(case.query_filters.get("current_only", False)),
                    ),
                }
            )
            citation_valid: bool | None = _content_contains(items, case.expected_evidence_quote)
            if items:
                validation = self.citation_validator.validate(items, items, require_quote=True)
                self_validation = validation.valid
                citation_valid = self_validation if citation_valid is None else citation_valid and self_validation
                errors.extend(validation.errors)
            if citation_valid is not None:
                case_metrics["citation_accuracy"] = float(citation_valid)

            expected_conflicts = case.query_filters.get("expected_conflicts")
            if expected_conflicts is not None:
                from .metrics import conflict_detection_metrics

                case_metrics.update(
                    conflict_detection_metrics(_response_conflicts(response), expected_conflicts)
                )
            details.append(
                EvaluationCaseResult(
                    case_id=case.case_id,
                    retrieved_chunk_ids=chunk_ids,
                    metrics=case_metrics,
                    latency_ms=latency_ms,
                    citation_valid=citation_valid,
                    errors=errors,
                )
            )

        metric_names = sorted({name for detail in details for name in detail.metrics})
        aggregate = {
            name: statistics.fmean(detail.metrics[name] for detail in details if name in detail.metrics)
            for name in metric_names
        }
        aggregate.update(latency_metrics(latencies))
        aggregate["query_success_rate"] = (
            sum(not detail.errors for detail in details) / len(details) if details else 0.0
        )
        completed = datetime.now(timezone.utc)
        return EvaluationResult(
            metrics=aggregate,
            case_count=len(details),
            latencies_ms=latencies,
            details=details,
            config={
                "top_ks": list(self.top_ks),
                "ndcg_k": self.ndcg_k,
                "query_top_k": max((*self.top_ks, self.ndcg_k)),
                **dict(config or {}),
            },
            started_at=started,
            completed_at=completed,
        )


Evaluator = RetrievalEvaluator
