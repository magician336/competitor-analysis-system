"""Formal phase-three HTTP contracts."""

from __future__ import annotations

from datetime import date, datetime
from math import ceil
from typing import Any, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .capability_snapshot import CapabilitySnapshot
from .comparison import CapabilityComparisonMatrix, ComparisonRules
from .document import DimensionTag, EventType
from .intelligence_card import AgentKind, AlertLevel, EvidenceReference, IntelligenceCard


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid")

    items: list[T]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
    total_pages: int = Field(ge=0)

    @classmethod
    def build(cls, items: list[T], *, page: int, page_size: int, total: int):
        return cls(
            items=items,
            page=page,
            page_size=page_size,
            total=total,
            total_pages=ceil(total / page_size) if total else 0,
        )


class CardSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card_id: str
    competitor: str
    agent_kind: AgentKind
    event_type: EventType
    event_title: str
    summary: str
    alert_level: AlertLevel
    confidence_score: float
    priority_score: int
    review_required: bool
    evidence_count: int
    publish_time: datetime | None
    created_at: datetime


class CardDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    card: IntelligenceCard
    evidence_links: list[EvidenceReference]


class EvidenceDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence: EvidenceReference
    card_ids: list[str]


class SnapshotSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot_id: str
    competitor: str
    snapshot_date: date
    product_version: str
    scoring_version: str
    total_score: float
    overall_confidence: float
    coverage_ratio: float
    previous_snapshot_id: str | None
    source_kind: str
    created_at: datetime


class SnapshotDetailResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: CapabilitySnapshot
    source_kind: str
    provenance: dict[str, Any]


class ComparisonCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    snapshot_ids: list[str] = Field(min_length=2)
    baseline_product: str = Field(default="CodeMate Campus", min_length=1)
    previous_snapshot_ids: list[str] = Field(default_factory=list)
    rules: ComparisonRules = Field(default_factory=ComparisonRules)

    @model_validator(mode="after")
    def unique_ids(self) -> "ComparisonCreateRequest":
        if len(set(self.snapshot_ids)) != len(self.snapshot_ids):
            raise ValueError("snapshot_ids must be unique")
        if len(set(self.previous_snapshot_ids)) != len(self.previous_snapshot_ids):
            raise ValueError("previous_snapshot_ids must be unique")
        return self


class ComparisonResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_id: str
    request_fingerprint: str
    official_ranking_ready: bool
    matrix: CapabilityComparisonMatrix
    provenance: dict[str, Any]
    created_at: datetime


class ComparisonSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    comparison_id: str
    baseline_product: str
    scoring_version: str
    comparison_date: date
    official_ranking_ready: bool
    snapshot_ids: list[str]
    created_at: datetime


class BriefingSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    briefing_id: str
    competitor: str
    snapshot_id: str | None
    workflow_id: str | None
    created_at: datetime


class BriefingDetail(BriefingSummary):
    markdown: str


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    competitor: str
    analysis_mode: str = "rules"
    status: str
    progress: int
    attempt_count: int
    max_attempts: int
    partial_failure: bool
    correlation_id: str | None
    submitted_at: datetime | None
    completed_at: datetime | None
    updated_at: datetime | None
    last_error: dict[str, Any] | None


class CompetitorWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=160)
    aliases: list[str] = Field(default_factory=list)
    sources: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class CompetitorCreate(CompetitorWrite):
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,79}$")


class CompetitorResponse(CompetitorWrite):
    id: str
    config_version: int
    created_at: datetime
    updated_at: datetime


class DimensionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: DimensionTag
    code: str
    name: str
    weight: float
    keywords: list[str]
    scoring_version: str


class ProblemDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    title: str
    status: int
    detail: str
    code: str
    request_id: str
    instance: str | None = None
    errors: list[dict[str, Any]] = Field(default_factory=list)


SortOrder = Literal["asc", "desc"]


__all__ = [name for name in globals() if not name.startswith("_")]
