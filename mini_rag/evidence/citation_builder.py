"""Build source-locatable agent evidence from ranked search candidates."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any

from mini_rag.models import Evidence, SearchCandidate
from mini_rag.ranking._common import as_candidate, candidate_id, chunk_of, get_value
from mini_rag.ranking.reranker import lexical_relevance


_PARAGRAPH_BOUNDARY = re.compile(r"\n\s*\n+|(?<=[。！？!?])\s+")


def _source_metadata(candidate: SearchCandidate) -> dict[str, Any]:
    chunk = candidate.chunk
    metadata = {**chunk.source_metadata, **candidate.metadata}
    for field in (
        "section_type",
        "source_locator",
        "plan_name",
        "price_value",
        "currency",
        "billing_period",
        "applicable_plans",
        "repository",
        "github_kind",
        "github_number",
        "github_state",
        "comment_url",
        "comment_time",
    ):
        value = get_value(chunk, field, None)
        if value not in (None, "", []):
            metadata.setdefault(field, value)
    return metadata


def select_quote(content: str, question: str | None = None, max_characters: int | None = None) -> str:
    """Select an exact source substring suitable for a concise citation."""

    if max_characters is not None and max_characters < 1:
        raise ValueError("max_characters must be positive")
    content = content.strip()
    if not content or max_characters is None or len(content) <= max_characters:
        return content
    segments = [segment.strip() for segment in _PARAGRAPH_BOUNDARY.split(content) if segment.strip()]
    if not segments:
        return content[:max_characters]
    if question:
        best = max(segments, key=lambda segment: lexical_relevance(question, segment))
    else:
        best = segments[0]
    if len(best) <= max_characters:
        return best
    # Slicing preserves exact substring identity required by citation validation.
    return best[:max_characters].rstrip()


class CitationBuilder:
    """Convert ranked candidates into the stable Evidence contract."""

    def __init__(
        self,
        *,
        max_quote_characters: int | None = None,
        quote_selector: Callable[[SearchCandidate, str | None], str] | None = None,
    ) -> None:
        if max_quote_characters is not None and max_quote_characters < 1:
            raise ValueError("max_quote_characters must be positive")
        self.max_quote_characters = max_quote_characters
        self.quote_selector = quote_selector

    def build_one(self, candidate: Any, *, question: str | None = None) -> Evidence:
        normalised = as_candidate(candidate)
        evidence = Evidence.from_candidate(normalised)
        if self.quote_selector is not None:
            quote = self.quote_selector(normalised, question)
        else:
            quote = select_quote(
                normalised.chunk.content,
                question=question,
                max_characters=self.max_quote_characters,
            )
        payload = evidence.model_dump()
        payload.update(citation_id="", quote=quote, metadata=_source_metadata(normalised))
        return Evidence.model_validate(payload)

    def build(
        self,
        candidates: Sequence[Any],
        *,
        question: str | None = None,
        top_k: int | None = None,
    ) -> list[Evidence]:
        if top_k is not None and top_k < 1:
            raise ValueError("top_k must be positive")
        evidence: list[Evidence] = []
        seen: set[str] = set()
        for candidate in candidates:
            identifier = candidate_id(candidate)
            if identifier in seen:
                continue
            seen.add(identifier)
            evidence.append(self.build_one(candidate, question=question))
            if top_k is not None and len(evidence) >= top_k:
                break
        return evidence


def build_evidence(
    candidates: Sequence[Any],
    *,
    question: str | None = None,
    top_k: int | None = None,
    max_quote_characters: int | None = None,
) -> list[Evidence]:
    return CitationBuilder(max_quote_characters=max_quote_characters).build(
        candidates,
        question=question,
        top_k=top_k,
    )

