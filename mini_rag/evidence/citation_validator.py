"""Validate citations against the evidence returned for one RAG query."""

from __future__ import annotations

import html
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from mini_rag.models import CitationValidationResult, Conflict, Evidence, RAGResponse
from mini_rag.ranking._common import as_candidate, as_datetime, get_value

from .citation_builder import CitationBuilder
from .conflict_detector import ConflictDetector


_SPACE = re.compile(r"\s+")
_ELLIPSIS = re.compile(r"(?:\.\.\.|…+)")


def _normalise_text(value: Any) -> str:
    return _SPACE.sub(" ", html.unescape(str(value or ""))).strip().casefold()


def _quote_is_source_substring(quote: str, content: str) -> bool:
    normalised_quote = _normalise_text(quote)
    normalised_content = _normalise_text(content)
    if not normalised_quote:
        return False
    if normalised_quote in normalised_content:
        return True
    # Ellipsised quotations are valid when every non-empty fragment appears in order.
    fragments = [_normalise_text(fragment) for fragment in _ELLIPSIS.split(quote) if _normalise_text(fragment)]
    cursor = 0
    for fragment in fragments:
        position = normalised_content.find(fragment, cursor)
        if position < 0:
            return False
        cursor = position + len(fragment)
    return len(fragments) > 1


def _canonical_url(value: Any) -> str | None:
    try:
        parts = urlsplit(str(value or "").strip())
    except ValueError:
        return None
    if parts.scheme.casefold() not in {"http", "https"} or not parts.netloc:
        return None
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), path, parts.query, ""))


def _citation_value(citation: Any, field: str, default: Any = None) -> Any:
    if isinstance(citation, str):
        return citation if field == "chunk_id" else default
    return get_value(citation, field, default)


def _as_evidence(value: Any) -> Evidence:
    if isinstance(value, Evidence):
        return value
    if isinstance(value, Mapping) and "chunk" not in value and "char_start" in value:
        return Evidence.model_validate(value)
    return CitationBuilder().build_one(as_candidate(value))


class CitationValidator:
    """Enforce source, quotation, metadata, and query-scope constraints."""

    def __init__(self, chunk_resolver: Callable[[str], Any | None] | None = None) -> None:
        self.chunk_resolver = chunk_resolver

    def validate(
        self,
        citations: Sequence[Any],
        retrieved: Sequence[Any] | RAGResponse | None = None,
        *,
        allowed_chunk_ids: Sequence[str] | None = None,
        conflicts: Sequence[Conflict] | None = None,
        require_quote: bool = False,
    ) -> CitationValidationResult:
        if isinstance(retrieved, RAGResponse):
            retrieved_items: list[Any] = list(retrieved.evidence)
            response_conflicts = list(retrieved.conflicts)
        else:
            retrieved_items = list(retrieved or [])
            response_conflicts = []
        sources: dict[str, Evidence] = {}
        for item in retrieved_items:
            evidence = _as_evidence(item)
            sources[evidence.chunk_id] = evidence

        allowed = set(allowed_chunk_ids) if allowed_chunk_ids is not None else set(sources)
        errors: list[str] = []
        warnings: list[str] = []
        validated: list[str] = []

        for citation in citations:
            chunk_id = str(_citation_value(citation, "chunk_id", "") or "")
            if not chunk_id:
                errors.append("citation is missing chunk_id")
                continue
            if allowed_chunk_ids is not None and chunk_id not in allowed:
                errors.append(f"{chunk_id}: citation is outside the current query scope")
                continue
            source = sources.get(chunk_id)
            if source is None and self.chunk_resolver is not None:
                resolved = self.chunk_resolver(chunk_id)
                if resolved is not None:
                    source = _as_evidence(resolved)
                    sources[chunk_id] = source
            if source is None:
                errors.append(f"{chunk_id}: chunk does not exist in retrieved evidence")
                continue

            source_url = _canonical_url(source.url)
            cited_url_raw = _citation_value(citation, "url", None)
            cited_url = _canonical_url(cited_url_raw) if cited_url_raw else source_url
            if source_url is None:
                errors.append(f"{chunk_id}: stored source URL is invalid")
            elif cited_url is None:
                errors.append(f"{chunk_id}: citation URL is invalid")
            elif cited_url != source_url:
                errors.append(f"{chunk_id}: citation URL does not match retrieved evidence")

            quote = _citation_value(citation, "quote", None)
            if quote in (None, "") and not isinstance(citation, str):
                quote = _citation_value(citation, "content", None)
            if quote in (None, ""):
                message = f"{chunk_id}: citation does not contain a quotation"
                (errors if require_quote else warnings).append(message)
            elif not _quote_is_source_substring(str(quote), source.content):
                errors.append(f"{chunk_id}: quotation cannot be located in source content")

            for field in ("document_id", "version_id", "title", "product_version", "competitor"):
                cited = _citation_value(citation, field, None)
                stored = get_value(source, field, None)
                if cited not in (None, "") and _normalise_text(cited) != _normalise_text(stored):
                    errors.append(f"{chunk_id}: {field} does not match retrieved evidence")
            for field in ("char_start", "char_end"):
                cited = _citation_value(citation, field, None)
                if cited is not None:
                    try:
                        matches = int(cited) == int(get_value(source, field, -1))
                    except (TypeError, ValueError):
                        matches = False
                    if not matches:
                        errors.append(f"{chunk_id}: {field} does not match retrieved evidence")
            cited_time = _citation_value(citation, "publish_time", None)
            if cited_time not in (None, ""):
                left, right = as_datetime(cited_time), as_datetime(source.publish_time)
                if left != right:
                    errors.append(f"{chunk_id}: publish_time does not match retrieved evidence")

            level = str(getattr(source.evidence_level, "value", source.evidence_level)).upper()
            if level in {"C", "D"}:
                warnings.append(f"{chunk_id}: evidence level {level} requires uncertainty context")
            if not any(error.startswith(f"{chunk_id}:") for error in errors):
                validated.append(chunk_id)

        detected_conflicts = list(conflicts) if conflicts is not None else response_conflicts
        if conflicts is None and not response_conflicts and retrieved_items:
            detected_conflicts = ConflictDetector().detect(retrieved_items)
        return CitationValidationResult(
            valid=not errors,
            errors=errors,
            warnings=warnings,
            validated_chunk_ids=validated,
            conflicts=detected_conflicts,
        )


def validate_citations(
    citations: Sequence[Any],
    retrieved: Sequence[Any] | RAGResponse | None = None,
    **options: Any,
) -> CitationValidationResult:
    return CitationValidator().validate(citations, retrieved, **options)
