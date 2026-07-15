"""Internal helpers shared by Mini-RAG ranking stages."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import is_dataclass, replace
from datetime import date, datetime, time, timezone
from typing import Any, Iterable

from mini_rag.models import Chunk, SearchCandidate


_MISSING = object()


def get_value(value: Any, name: str, default: Any = None) -> Any:
    """Read a field from mappings, Pydantic models, or regular objects."""

    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def chunk_of(candidate: Any) -> Any:
    """Return the chunk embedded in a search candidate."""

    chunk = get_value(candidate, "chunk", _MISSING)
    return candidate if chunk is _MISSING or chunk is None else chunk


def candidate_id(candidate: Any) -> str:
    chunk = chunk_of(candidate)
    value = get_value(chunk, "chunk_id", None) or get_value(candidate, "chunk_id", None)
    if not value:
        raise ValueError("each ranking candidate must contain a non-empty chunk_id")
    return str(value)


def _model_fields(model_type: type[Any]) -> set[str]:
    fields = getattr(model_type, "model_fields", None)
    return set(fields) if fields else set()


def _make_chunk(payload: Mapping[str, Any]) -> Chunk:
    if hasattr(Chunk, "model_validate"):
        return Chunk.model_validate(payload)
    return Chunk(**dict(payload))


def as_candidate(value: Any) -> SearchCandidate:
    """Coerce a public candidate, chunk, or mapping into SearchCandidate."""

    if isinstance(value, SearchCandidate):
        if hasattr(value, "model_copy"):
            return value.model_copy(deep=True)
        if is_dataclass(value):
            return replace(value)
        return value

    if isinstance(value, Chunk):
        return SearchCandidate(chunk=value)

    if isinstance(value, Mapping):
        payload = dict(value)
        chunk = payload.get("chunk")
        if isinstance(chunk, Mapping):
            chunk = _make_chunk(chunk)
        if chunk is None:
            chunk_fields = _model_fields(Chunk)
            chunk_payload = {key: payload[key] for key in chunk_fields if key in payload}
            if not chunk_payload:
                raise ValueError("candidate mapping must contain a chunk or Chunk fields")
            chunk = _make_chunk(chunk_payload)
        candidate_fields = _model_fields(SearchCandidate)
        candidate_payload = {
            key: item
            for key, item in payload.items()
            if key != "chunk" and (not candidate_fields or key in candidate_fields)
        }
        candidate_payload["chunk"] = chunk
        if hasattr(SearchCandidate, "model_validate"):
            return SearchCandidate.model_validate(candidate_payload)
        return SearchCandidate(**candidate_payload)

    embedded = get_value(value, "chunk", None)
    if embedded is not None:
        fields = _model_fields(SearchCandidate)
        payload = {name: get_value(value, name) for name in fields if get_value(value, name, _MISSING) is not _MISSING}
        payload["chunk"] = embedded
        return SearchCandidate(**payload)
    raise TypeError(f"unsupported candidate type: {type(value)!r}")


def with_candidate(candidate: Any, **changes: Any) -> SearchCandidate:
    """Return a candidate copy with selected score/rank fields updated."""

    current = as_candidate(candidate)
    known = _model_fields(SearchCandidate)
    updates = {key: value for key, value in changes.items() if not known or key in known}
    if hasattr(current, "model_copy"):
        return current.model_copy(update=updates, deep=True)
    if is_dataclass(current):
        return replace(current, **updates)
    for key, value in updates.items():
        setattr(current, key, value)
    return current


def candidate_text(candidate: Any) -> str:
    chunk = chunk_of(candidate)
    title = str(get_value(chunk, "title", "") or "").strip()
    content = str(get_value(chunk, "content", "") or "").strip()
    heading = get_value(chunk, "heading_path", ()) or ()
    if isinstance(heading, str):
        heading_text = heading
    else:
        heading_text = " > ".join(str(item) for item in heading if item)
    return "\n".join(item for item in (title, heading_text, content) if item)


def score_of(candidate: Any) -> float:
    """Return the best score available before the next ranking stage."""

    for field in ("final_score", "rerank_score", "rrf_score", "dense_score", "bm25_score", "score"):
        value = get_value(candidate, field, None)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def normalise_scores(values: Iterable[float]) -> list[float]:
    numbers = [float(value) for value in values]
    if not numbers:
        return []
    low, high = min(numbers), max(numbers)
    if high == low:
        return [1.0 if high > 0 else 0.0 for _ in numbers]
    return [(value - low) / (high - low) for value in numbers]


def as_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.min)
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            result = datetime.fromisoformat(text)
        except ValueError:
            try:
                result = datetime.combine(date.fromisoformat(text[:10]), time.min)
            except ValueError:
                return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def metadata_of(candidate: Any) -> dict[str, Any]:
    metadata = get_value(chunk_of(candidate), "source_metadata", {}) or {}
    return dict(metadata) if isinstance(metadata, Mapping) else {}

