"""Configurable dense-vector generation."""

from .base_embedding import BaseEmbedding, EmbeddingProvider, Vector, l2_normalize
from .embedding_service import EmbeddingBatch, EmbeddingService, create_embedding_provider
from .hash_embedding import DeterministicHashEmbedding, HashEmbedding
from .sentence_transformer import SentenceTransformerEmbedding

__all__ = [
    "BaseEmbedding",
    "DeterministicHashEmbedding",
    "EmbeddingBatch",
    "EmbeddingProvider",
    "EmbeddingService",
    "HashEmbedding",
    "SentenceTransformerEmbedding",
    "Vector",
    "create_embedding_provider",
    "l2_normalize",
]
