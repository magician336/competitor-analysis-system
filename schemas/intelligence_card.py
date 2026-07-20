"""Strict, evidence-aware contracts for the week-three Agent layer.

The models in this module deliberately keep LLM-authored prose separate from
code-controlled evidence, scores and identifiers.  This makes every conclusion
auditable and prevents a structured-output model from inventing citations.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .document import (
    DimensionTag,
    EventType,
    EvidenceLevel,
    SourceType,
)


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


def alert_from_priority(score: int) -> "AlertLevel":
    if score >= 80:
        return AlertLevel.RED
    if score >= 60:
        return AlertLevel.ORANGE
    if score >= 40:
        return AlertLevel.YELLOW
    return AlertLevel.BLUE


class AlertLevel(str, Enum):
    BLUE = "blue"
    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


class RiskLevel(str, Enum):
    UNKNOWN = "unknown"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class AgentKind(str, Enum):
    PRICE = "price"
    PRODUCT = "product"
    RISK = "risk"
    DIMENSION_TAGGING = "dimension_tagging"
    BENCHMARK = "benchmark"
    COMPARE = "compare"
    BRIEFING = "briefing"


class FindingType(str, Enum):
    FACT = "fact"
    INFERENCE = "inference"
    RECOMMENDATION = "recommendation"


class ImpactDirection(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class PriorityBreakdown(BaseModel):
    """Transparent implementation of the documented 35/25/20/20 formula."""

    model_config = ConfigDict(extra="forbid")

    event_impact: float = Field(ge=0.0, le=1.0)
    urgency: float = Field(ge=0.0, le=1.0)
    evidence_confidence: float = Field(ge=0.0, le=1.0)
    product_relevance: float = Field(ge=0.0, le=1.0)
    score: int = Field(default=0, ge=0, le=100)

    @model_validator(mode="after")
    def calculate_score(self) -> "PriorityBreakdown":
        self.score = max(
            0,
            min(
                100,
                round(
                    35 * self.event_impact
                    + 25 * self.urgency
                    + 20 * self.evidence_confidence
                    + 20 * self.product_relevance
                ),
            ),
        )
        return self


class EvidenceReference(BaseModel):
    """Minimal immutable source pointer retained on every Agent conclusion."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, frozen=True)

    citation_id: str | None = None
    chunk_id: str = Field(min_length=1)
    document_id: str = Field(min_length=1)
    version_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    url: str = Field(min_length=1)
    competitor: str = Field(min_length=1)
    source_type: SourceType
    evidence_level: EvidenceLevel
    event_type: EventType | None = None
    dimension_tags: list[DimensionTag] = Field(default_factory=list)
    product_version: str | None = None
    publish_time: datetime | None = None
    quote: str | None = None
    final_score: float | None = None

    @field_validator("dimension_tags", mode="after")
    @classmethod
    def deduplicate_dimensions(cls, values: list[DimensionTag]) -> list[DimensionTag]:
        return list(dict.fromkeys(values))

    @field_validator("chunk_id", "document_id", "version_id", "title", "url", "competitor")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @classmethod
    def from_evidence(cls, evidence: Any) -> "EvidenceReference":
        content = str(getattr(evidence, "content", ""))
        return cls(
            citation_id=getattr(evidence, "citation_id", None),
            chunk_id=evidence.chunk_id,
            document_id=evidence.document_id,
            version_id=evidence.version_id,
            title=evidence.title,
            url=evidence.url,
            competitor=evidence.competitor,
            source_type=evidence.source_type,
            evidence_level=evidence.evidence_level,
            event_type=evidence.event_type,
            dimension_tags=list(evidence.dimension_tags or []),
            product_version=evidence.product_version,
            publish_time=evidence.publish_time,
            quote=evidence.quote or content[:500] or None,
            final_score=evidence.final_score,
        )


class StructuredFinding(BaseModel):
    """One fact, inference or recommendation with explicit evidence IDs."""

    model_config = ConfigDict(extra="forbid")

    finding_type: FindingType = FindingType.FACT
    title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    confidence_score: float = Field(ge=0.0, le=1.0)

    @field_validator("title", "summary")
    @classmethod
    def strip_finding_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("finding text must not be blank")
        return stripped

    @field_validator("evidence_chunk_ids", mode="after")
    @classmethod
    def deduplicate_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class CapabilityImpact(BaseModel):
    """Directional effect of one event on a D1--D7 capability."""

    model_config = ConfigDict(extra="forbid")

    dimension: DimensionTag
    direction: ImpactDirection = ImpactDirection.UNKNOWN
    magnitude: int = Field(ge=0, le=10)
    confidence_score: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=1)
    evidence_chunk_ids: list[str] = Field(default_factory=list)

    @field_validator("evidence_chunk_ids", mode="after")
    @classmethod
    def deduplicate_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class AgentAnalysisRequest(BaseModel):
    """Common request accepted by Price/Product/Risk Agents."""

    model_config = ConfigDict(extra="forbid")

    competitor: str = Field(min_length=1)
    question: str | None = None
    event_type: EventType | None = None
    dimension_tags: list[DimensionTag] = Field(default_factory=list)
    product_versions: list[str] = Field(default_factory=list)
    evidence_levels: list[EvidenceLevel] = Field(default_factory=list)
    source_types: list[SourceType] = Field(default_factory=list)
    start_time: datetime | None = None
    end_time: datetime | None = None
    current_only: bool = True
    top_k: int = Field(default=8, ge=1, le=30)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("competitor")
    @classmethod
    def strip_competitor(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("competitor must not be blank")
        return stripped

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator(
        "dimension_tags",
        "product_versions",
        "evidence_levels",
        "source_types",
        mode="after",
    )
    @classmethod
    def deduplicate_lists(cls, values: list[Any]) -> list[Any]:
        if values and isinstance(values[0], str):
            return list(dict.fromkeys(value.strip() for value in values if value.strip()))
        return list(dict.fromkeys(values))

    @model_validator(mode="after")
    def validate_time_range(self) -> "AgentAnalysisRequest":
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValueError("end_time must be greater than or equal to start_time")
        return self


class IntelligenceCard(BaseModel):
    """A scored competitor-intelligence card whose claims are source-bound."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: Literal["1.0"] = "1.0"
    card_id: str = ""
    agent_kind: AgentKind
    competitor: str = Field(min_length=1)
    event_type: EventType
    dimension_tags: list[DimensionTag] = Field(default_factory=list)
    event_title: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    change_before: str = "unknown"
    change_after: str = "unknown"
    impact_analysis: str = Field(min_length=1)
    relevance_to_our_product: str = Field(min_length=1)
    threat_level: RiskLevel = RiskLevel.UNKNOWN
    opportunity: str = Field(min_length=1)
    threat: str = Field(min_length=1)
    recommended_action: str = Field(min_length=1)
    confidence_score: float = Field(ge=0.0, le=1.0)
    priority_score: int = Field(ge=0, le=100)
    priority_breakdown: PriorityBreakdown | None = None
    alert_level: AlertLevel | None = None
    capability_impact: dict[DimensionTag, int] = Field(default_factory=dict)
    impact_details: list[CapabilityImpact] = Field(default_factory=list)
    evidence: list[EvidenceReference] = Field(default_factory=list)
    findings: list[StructuredFinding] = Field(default_factory=list)
    conflict_notes: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    review_required: bool = False
    analysis_mode: Literal["rules", "llm", "hybrid"] = "rules"
    model_name: str | None = None
    rag_query_id: str | None = None
    prompt_name: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("dimension_tags", mode="after")
    @classmethod
    def deduplicate_dimensions(cls, values: list[DimensionTag]) -> list[DimensionTag]:
        return list(dict.fromkeys(values))

    @field_validator("conflict_notes", "assumptions", mode="after")
    @classmethod
    def deduplicate_text(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def complete_and_validate_card(self) -> "IntelligenceCard":
        if self.priority_breakdown is not None:
            self.priority_score = self.priority_breakdown.score
        self.alert_level = alert_from_priority(self.priority_score)

        evidence_ids = {item.chunk_id for item in self.evidence}
        duplicated = len(evidence_ids) != len(self.evidence)
        if duplicated:
            by_id = {item.chunk_id: item for item in self.evidence}
            self.evidence = list(by_id.values())
            evidence_ids = set(by_id)

        for finding in self.findings:
            unknown = set(finding.evidence_chunk_ids) - evidence_ids
            if unknown:
                raise ValueError(
                    "finding references unknown evidence chunk(s): "
                    + ", ".join(sorted(unknown))
                )
            if evidence_ids and not finding.evidence_chunk_ids:
                raise ValueError("every finding must cite at least one retrieved chunk")

        for impact in self.impact_details:
            unknown = set(impact.evidence_chunk_ids) - evidence_ids
            if unknown:
                raise ValueError(
                    "capability impact references unknown evidence chunk(s): "
                    + ", ".join(sorted(unknown))
                )

        if self.impact_details:
            self.capability_impact = {
                item.dimension: item.magnitude for item in self.impact_details
            }
            self.dimension_tags = list(
                dict.fromkeys([*self.dimension_tags, *(item.dimension for item in self.impact_details)])
            )

        if not self.evidence:
            self.review_required = True
            self.confidence_score = min(self.confidence_score, 0.3)
            self.priority_score = min(self.priority_score, 39)
            self.alert_level = AlertLevel.BLUE
            if not self.assumptions:
                self.assumptions.append("未检索到可引用证据，卡片不可作为已证实结论。")

        if self.conflict_notes or self.confidence_score < 0.55:
            self.review_required = True

        if not self.card_id:
            self.card_id = _stable_id(
                "card",
                self.agent_kind,
                self.competitor,
                self.event_type,
                self.event_title,
                sorted(evidence_ids),
            )
        return self


class AgentTraceEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(min_length=1)
    event: Literal["start", "end", "error"]
    at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    detail: str | None = None


class AgentExecutionTrace(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trace_id: str
    agent_kind: AgentKind
    duration_ms: float = Field(default=0.0, ge=0.0)
    events: list[AgentTraceEvent] = Field(default_factory=list)
    llm_used: bool = False
    fallback_used: bool = False
    model_name: str | None = None


class AgentRunResult(BaseModel):
    """HTTP- and CLI-friendly result for one Agent execution."""

    model_config = ConfigDict(extra="forbid")

    request: AgentAnalysisRequest
    rag_query_id: str | None = None
    cards: list[IntelligenceCard] = Field(default_factory=list)
    evidence_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    degraded: bool = False
    trace: AgentExecutionTrace | None = None

    @field_validator("warnings", mode="after")
    @classmethod
    def deduplicate_warnings(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def align_evidence_count(self) -> "AgentRunResult":
        unique_ids = {
            evidence.chunk_id for card in self.cards for evidence in card.evidence
        }
        self.evidence_count = len(unique_ids)
        self.degraded = self.degraded or not unique_ids or any(
            card.review_required for card in self.cards
        )
        return self


__all__ = [
    "AgentAnalysisRequest",
    "AgentExecutionTrace",
    "AgentKind",
    "AgentRunResult",
    "AgentTraceEvent",
    "AlertLevel",
    "CapabilityImpact",
    "EvidenceReference",
    "FindingType",
    "ImpactDirection",
    "IntelligenceCard",
    "PriorityBreakdown",
    "RiskLevel",
    "StructuredFinding",
    "alert_from_priority",
]
