"""Embedding provider contracts used by indexing and dense retrieval.

Providers deliberately expose a very small synchronous interface.  This keeps
the rest of Mini-RAG independent from a particular model library or hosted
embedding API.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from math import sqrt


Vector = list[float]


def l2_normalize(vector: Sequence[float]) -> Vector:
    """Return an L2-normalized copy, preserving an all-zero vector."""

    norm = sqrt(sum(float(value) ** 2 for value in vector))
    if norm == 0.0:
        return [0.0 for _ in vector]
    return [float(value) / norm for value in vector]


class BaseEmbedding(ABC):
    """Abstract embedding provider.

    Every provider must produce a fixed-size vector and expose a stable model
    name.  Index mappings use these two properties as compatibility metadata.
    """

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Stable model identifier written into every indexed chunk."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Number of floating-point values returned for each text."""

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        """Embed document texts in input order."""

    def embed_query(self, text: str) -> Vector:
        """Embed one query in the same vector space as documents."""

        vectors = self.embed_documents([text])
        if len(vectors) != 1:
            raise RuntimeError("embedding provider returned an invalid query batch")
        return vectors[0]

    def embed(self, texts: Sequence[str]) -> list[Vector]:
        """Compatibility alias for :meth:`embed_documents`."""

        return self.embed_documents(texts)

    def encode(self, texts: str | Sequence[str]) -> Vector | list[Vector]:
        """Sentence-transformers compatible convenience method."""

        if isinstance(texts, str):
            return self.embed_query(texts)
        return self.embed_documents(texts)

    def validate_vectors(self, vectors: Sequence[Sequence[float]], expected: int) -> None:
        """Validate provider output before it is sent to a fixed mapping."""

        if len(vectors) != expected:
            raise ValueError(
                f"embedding provider returned {len(vectors)} vectors for {expected} texts"
            )
        invalid = [index for index, vector in enumerate(vectors) if len(vector) != self.dimension]
        if invalid:
            raise ValueError(
                f"embedding dimension mismatch at positions {invalid[:5]}; "
                f"expected {self.dimension}"
            )


# A more descriptive alias for integrations that prefer the word "provider".
EmbeddingProvider = BaseEmbedding


__all__ = ["BaseEmbedding", "EmbeddingProvider", "Vector", "l2_normalize"]
