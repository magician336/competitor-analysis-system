"""HTTP endpoints for evidence-backed week-three Agents."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from backend.services.agent_service import AgentService, get_agent_service
from schemas.capability_snapshot import CapabilitySnapshot
from schemas.intelligence_card import (
    AgentAnalysisRequest,
    AgentRunResult,
    IntelligenceCard,
)


router = APIRouter(prefix="/api/agent", tags=["Agents"])


class SnapshotRequest(BaseModel):
    competitor: str = Field(min_length=1)
    product_version: str | None = None


class BriefingResponse(BaseModel):
    competitor: str
    markdown: str
    cards: list[IntelligenceCard]
    snapshot: CapabilitySnapshot


def _service_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (ValueError, KeyError)):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=f"Agent service unavailable: {type(exc).__name__}: {exc}",
    )


@router.post("/price", response_model=AgentRunResult)
def analyze_price(
    request: AgentAnalysisRequest,
    service: AgentService = Depends(get_agent_service),
) -> AgentRunResult:
    try:
        return service.analyze_price(request)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/product", response_model=AgentRunResult)
def analyze_product(
    request: AgentAnalysisRequest,
    service: AgentService = Depends(get_agent_service),
) -> AgentRunResult:
    try:
        return service.analyze_product(request)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.post("/sentiment-risk", response_model=AgentRunResult)
def analyze_sentiment_risk(
    request: AgentAnalysisRequest,
    service: AgentService = Depends(get_agent_service),
) -> AgentRunResult:
    try:
        return service.analyze_risk(request)
    except Exception as exc:
        raise _service_error(exc) from exc


@router.get("/cards", response_model=list[IntelligenceCard])
def list_cards(
    competitor: str | None = Query(default=None),
    service: AgentService = Depends(get_agent_service),
) -> list[IntelligenceCard]:
    return service.list_cards(competitor)


@router.get("/cards/{card_id}", response_model=IntelligenceCard)
def get_card(
    card_id: str,
    service: AgentService = Depends(get_agent_service),
) -> IntelligenceCard:
    card = service.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="card not found")
    return card


@router.post("/compare", response_model=CapabilitySnapshot)
def build_snapshot(
    request: SnapshotRequest,
    service: AgentService = Depends(get_agent_service),
) -> CapabilitySnapshot:
    return service.build_snapshot(
        request.competitor,
        product_version=request.product_version,
    )


@router.get("/snapshots", response_model=list[CapabilitySnapshot])
def list_snapshots(
    competitor: str | None = Query(default=None),
    service: AgentService = Depends(get_agent_service),
) -> list[CapabilitySnapshot]:
    return service.list_snapshots(competitor)


@router.post("/briefing", response_model=BriefingResponse)
def generate_briefing(
    request: SnapshotRequest,
    service: AgentService = Depends(get_agent_service),
) -> dict[str, Any]:
    return service.generate_briefing(
        request.competitor,
        product_version=request.product_version,
    )
