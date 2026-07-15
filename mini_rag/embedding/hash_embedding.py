"""Deterministic, dependency-free embedding for development and tests."""

from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence

from .base_embedding import BaseEmbedding, Vector, l2_normalize


_WORD_RE = re.compile(r"[a-z0-9]+(?:[._+#/-][a-z0-9]+)*", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")


def _feature_tokens(text: str) -> Iterable[str]:
    """Yield word and CJK character n-gram features.

    Character bigrams make the offline provider useful for Chinese queries
    without requiring an external tokenizer.  Word unigrams and adjacent
    bigrams preserve exact product names, versions, and technical terms.
    """

    normalized = unicodedata.normalize("NFKC", text).casefold()
    words = _WORD_RE.findall(normalized)
    for word in words:
        yield f"w:{word}"
    for left, right in zip(words, words[1:]):
        yield f"wb:{left}_{right}"

    for run in _CJK_RE.findall(normalized):
        for char in run:
            yield f"c:{char}"
        for size in (2, 3):
            for index in range(max(0, len(run) - size + 1)):
                yield f"c{size}:{run[index:index + size]}"


class HashEmbedding(BaseEmbedding):
    """Feature-hashing embedding with deterministic signed buckets.

    It is intended as an offline baseline and a reliable fallback when model
    weights are unavailable.  It performs no network access and has no learned
    parameters.
    """

    def __init__(self, dimension: int = 384, *, seed: str = "coderadar-v1") -> None:
        if dimension < 8:
            raise ValueError("hash embedding dimension must be at least 8")
        self._dimension = int(dimension)
        self.seed = seed

    @property
    def model_name(self) -> str:
        return f"hash-embedding/{self.seed}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed_one(self, text: str) -> Vector:
        counts = Counter(_feature_tokens(text or ""))
        vector = [0.0] * self.dimension
        for token, frequency in counts.items():
            digest = hashlib.blake2b(
                token.encode("utf-8"),
                digest_size=16,
                person=self.seed.encode("utf-8")[:16],
            ).digest()
            bucket = int.from_bytes(digest[:8], "big") % self.dimension
            sign = 1.0 if digest[8] & 1 else -1.0
            # Sub-linear term frequency prevents long chunks from being
            # dominated by repeated navigation or boilerplate tokens.
            vector[bucket] += sign * (1.0 + math.log(float(frequency)))
        return l2_normalize(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        vectors = [self._embed_one(str(text)) for text in texts]
        self.validate_vectors(vectors, len(texts))
        return vectors


DeterministicHashEmbedding = HashEmbedding


__all__ = ["DeterministicHashEmbedding", "HashEmbedding"]
