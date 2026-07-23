"""CodeRadar Mini-RAG retrieval and evidence package."""

from .models import (
    Chunk,
    CitationValidationResult,
    Conflict,
    EvaluationCase,
    EvaluationResult,
    Evidence,
    RAGQuery,
    RAGResponse,
    RetrievalTrace,
    SearchCandidate,
)


def __getattr__(name: str):
    if name in {"MiniRAGSettings", "load_settings"}:
        from .config import MiniRAGSettings, load_settings

        return {"MiniRAGSettings": MiniRAGSettings, "load_settings": load_settings}[name]
    raise AttributeError(name)


__all__ = [
    "Chunk",
    "CitationValidationResult",
    "Conflict",
    "EvaluationCase",
    "EvaluationResult",
    "Evidence",
    "MiniRAGSettings",
    "RAGQuery",
    "RAGResponse",
    "RetrievalTrace",
    "SearchCandidate",
    "load_settings",
]
