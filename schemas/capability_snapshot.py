"""Traceable D1--D7 capability snapshot contracts for week three."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .document import DimensionTag, EvidenceLevel, SourceType
from .intelligence_card import ImpactDirection


CAPABILITY_WEIGHTS: dict[DimensionTag, float] = {
    DimensionTag.CODE_INTELLIGENCE: 0.20,
    DimensionTag.AGENT_CONTEXT: 0.18,
    DimensionTag.IDE_ECOSYSTEM: 0.12,
    DimensionTag.MODEL_EXTENSIBILITY: 0.10,
    DimensionTag.PERFORMANCE_COST: 0.10,
    DimensionTag.SECURITY_COMPLIANCE: 0.12,
    DimensionTag.EDUCATION_FIT: 0.18,
}


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return f"{prefix}_{digest}"


class CapabilityScoreStatus(str, Enum):
    SCORED = "scored"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class EvidenceContribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_id: str = Field(min_length=1)
    card_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    source_type: SourceType
    evidence_level: EvidenceLevel
    direction: ImpactDirection
    magnitude: int = Field(ge=0, le=10)
    authority_weight: float = Field(ge=0.0, le=1.0)
    freshness_weight: float = Field(ge=0.0, le=1.0)
    confidence_weight: float = Field(ge=0.0, le=1.0)
    publish_time: datetime | None = None


class BenchmarkContribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=100.0)
    task_success: bool
    test_pass_rate: float = Field(ge=0.0, le=1.0)
    harmful_action: bool


class CapabilityScore(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dimension: DimensionTag
    score: int = Field(ge=0, le=100)
    status: CapabilityScoreStatus = CapabilityScoreStatus.SCORED
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_count: int = Field(ge=0)
    independent_source_count: int = Field(default=0, ge=0)
    evidence_levels: dict[EvidenceLevel, int] = Field(default_factory=dict)
    evidence_chunk_ids: list[str] = Field(default_factory=list)
    source_card_ids: list[str] = Field(default_factory=list)
    benchmark_run_ids: list[str] = Field(default_factory=list)
    evidence_score: float | None = Field(default=None, ge=0.0, le=100.0)
    benchmark_score: float | None = Field(default=None, ge=0.0, le=100.0)
    delta: int | None = Field(default=None, ge=-100, le=100)
    evidence_contributions: list[EvidenceContribution] = Field(default_factory=list)
    benchmark_contributions: list[BenchmarkContribution] = Field(default_factory=list)
    rationale: str = Field(min_length=1)

    @field_validator(
        "evidence_chunk_ids",
        "source_card_ids",
        "benchmark_run_ids",
        mode="after",
    )
    @classmethod
    def deduplicate_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def align_status(self) -> "CapabilityScore":
        self.evidence_count = len(self.evidence_chunk_ids)
        self.independent_source_count = max(
            self.independent_source_count,
            len(
                {
                    (item.source_type, item.url)
                    for item in self.evidence_contributions
                }
            ),
        )
        if self.evidence_score is None and self.benchmark_score is None:
            self.status = CapabilityScoreStatus.INSUFFICIENT_EVIDENCE
            self.score = 0
            self.confidence = 0.0
        return self


class CapabilitySnapshot(BaseModel):
    """One versioned, reproducible seven-dimension competitor snapshot."""

    model_config = ConfigDict(extra="forbid")

    snapshot_id: str = ""
    competitor: str = Field(min_length=1)
    snapshot_date: date = Field(default_factory=date.today)
    product_version: str | None = None
    scoring_version: str = Field(default="week3-evidence-v2", min_length=1)
    window_start: datetime | None = None
    window_end: datetime | None = None
    scores: dict[DimensionTag, int] = Field(default_factory=dict)
    confidence: dict[DimensionTag, float] = Field(default_factory=dict)
    evidence_count: dict[DimensionTag, int] = Field(default_factory=dict)
    details: list[CapabilityScore] = Field(default_factory=list)
    total_score: float = Field(default=0.0, ge=0.0, le=100.0)
    overall_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    coverage_ratio: float = Field(default=0.0, ge=0.0, le=1.0)
    input_card_ids: list[str] = Field(default_factory=list)
    benchmark_run_ids: list[str] = Field(default_factory=list)
    previous_snapshot_id: str | None = None
    deltas: dict[DimensionTag, int] = Field(default_factory=dict)

    @field_validator("competitor", "scoring_version")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("value must not be blank")
        return stripped

    @field_validator("input_card_ids", "benchmark_run_ids", mode="after")
    @classmethod
    def deduplicate_ids(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def complete_snapshot(self) -> "CapabilitySnapshot":
        if self.window_start and self.window_end and self.window_end < self.window_start:
            raise ValueError("window_end must be greater than or equal to window_start")
        if self.details:
            by_dimension = {item.dimension: item for item in self.details}
            if len(by_dimension) != len(self.details):
                raise ValueError("snapshot details must contain each dimension at most once")
            self.scores = {item.dimension: item.score for item in self.details}
            self.confidence = {item.dimension: item.confidence for item in self.details}
            self.evidence_count = {
                item.dimension: item.evidence_count for item in self.details
            }
            self.deltas = {
                item.dimension: item.delta
                for item in self.details
                if item.delta is not None
            }
            self.input_card_ids = list(
                dict.fromkeys(
                    [*self.input_card_ids, *(cid for item in self.details for cid in item.source_card_ids)]
                )
            )
            self.benchmark_run_ids = list(
                dict.fromkeys(
                    [
                        *self.benchmark_run_ids,
                        *(rid for item in self.details for rid in item.benchmark_run_ids),
                    ]
                )
            )

        scored = [
            item for item in self.details if item.status == CapabilityScoreStatus.SCORED
        ]
        if self.details:
            self.total_score = round(
                sum(
                    self.scores.get(dimension, 0) * weight
                    for dimension, weight in CAPABILITY_WEIGHTS.items()
                ),
                2,
            )
            self.coverage_ratio = round(
                sum(CAPABILITY_WEIGHTS[item.dimension] for item in scored),
                3,
            )
            self.overall_confidence = round(
                sum(
                    item.confidence * CAPABILITY_WEIGHTS[item.dimension]
                    for item in scored
                ),
                3,
            )
        if not self.snapshot_id:
            self.snapshot_id = _stable_id(
                "snap",
                self.competitor,
                self.snapshot_date,
                self.product_version,
                self.scoring_version,
                self.scores,
                self.evidence_count,
                self.input_card_ids,
                self.benchmark_run_ids,
            )
        return self


class CapabilitySnapshotSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshots: list[CapabilitySnapshot] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_snapshot_ids(self) -> "CapabilitySnapshotSet":
        ids = [item.snapshot_id for item in self.snapshots]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot IDs must be unique")
        return self


__all__ = [
    "BenchmarkContribution",
    "CAPABILITY_WEIGHTS",
    "CapabilityScore",
    "CapabilityScoreStatus",
    "CapabilitySnapshot",
    "CapabilitySnapshotSet",
    "EvidenceContribution",
]
