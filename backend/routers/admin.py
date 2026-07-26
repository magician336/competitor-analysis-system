"""Read-only admin overview and safe manual document import endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.services.admin_documents import (
    MAX_UPLOAD_BYTES,
    AdminImportError,
    build_overview,
    dimensions_from_form,
    list_document_summaries,
    parse_uploaded_documents,
    persist_documents,
)
from backend.services.rag_service import get_rag_service
from mini_rag.api import MiniRAGService
from schemas.api import Page
from schemas.document import DimensionTag, SourceType


router = APIRouter(prefix="/api/admin", tags=["Admin"])


class DocumentStats(BaseModel):
    documents_total: int
    current_versions: int
    historical_versions: int
    pending_review: int


class DocumentDistributions(BaseModel):
    competitors: dict[str, int]
    source_types: dict[str, int]


class RecentDocument(BaseModel):
    document_id: str
    version_id: str
    title: str
    competitor: str
    source_type: str
    publish_time: str | None = None
    is_current: bool


class AdminOverviewResponse(BaseModel):
    dataset_updated_at: str
    stats: DocumentStats
    distributions: DocumentDistributions
    recent_documents: list[RecentDocument]


class AdminImportResponse(BaseModel):
    status: Literal["success", "partial_failure", "save_failed"]
    persisted: bool
    documents_imported: int = 0
    documents_skipped: int = 0
    chunks_generated: int = 0
    chunks_indexed: int = 0
    chunks_skipped: int = 0
    indexed_chunks_total: int | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class AdminDocumentSummary(BaseModel):
    document_id: str
    version_id: str
    title: str
    competitor: str
    source_type: SourceType
    publish_time: datetime | None = None
    dimension_tags: list[DimensionTag] = Field(default_factory=list)
    is_current: bool
    needs_review: bool


@router.get("/overview", response_model=AdminOverviewResponse)
def admin_overview(
    service: MiniRAGService = Depends(get_rag_service),
) -> dict[str, Any]:
    try:
        return build_overview(service.settings.documents_path)
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Structured document data set is unavailable: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Structured document data set is invalid: {exc}",
        ) from exc


@router.get("/documents", response_model=Page[AdminDocumentSummary])
def list_admin_documents(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    q: Annotated[str | None, Query(max_length=200)] = None,
    status_filter: Annotated[
        Literal["all", "current", "historical", "review"],
        Query(alias="status"),
    ] = "all",
    competitor: Annotated[str | None, Query(max_length=200)] = None,
    source_type: Annotated[str | None, Query(max_length=100)] = None,
    service: MiniRAGService = Depends(get_rag_service),
) -> dict[str, Any]:
    normalized_source_type = source_type.strip().casefold() if source_type else None
    if normalized_source_type and normalized_source_type not in {
        item.value for item in SourceType
    }:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown source_type: {source_type}",
        )
    try:
        return list_document_summaries(
            service.settings.documents_path,
            page=page,
            page_size=page_size,
            query=q,
            status=status_filter,
            competitor=competitor,
            source_type=normalized_source_type,
        )
    except FileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Structured document data set is unavailable: {exc}",
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Structured document data set is invalid: {exc}",
        ) from exc


@router.post(
    "/documents/import",
    response_model=AdminImportResponse,
    responses={500: {"model": AdminImportResponse}},
)
async def import_documents(
    file: UploadFile = File(...),
    competitor: str | None = Form(default=None),
    title: str | None = Form(default=None),
    source_url: str | None = Form(default=None),
    publish_time: str | None = Form(default=None),
    dimension_tags: str | None = Form(default=None),
    service: MiniRAGService = Depends(get_rag_service),
) -> AdminImportResponse | JSONResponse:
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="The uploaded file exceeds the 5 MB limit.",
        )
    try:
        parsed_dimensions = dimensions_from_form(dimension_tags)
        documents, _raw_path = parse_uploaded_documents(
            filename=file.filename or "upload",
            content=content,
            project_root=service.settings.project_root,
            competitor=competitor,
            title=title,
            source_url=source_url,
            publish_time=publish_time,
            dimension_tags=parsed_dimensions,
        )
    except AdminImportError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    try:
        imported, skipped, persisted, _backup = persist_documents(
            service.settings.documents_path,
            documents,
        )
    except Exception as exc:
        response = AdminImportResponse(
            status="save_failed",
            persisted=False,
            errors=[f"Document persistence failed: {type(exc).__name__}: {exc}"],
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=response.model_dump(mode="json"),
        )

    warnings: list[str] = []
    if skipped:
        warnings.append(
            f"{skipped} duplicate document version(s) were skipped; index synchronization still ran."
        )
    try:
        result = service.build_index(
            service.settings.documents_path,
            rebuild=False,
            delete_missing=False,
        )
        report = result.get("report", {})
        failures = int(report.get("failed_count", 0) or 0)
        report_errors = [str(item) for item in report.get("errors", [])]
        if failures:
            report_errors.insert(0, f"Incremental indexing reported {failures} failure(s).")
        try:
            indexed_chunks_total = service.health().get("indexed_chunks")
        except Exception as exc:
            indexed_chunks_total = None
            warnings.append(
                f"Index synchronization completed, but status refresh failed: {type(exc).__name__}."
            )
        response = AdminImportResponse(
            status="partial_failure" if failures or report_errors else "success",
            persisted=persisted,
            documents_imported=imported,
            documents_skipped=skipped,
            chunks_generated=int(result.get("chunks", 0) or 0),
            chunks_indexed=int(report.get("indexed_count", 0) or 0),
            chunks_skipped=int(report.get("skipped_count", 0) or 0),
            indexed_chunks_total=indexed_chunks_total,
            warnings=warnings,
            errors=report_errors,
        )
        return response
    except Exception as exc:
        try:
            indexed_chunks_total = service.health().get("indexed_chunks")
        except Exception:
            indexed_chunks_total = None
        return AdminImportResponse(
            status="partial_failure",
            persisted=persisted,
            documents_imported=imported,
            documents_skipped=skipped,
            indexed_chunks_total=indexed_chunks_total,
            warnings=warnings,
            errors=[f"Incremental indexing failed: {type(exc).__name__}: {exc}"],
        )


__all__ = [
    "AdminDocumentSummary",
    "AdminImportResponse",
    "AdminOverviewResponse",
    "router",
]
