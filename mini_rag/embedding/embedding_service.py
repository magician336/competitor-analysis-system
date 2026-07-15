"""Batched embedding orchestration with a bounded in-process cache."""

from __future__ import annotations

import hashlib
from collections import OrderedDict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from .base_embedding import BaseEmbedding, Vector
from .hash_embedding import HashEmbedding
from .sentence_transformer import SentenceTransformerEmbedding


@dataclass(frozen=True)
class EmbeddingBatch:
    """Vectors plus compatibility metadata needed by an index builder."""

    vectors: list[Vector]
    model_name: str
    dimension: int


def create_embedding_provider(
    provider: str = "hash",
    *,
    model_name: str | None = None,
    dimension: int = 384,
    device: str = "cpu",
    **kwargs: Any,
) -> BaseEmbedding:
    """Create one configured provider by its stable short name."""

    normalized = provider.strip().casefold().replace("_", "-")
    if normalized in {"hash", "deterministic-hash", "offline"}:
        return HashEmbedding(dimension=dimension, seed=kwargs.pop("seed", "coderadar-v1"))
    if normalized in {"sentence-transformer", "sentence-transformers", "st"}:
        return SentenceTransformerEmbedding(
            model_name=model_name or "BAAI/bge-small-zh-v1.5",
            device=device,
            **kwargs,
        )
    raise ValueError(f"unknown embedding provider: {provider}")


class EmbeddingService:
    """Validate, batch, and cache calls to one embedding provider."""

    def __init__(
        self,
        provider: BaseEmbedding | None = None,
        *,
        batch_size: int = 32,
        cache_size: int = 2_048,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if cache_size < 0:
            raise ValueError("cache_size cannot be negative")
        self.provider = provider or HashEmbedding()
        self.batch_size = int(batch_size)
        self.cache_size = int(cache_size)
        self._cache: OrderedDict[str, Vector] = OrderedDict()

    @property
    def model_name(self) -> str:
        return self.provider.model_name

    @property
    def dimension(self) -> int:
        return self.provider.dimension

    def _cache_key(self, text: str) -> str:
        payload = f"{self.model_name}\0{text}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _remember(self, key: str, vector: Vector) -> None:
        if self.cache_size == 0:
            return
        self._cache[key] = list(vector)
        self._cache.move_to_end(key)
        while len(self._cache) > self.cache_size:
            self._cache.popitem(last=False)

    def clear_cache(self) -> None:
        self._cache.clear()

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        normalized = [str(text) for text in texts]
        output: list[Vector | None] = [None] * len(normalized)
        missing: OrderedDict[str, tuple[str, list[int]]] = OrderedDict()

        for index, text in enumerate(normalized):
            key = self._cache_key(text)
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                output[index] = list(cached)
                continue
            if key not in missing:
                missing[key] = (text, [])
            missing[key][1].append(index)

        entries = list(missing.items())
        for offset in range(0, len(entries), self.batch_size):
            batch = entries[offset : offset + self.batch_size]
            batch_texts = [entry[1][0] for entry in batch]
            vectors = self.provider.embed_documents(batch_texts)
            self.provider.validate_vectors(vectors, len(batch_texts))
            for (key, (_, positions)), vector in zip(batch, vectors, strict=True):
                copied = [float(value) for value in vector]
                self._remember(key, copied)
                for position in positions:
                    output[position] = list(copied)

        if any(vector is None for vector in output):  # defensive provider boundary
            raise RuntimeError("embedding service failed to populate every output vector")
        return [vector for vector in output if vector is not None]

    def embed_query(self, text: str) -> Vector:
        # Query-specific prefixes on learned providers must be respected, so
        # only the document path uses the shared cache.
        vector = self.provider.embed_query(str(text))
        self.provider.validate_vectors([vector], 1)
        return [float(value) for value in vector]

    def embed_batch(self, texts: Sequence[str]) -> EmbeddingBatch:
        return EmbeddingBatch(
            vectors=self.embed_documents(texts),
            model_name=self.model_name,
            dimension=self.dimension,
        )

    def embed_chunks(self, chunks: Iterable[Any]) -> dict[str, Vector]:
        materialized = list(chunks)
        texts = [str(_field(chunk, "content", "")) for chunk in materialized]
        vectors = self.embed_documents(texts)
        return {
            str(_field(chunk, "chunk_id")): vector
            for chunk, vector in zip(materialized, vectors, strict=True)
        }


def _field(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


__all__ = ["EmbeddingBatch", "EmbeddingService", "create_embedding_provider"]
