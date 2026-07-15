"""Optional sentence-transformers embedding provider."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .base_embedding import BaseEmbedding, Vector


class SentenceTransformerEmbedding(BaseEmbedding):
    """Lazy wrapper around ``sentence_transformers.SentenceTransformer``.

    Import and model loading happen only when this provider is selected.  The
    base Mini-RAG installation therefore remains usable without PyTorch or
    downloaded model weights.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-small-zh-v1.5",
        *,
        device: str = "cpu",
        normalize_embeddings: bool = True,
        model: Any | None = None,
        revision: str | None = None,
        cache_folder: str | None = None,
        query_prefix: str = "",
        document_prefix: str = "",
    ) -> None:
        self._model_name = model_name
        self.device = device
        self.normalize_embeddings = normalize_embeddings
        self.revision = revision
        self.cache_folder = cache_folder
        self.query_prefix = query_prefix
        self.document_prefix = document_prefix
        self._model = model
        self._dimension: int | None = None

    def _load_model(self) -> Any:
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "sentence-transformers is required for the selected embedding provider; "
                    "install it or select the 'hash' provider"
                ) from exc
            kwargs: dict[str, Any] = {"device": self.device}
            if self.revision:
                kwargs["revision"] = self.revision
            if self.cache_folder:
                kwargs["cache_folder"] = self.cache_folder
            self._model = SentenceTransformer(self._model_name, **kwargs)
        return self._model

    @property
    def model_name(self) -> str:
        suffix = f"@{self.revision}" if self.revision else ""
        return f"sentence-transformers/{self._model_name}{suffix}"

    @property
    def dimension(self) -> int:
        if self._dimension is None:
            model = self._load_model()
            dimension_getter = getattr(model, "get_embedding_dimension", None)
            if dimension_getter is None:
                dimension_getter = model.get_sentence_embedding_dimension
            dimension = dimension_getter()
            if not dimension:
                raise RuntimeError("sentence-transformers model did not report its dimension")
            self._dimension = int(dimension)
        return self._dimension

    def _encode(self, texts: Sequence[str], *, prefix: str) -> list[Vector]:
        model = self._load_model()
        prepared = [f"{prefix}{text}" for text in texts]
        result = model.encode(
            prepared,
            batch_size=max(1, min(64, len(prepared))),
            convert_to_numpy=True,
            normalize_embeddings=self.normalize_embeddings,
            show_progress_bar=False,
        )
        vectors = [[float(value) for value in row] for row in result]
        self.validate_vectors(vectors, len(texts))
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return []
        return self._encode(texts, prefix=self.document_prefix)

    def embed_query(self, text: str) -> Vector:
        return self._encode([text], prefix=self.query_prefix)[0]


__all__ = ["SentenceTransformerEmbedding"]
