"""Optional Cross-Encoder reranking with a deterministic lexical fallback."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from typing import Any, Protocol

from mini_rag.models import SearchCandidate

from ._common import candidate_id, candidate_text, normalise_scores, score_of, with_candidate


_LATIN_TOKEN = re.compile(r"[a-z0-9]+(?:[._+/#-][a-z0-9]+)*", re.IGNORECASE)
_HAN_RUN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_SPACE = re.compile(r"\s+")


def lexical_tokens(text: str) -> set[str]:
    """Tokenize product terms, versions, and Chinese character n-grams."""

    lowered = text.casefold()
    tokens = {match.group(0) for match in _LATIN_TOKEN.finditer(lowered)}
    for match in _HAN_RUN.finditer(lowered):
        run = match.group(0)
        tokens.update(run)
        tokens.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
        if len(run) <= 8:
            tokens.add(run)
    return {token for token in tokens if token}


def lexical_relevance(query: str, text: str) -> float:
    query_tokens = lexical_tokens(query)
    if not query_tokens:
        return 0.0
    document_tokens = lexical_tokens(text)
    overlap = query_tokens & document_tokens
    coverage = len(overlap) / len(query_tokens)
    union = query_tokens | document_tokens
    jaccard = len(overlap) / len(union) if union else 0.0
    normalised_query = _SPACE.sub("", query.casefold())
    normalised_text = _SPACE.sub("", text.casefold())
    phrase = float(bool(normalised_query) and normalised_query in normalised_text)
    return min(1.0, 0.7 * coverage + 0.2 * jaccard + 0.1 * phrase)


class PairScorer(Protocol):
    def __call__(self, query: str, texts: Sequence[str]) -> Sequence[float]: ...


class TermOverlapReranker:
    """Offline reranker based on exact term coverage and phrase matching."""

    def __init__(self, rerank_weight: float = 0.75) -> None:
        if not 0.0 <= rerank_weight <= 1.0:
            raise ValueError("rerank_weight must be between 0 and 1")
        self.rerank_weight = rerank_weight
        self.last_backend = "term_overlap"
        self.last_error: str | None = None

    def score(self, query: str, candidates: Sequence[Any]) -> list[float]:
        return [lexical_relevance(query, candidate_text(candidate)) for candidate in candidates]

    def rerank(
        self,
        query: str,
        candidates: Sequence[Any],
        *,
        top_k: int | None = None,
    ) -> list[SearchCandidate]:
        if top_k is not None and top_k < 1:
            raise ValueError("top_k must be positive")
        lexical_scores = self.score(query, candidates)
        base_scores = normalise_scores(score_of(candidate) for candidate in candidates)
        ranked: list[SearchCandidate] = []
        for candidate, lexical_score, base_score in zip(candidates, lexical_scores, base_scores):
            final = self.rerank_weight * lexical_score + (1.0 - self.rerank_weight) * base_score
            ranked.append(with_candidate(candidate, rerank_score=lexical_score, final_score=final))
        ranked.sort(key=lambda item: (-score_of(item), candidate_id(item)))
        return ranked if top_k is None else ranked[:top_k]


class CrossEncoderReranker:
    """Use an injected/local Cross-Encoder and fall back to lexical scoring.

    No model is downloaded by default.  Supplying ``model_name`` enables lazy
    loading through ``sentence_transformers`` when that optional dependency is
    installed. Tests and CPU-only deployments can inject an object exposing
    ``predict(pairs, batch_size=...)`` or a simple callable.
    """

    def __init__(
        self,
        model_name: str | None = None,
        *,
        model: Any | None = None,
        scorer: PairScorer | None = None,
        batch_size: int = 16,
        rerank_weight: float = 0.75,
        fallback: TermOverlapReranker | None = None,
        strict: bool = False,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        if not 0.0 <= rerank_weight <= 1.0:
            raise ValueError("rerank_weight must be between 0 and 1")
        self.model_name = model_name
        self.model = model
        self.scorer = scorer
        self.batch_size = batch_size
        self.rerank_weight = rerank_weight
        self.fallback = fallback or TermOverlapReranker(rerank_weight=rerank_weight)
        self.strict = strict
        self._load_attempted = model is not None
        self.last_backend = "uninitialised"
        self.last_error: str | None = None

    def _load_model(self) -> Any | None:
        if self.model is not None or self._load_attempted or not self.model_name:
            return self.model
        self._load_attempted = True
        try:
            from sentence_transformers import CrossEncoder  # type: ignore

            self.model = CrossEncoder(self.model_name)
        except Exception as exc:  # optional dependency/model availability
            self.last_error = f"{type(exc).__name__}: {exc}"
            if self.strict:
                raise RuntimeError(f"failed to load Cross-Encoder {self.model_name!r}") from exc
        return self.model

    def _predict(self, query: str, texts: Sequence[str]) -> list[float]:
        if self.scorer is not None:
            self.last_backend = "injected_scorer"
            return [float(value) for value in self.scorer(query, texts)]
        model = self._load_model()
        if model is None:
            raise RuntimeError(self.last_error or "Cross-Encoder is not configured")
        pairs = [[query, text] for text in texts]
        if hasattr(model, "predict"):
            result = model.predict(pairs, batch_size=self.batch_size)
        elif callable(model):
            result = model(pairs)
        else:
            raise TypeError("Cross-Encoder must be callable or expose predict()")
        self.last_backend = "cross_encoder"
        return [float(value) for value in result]

    @staticmethod
    def _normalise_predictions(predictions: Sequence[float]) -> list[float]:
        values = [float(value) for value in predictions]
        if not values:
            return []
        if all(0.0 <= value <= 1.0 for value in values):
            return values
        normalised = normalise_scores(values)
        if len(set(values)) == 1:
            return [1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value)))) for value in values]
        return normalised

    def rerank(
        self,
        query: str,
        candidates: Sequence[Any],
        *,
        top_k: int | None = None,
    ) -> list[SearchCandidate]:
        if not candidates:
            return []
        texts = [candidate_text(candidate) for candidate in candidates]
        try:
            predictions = self._predict(query, texts)
            if len(predictions) != len(candidates):
                raise ValueError("Cross-Encoder returned a different number of scores")
        except Exception as exc:
            if self.strict:
                raise
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.last_backend = "term_overlap"
            return self.fallback.rerank(query, candidates, top_k=top_k)

        rerank_scores = self._normalise_predictions(predictions)
        base_scores = normalise_scores(score_of(candidate) for candidate in candidates)
        ranked = [
            with_candidate(
                candidate,
                rerank_score=rerank_score,
                final_score=self.rerank_weight * rerank_score + (1.0 - self.rerank_weight) * base_score,
            )
            for candidate, rerank_score, base_score in zip(candidates, rerank_scores, base_scores)
        ]
        ranked.sort(key=lambda item: (-score_of(item), candidate_id(item)))
        return ranked if top_k is None else ranked[:top_k]


LexicalReranker = TermOverlapReranker
Reranker = CrossEncoderReranker
