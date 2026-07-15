"""Lazy singleton adapter for the shared Mini-RAG service."""

from __future__ import annotations

import threading

from mini_rag.api import MiniRAGService, create_service, query_rag


_lock = threading.RLock()
_service: MiniRAGService | None = None


def get_rag_service() -> MiniRAGService:
    global _service
    with _lock:
        if _service is None:
            _service = create_service()
        return _service


def set_rag_service(service: MiniRAGService | None) -> None:
    """Override the process service for tests or controlled reconfiguration."""

    global _service
    with _lock:
        _service = service


__all__ = ["MiniRAGService", "get_rag_service", "query_rag", "set_rag_service"]
