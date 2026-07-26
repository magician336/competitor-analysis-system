"""Formal read/query API for the phase-three frontend contract."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

import yaml
from fastapi import APIRouter, Depends, Query, Request, Response, status

from backend.auth import actor_ownership
from backend.api_repository import FormalApiRepository, get_formal_api_repository
from backend.config import PROJECT_ROOT
from schemas.api import (
    BriefingDetail,
    BriefingSummary,
    CardDetailResponse,
    CardSummary,
    ComparisonCreateRequest,
    ComparisonResponse,
    ComparisonSummary,
    CompetitorCreate,
    CompetitorResponse,
    CompetitorWrite,
    DimensionResponse,
    EvidenceDetailResponse,
    Page,
    SnapshotDetailResponse,
    SnapshotSummary,
    WorkflowSummary,
)
from schemas.capability_snapshot import CAPABILITY_WEIGHTS
from schemas.document import DimensionTag, EventType, EvidenceLevel, SourceType
from schemas.intelligence_card import AlertLevel, EvidenceReference, IntelligenceCard
from schemas.workflow import WorkflowStatus


router = APIRouter(tags=["Formal API"])
PageNumber = Annotated[int, Query(ge=1)]
PageSize = Annotated[int, Query(ge=1, le=100)]


@router.get("/api/cards", response_model=Page[CardSummary])
def list_cards(
    page: PageNumber = 1,
    page_size: PageSize = 20,
    competitor: str | None = None,
    agent_kind: Literal["price", "product", "risk", "dimension_tagging", "compare", "briefing"] | None = None,
    event_type: EventType | None = None,
    alert_level: AlertLevel | None = None,
    review_required: bool | None = None,
    evidence_level: EvidenceLevel | None = None,
    source_type: SourceType | None = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    min_priority: Annotated[int | None, Query(ge=0, le=100)] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    sort_by: Literal["created_at", "priority_score", "confidence_score"] = "created_at",
    order: Literal["asc", "desc"] = "desc",
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> Page[CardSummary]:
    return repository.list_cards(
        page=page,
        page_size=page_size,
        competitor=competitor,
        agent_kind=agent_kind,
        event_type=event_type.value if event_type else None,
        alert_level=alert_level.value if alert_level else None,
        review_required=review_required,
        evidence_level=evidence_level.value if evidence_level else None,
        source_type=source_type.value if source_type else None,
        min_confidence=min_confidence,
        min_priority=min_priority,
        created_from=created_from,
        created_to=created_to,
        sort_by=sort_by,
        order=order,
    )


@router.get("/api/cards/{card_id}", response_model=CardDetailResponse)
def get_card(card_id: str, repository: FormalApiRepository = Depends(get_formal_api_repository)) -> CardDetailResponse:
    return repository.card_detail(card_id)


@router.get("/api/cards/{card_id}/evidence", response_model=list[EvidenceReference])
def get_card_evidence(card_id: str, repository: FormalApiRepository = Depends(get_formal_api_repository)) -> list[EvidenceReference]:
    return repository.card_evidence(card_id)


@router.get("/api/evidence/{chunk_id}", response_model=EvidenceDetailResponse)
def get_evidence(chunk_id: str, repository: FormalApiRepository = Depends(get_formal_api_repository)) -> EvidenceDetailResponse:
    return repository.evidence_detail(chunk_id)


@router.get("/api/snapshots", response_model=Page[SnapshotSummary])
def list_snapshots(
    page: PageNumber = 1,
    page_size: PageSize = 20,
    competitor: str | None = None,
    snapshot_from: date | None = None,
    snapshot_to: date | None = None,
    product_version: str | None = None,
    scoring_version: str | None = None,
    min_coverage: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    min_confidence: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> Page[SnapshotSummary]:
    return repository.list_snapshots(
        page=page,
        page_size=page_size,
        competitor=competitor,
        snapshot_from=snapshot_from,
        snapshot_to=snapshot_to,
        product_version=product_version,
        scoring_version=scoring_version,
        min_coverage=min_coverage,
        min_confidence=min_confidence,
    )


@router.get("/api/snapshots/{snapshot_id}", response_model=SnapshotDetailResponse)
def get_snapshot(snapshot_id: str, repository: FormalApiRepository = Depends(get_formal_api_repository)) -> SnapshotDetailResponse:
    return repository.snapshot_detail(snapshot_id)


@router.get("/api/snapshots/{snapshot_id}/cards", response_model=list[IntelligenceCard])
def get_snapshot_cards(snapshot_id: str, repository: FormalApiRepository = Depends(get_formal_api_repository)) -> list[IntelligenceCard]:
    return repository.snapshot_cards(snapshot_id)


@router.get("/api/comparisons", response_model=Page[ComparisonSummary])
def list_comparisons(
    page: PageNumber = 1,
    page_size: PageSize = 20,
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> Page[ComparisonSummary]:
    return repository.list_comparisons(page=page, page_size=page_size)


@router.post("/api/comparisons", response_model=ComparisonResponse, status_code=status.HTTP_201_CREATED)
def create_comparison(
    request: ComparisonCreateRequest,
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> ComparisonResponse:
    return repository.create_comparison(request)


@router.get("/api/comparisons/latest", response_model=ComparisonResponse)
def latest_comparison(repository: FormalApiRepository = Depends(get_formal_api_repository)) -> ComparisonResponse:
    return repository.latest_comparison()


@router.get("/api/comparisons/{comparison_id}", response_model=ComparisonResponse)
def get_comparison(comparison_id: str, repository: FormalApiRepository = Depends(get_formal_api_repository)) -> ComparisonResponse:
    return repository.comparison(comparison_id)


@router.get("/api/briefings", response_model=Page[BriefingSummary])
def list_briefings(
    request: Request,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    competitor: str | None = None,
    snapshot_id: str | None = None,
    workflow_id: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> Page[BriefingSummary]:
    user_id, machine_only = actor_ownership(request)
    return repository.list_briefings(
        page=page,
        page_size=page_size,
        user_id=user_id,
        machine_only=machine_only,
        competitor=competitor,
        snapshot_id=snapshot_id,
        workflow_id=workflow_id,
        created_from=created_from,
        created_to=created_to,
    )


@router.get("/api/briefings/{briefing_id}", response_model=BriefingDetail)
def get_briefing(
    briefing_id: str,
    request: Request,
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> BriefingDetail:
    user_id, machine_only = actor_ownership(request)
    return repository.briefing(
        briefing_id, user_id=user_id, machine_only=machine_only
    )


@router.get("/api/briefings/{briefing_id}/content", response_class=Response)
def download_briefing(
    briefing_id: str,
    request: Request,
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> Response:
    user_id, machine_only = actor_ownership(request)
    briefing = repository.briefing(
        briefing_id, user_id=user_id, machine_only=machine_only
    )
    safe_name = re.sub(r"[^A-Za-z0-9_.-]", "-", briefing.briefing_id)
    return Response(
        content=briefing.markdown,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.md"'},
    )


@router.get("/api/workflows", response_model=Page[WorkflowSummary])
def list_workflows(
    request: Request,
    page: PageNumber = 1,
    page_size: PageSize = 20,
    competitor: str | None = None,
    workflow_status: WorkflowStatus | None = Query(default=None, alias="status"),
    analysis_mode: Literal["rules", "hybrid", "llm"] | None = None,
    correlation_id: str | None = None,
    submitted_from: datetime | None = None,
    submitted_to: datetime | None = None,
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> Page[WorkflowSummary]:
    user_id, machine_only = actor_ownership(request)
    return repository.list_workflows(
        page=page,
        page_size=page_size,
        user_id=user_id,
        machine_only=machine_only,
        competitor=competitor,
        workflow_status=workflow_status.value if workflow_status else None,
        analysis_mode=analysis_mode,
        correlation_id=correlation_id,
        submitted_from=submitted_from,
        submitted_to=submitted_to,
    )


@router.get("/api/competitors", response_model=list[CompetitorResponse])
def list_competitors(
    enabled_only: bool = False,
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> list[CompetitorResponse]:
    return repository.list_competitors(enabled_only=enabled_only)


@router.post("/api/competitors", response_model=CompetitorResponse, status_code=status.HTTP_201_CREATED)
def create_competitor(request: CompetitorCreate, repository: FormalApiRepository = Depends(get_formal_api_repository)) -> CompetitorResponse:
    return repository.create_competitor(request)


@router.put("/api/competitors/{competitor_id}", response_model=CompetitorResponse)
def update_competitor(
    competitor_id: str,
    request: CompetitorWrite,
    repository: FormalApiRepository = Depends(get_formal_api_repository),
) -> CompetitorResponse:
    return repository.update_competitor(competitor_id, request)


@router.delete("/api/competitors/{competitor_id}", status_code=status.HTTP_204_NO_CONTENT)
def disable_competitor(competitor_id: str, repository: FormalApiRepository = Depends(get_formal_api_repository)) -> Response:
    repository.disable_competitor(competitor_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/api/dimensions", response_model=list[DimensionResponse])
def list_dimensions() -> list[DimensionResponse]:
    dimensions_payload = yaml.safe_load(
        (PROJECT_ROOT / "config" / "dimensions.yaml").read_text(encoding="utf-8")
    )
    scoring_payload = yaml.safe_load(
        (PROJECT_ROOT / "config" / "scoring.yaml").read_text(encoding="utf-8")
    )
    return [
        DimensionResponse(
            dimension=dimension,
            code=dimensions_payload["dimensions"][dimension.value]["code"],
            name=dimensions_payload["dimensions"][dimension.value]["name"],
            weight=CAPABILITY_WEIGHTS[dimension],
            keywords=dimensions_payload["dimensions"][dimension.value]["keywords"],
            scoring_version=scoring_payload["version"],
        )
        for dimension in DimensionTag
    ]


__all__ = ["router"]
