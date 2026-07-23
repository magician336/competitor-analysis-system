"""Strict contracts for the week-three LangChain Multi-Agent workflow."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .benchmark import BenchmarkRun, BenchmarkTask
from .capability_snapshot import CapabilitySnapshot
from .document import EventType
from .intelligence_card import (
    AgentAnalysisRequest,
    AgentRunResult,
    IntelligenceCard,
)


class SpecialistBranch(str, Enum):
    PRICE = "price"
    PRODUCT = "product"
    RISK = "risk"


DEFAULT_SPECIALIST_BRANCHES = [
    SpecialistBranch.PRICE,
    SpecialistBranch.PRODUCT,
    SpecialistBranch.RISK,
]


_EVENT_BRANCH = {
    EventType.PRICING_CHANGE: SpecialistBranch.PRICE,
    EventType.PRODUCT_RELEASE: SpecialistBranch.PRODUCT,
    EventType.RISK_EXPERIENCE: SpecialistBranch.RISK,
}


def request_fingerprint(request: "MultiAgentAnalysisRequest") -> str:
    payload = request.model_dump(mode="json", exclude_none=False)
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def workflow_id_for(request: "MultiAgentAnalysisRequest") -> str:
    fingerprint = request_fingerprint(request)
    correlation = request.correlation_id or "no-correlation"
    payload = f"{correlation}\0{fingerprint}".encode("utf-8")
    return "workflow_" + hashlib.sha256(payload).hexdigest()[:24]


class MultiAgentAnalysisRequest(AgentAnalysisRequest):
    """One typed request fan-outs to a selected set of specialist Agents."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    branches: list[SpecialistBranch] = Field(
        default_factory=lambda: list(DEFAULT_SPECIALIST_BRANCHES),
        min_length=1,
        max_length=3,
    )
    analysis_mode: Literal["rules", "hybrid", "llm"] = "rules"
    include_snapshot: bool = True
    include_briefing: bool = True
    include_benchmark_data: bool = False
    snapshot_product_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    benchmark_tasks: list[BenchmarkTask] = Field(default_factory=list)
    benchmark_runs: list[BenchmarkRun] = Field(default_factory=list)
    use_cache: bool = True

    @field_validator("branches", mode="after")
    @classmethod
    def validate_unique_branches(
        cls,
        values: list[SpecialistBranch],
    ) -> list[SpecialistBranch]:
        if len(values) != len(set(values)):
            raise ValueError("branches must not contain duplicates")
        return values

    @field_validator("correlation_id", mode="after")
    @classmethod
    def normalize_correlation_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("correlation_id must not be blank")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", normalized):
            raise ValueError("correlation_id must be a portable identifier")
        return normalized

    @model_validator(mode="after")
    def validate_orchestration_scope(self) -> "MultiAgentAnalysisRequest":
        if self.event_type is not None:
            expected = _EVENT_BRANCH[self.event_type]
            if self.branches != [expected]:
                raise ValueError(
                    "event_type can only be used with its single matching specialist branch"
                )

        task_ids = [task.task_id for task in self.benchmark_tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("benchmark_tasks must have unique task_id values")
        run_ids = [run.run_id for run in self.benchmark_runs]
        if len(run_ids) != len(set(run_ids)):
            raise ValueError("benchmark_runs must have unique run_id values")
        mismatched = sorted(
            {
                run.competitor
                for run in self.benchmark_runs
                if run.competitor.casefold() != self.competitor.casefold()
            }
        )
        if mismatched:
            raise ValueError(
                "benchmark_runs must belong to request competitor: "
                + ", ".join(mismatched)
            )
        if task_ids:
            unknown = sorted(
                {run.task_id for run in self.benchmark_runs} - set(task_ids)
            )
            if unknown:
                raise ValueError(
                    "benchmark_runs reference task IDs absent from benchmark_tasks: "
                    + ", ".join(unknown)
                )
        return self

    @property
    def fingerprint(self) -> str:
        return request_fingerprint(self)

    @property
    def stable_workflow_id(self) -> str:
        return workflow_id_for(self)


class BranchExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class WorkflowExecutionStatus(str, Enum):
    SUCCESS = "success"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"


class BranchError(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    error_type: str = Field(min_length=1, max_length=160)
    message: str = Field(min_length=1, max_length=1_000)
    stage: str = Field(default="analysis", min_length=1, max_length=80)
    retryable: bool = False


class BranchOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    branch: SpecialistBranch
    status: BranchExecutionStatus
    duration_ms: float = Field(ge=0.0)
    result: AgentRunResult | None = None
    error: BranchError | None = None
    card_count: int = Field(default=0, ge=0)
    degraded: bool = False

    @model_validator(mode="after")
    def align_outcome(self) -> "BranchOutcome":
        if self.status == BranchExecutionStatus.SUCCESS:
            if self.result is None or self.error is not None:
                raise ValueError("successful branch requires result and forbids error")
            self.card_count = len(self.result.cards)
            self.degraded = self.result.degraded
        else:
            if self.error is None or self.result is not None:
                raise ValueError("failed branch requires error and forbids result")
            self.card_count = 0
            self.degraded = True
        return self


class MultiAgentAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    workflow_id: str = Field(pattern=r"^workflow_[0-9a-f]{24}$")
    request_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    request: MultiAgentAnalysisRequest
    status: WorkflowExecutionStatus
    partial_failure: bool = False
    cache_hit: bool = False
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float = Field(ge=0.0)
    branch_outcomes: dict[SpecialistBranch, BranchOutcome] = Field(
        default_factory=dict
    )
    cards: list[IntelligenceCard] = Field(default_factory=list)
    snapshot: CapabilitySnapshot | None = None
    briefing: str | None = None
    warnings: list[str] = Field(default_factory=list)

    @field_validator("briefing", mode="after")
    @classmethod
    def reject_blank_briefing(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("briefing must be null or non-blank")
        return normalized

    @field_validator("warnings", mode="after")
    @classmethod
    def deduplicate_warnings(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @model_validator(mode="after")
    def align_workflow_result(self) -> "MultiAgentAnalysisResult":
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must be after started_at")
        for branch, outcome in self.branch_outcomes.items():
            if branch != outcome.branch:
                raise ValueError("branch_outcomes key must match outcome.branch")
        unique_cards: dict[str, IntelligenceCard] = {}
        for card in self.cards:
            unique_cards.setdefault(card.card_id, card)
        self.cards = list(unique_cards.values())
        self.partial_failure = self.status == WorkflowExecutionStatus.PARTIAL_FAILURE
        return self


__all__ = [
    "BranchError",
    "BranchExecutionStatus",
    "BranchOutcome",
    "DEFAULT_SPECIALIST_BRANCHES",
    "MultiAgentAnalysisRequest",
    "MultiAgentAnalysisResult",
    "SpecialistBranch",
    "WorkflowExecutionStatus",
    "request_fingerprint",
    "workflow_id_for",
]
