"""HTTP endpoints for Mini-RAG queries, evidence, indexing and evaluation."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from backend.services.rag_service import get_rag_service
from mini_rag.api import MiniRAGService
from mini_rag.models import (
    CitationValidationResult,
    EvaluationCase,
    EvaluationResult,
    Evidence,
    RAGQuery,
    RAGResponse,
)


router = APIRouter(prefix="/api/rag", tags=["Mini-RAG"])


class IndexRequest(BaseModel):
    documents_path: str | None = None
    delete_missing: bool = False


class EvaluationRequest(BaseModel):
    cases: list[EvaluationCase] = Field(min_length=1)
    run_ablations: bool = False


class CitationValidationRequest(BaseModel):
    query_id: str = Field(min_length=1)
    citations: list[dict[str, Any]] = Field(min_length=1)


class IndexingReportResponse(BaseModel):
    index_name: str
    input_count: int
    indexed_count: int
    skipped_count: int
    deleted_count: int
    failed_count: int
    embedding_model: str
    embedding_dimension: int
    created_index: bool
    alias: str | None = None
    errors: list[str] = Field(default_factory=list)


class IndexBuildResponse(BaseModel):
    documents: int
    chunks: int
    report: IndexingReportResponse


class IndexStatusResponse(BaseModel):
    status: str
    index: str
    physical_index: str | None = None
    indexed_chunks: int | None = None
    embedding_model: str
    embedding_dimension: int
    index_embedding_model: str | None = None
    index_embedding_dimension: int | None = None
    embedding_compatible: bool = False
    backend: dict[str, Any] = Field(default_factory=dict)


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, FileNotFoundError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    if isinstance(exc, (ValueError, KeyError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Mini-RAG unavailable: {type(exc).__name__}: {exc}",
    )


@router.post("/query", response_model=RAGResponse)
def query_rag(
    request: RAGQuery,
    service: MiniRAGService = Depends(get_rag_service),
) -> RAGResponse:
    try:
        return service.query(request)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.get("/evidence/{chunk_id}")
def get_evidence(
    chunk_id: str,
    service: MiniRAGService = Depends(get_rag_service),
) -> Evidence:
    try:
        evidence = service.get_evidence(chunk_id)
    except Exception as exc:
        raise _service_error(exc) from exc
    if evidence is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="evidence not found")
    return evidence


@router.get("/trace/{query_id}")
def get_trace(
    query_id: str,
    service: MiniRAGService = Depends(get_rag_service),
) -> RAGResponse:
    trace = service.get_trace(query_id)
    if trace is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="query trace not found")
    return trace


@router.get("/index/status")
def index_status(
    service: MiniRAGService = Depends(get_rag_service),
) -> IndexStatusResponse:
    return service.health()


@router.post("/rebuild")
def rebuild_index(
    request: IndexRequest,
    service: MiniRAGService = Depends(get_rag_service),
) -> IndexBuildResponse:
    try:
        result = service.build_index(request.documents_path, rebuild=True)
        if result["report"].get("failed_count", 0):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=result,
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/index/incremental")
def incremental_index(
    request: IndexRequest,
    service: MiniRAGService = Depends(get_rag_service),
) -> IndexBuildResponse:
    try:
        result = service.build_index(
            request.documents_path,
            rebuild=False,
            delete_missing=request.delete_missing,
        )
        if result["report"].get("failed_count", 0):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=result,
            )
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/evaluate")
def evaluate(
    request: EvaluationRequest,
    service: MiniRAGService = Depends(get_rag_service),
) -> EvaluationResult | dict[str, EvaluationResult]:
    try:
        if request.run_ablations:
            return service.evaluate_ablations(request.cases)
        return service.evaluate(request.cases)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/citations/validate")
def validate_citations(
    request: CitationValidationRequest,
    service: MiniRAGService = Depends(get_rag_service),
) -> CitationValidationResult:
    try:
        return service.validate_citations(request.citations, query_id=request.query_id)
    except Exception as exc:
        raise _service_error(exc) from exc
