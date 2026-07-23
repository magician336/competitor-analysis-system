"""Public contracts for durable asynchronous Multi-Agent workflows."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .intelligence_card import AgentAnalysisRequest
from .orchestration import (
    DEFAULT_SPECIALIST_BRANCHES,
    MultiAgentAnalysisRequest,
    MultiAgentAnalysisResult,
    SpecialistBranch,
)


class WorkflowStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    SUCCESS = "success"
    PARTIAL_FAILURE = "partial_failure"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class WorkflowBranchStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


TERMINAL_WORKFLOW_STATUSES = {
    WorkflowStatus.CANCELLED,
    WorkflowStatus.SUCCESS,
    WorkflowStatus.PARTIAL_FAILURE,
    WorkflowStatus.FAILED,
    WorkflowStatus.TIMED_OUT,
}


class WorkflowSubmitRequest(AgentAnalysisRequest):
    """Benchmark-free public request for one durable Multi-Agent workflow."""

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
    snapshot_product_version: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_orchestration_scope(self) -> "WorkflowSubmitRequest":
        self.to_analysis_request()
        return self

    def to_analysis_request(self) -> MultiAgentAnalysisRequest:
        return MultiAgentAnalysisRequest.model_validate(
            {
                **self.model_dump(mode="python"),
                "include_benchmark_data": False,
                "benchmark_tasks": [],
                "benchmark_runs": [],
                "use_cache": False,
            }
        )


class WorkflowSubmissionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    status: WorkflowStatus
    deduplicated: bool
    status_url: str
    submitted_at: datetime | None = None


class WorkflowActionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    status: WorkflowStatus
    accepted: bool = True
    attempt_count: int = Field(ge=0)
    message: str


class WorkflowBranchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branch: SpecialistBranch
    status: WorkflowBranchStatus
    progress: int = Field(ge=0, le=100)
    last_attempt: int = Field(ge=0)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: float | None = Field(default=None, ge=0.0)
    rag_query_id: str | None = None
    card_ids: list[str] = Field(default_factory=list)
    trace_id: str | None = None
    error: dict[str, object] | None = None


class WorkflowStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    request_fingerprint: str | None = None
    competitor: str
    analysis_mode: str = "rules"
    status: WorkflowStatus
    progress: int = Field(ge=0, le=100)
    attempt_count: int = Field(ge=0)
    max_attempts: int = Field(ge=1)
    cancel_requested: bool
    submitted_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    deadline_at: datetime | None = None
    updated_at: datetime | None = None
    branches: list[WorkflowBranchResponse] = Field(default_factory=list)
    card_ids: list[str] = Field(default_factory=list)
    snapshot_id: str | None = None
    briefing_id: str | None = None
    last_error: dict[str, object] | None = None
    result: MultiAgentAnalysisResult | None = None


__all__ = [
    "TERMINAL_WORKFLOW_STATUSES",
    "WorkflowActionResponse",
    "WorkflowBranchResponse",
    "WorkflowBranchStatus",
    "WorkflowStatus",
    "WorkflowStatusResponse",
    "WorkflowSubmissionResponse",
    "WorkflowSubmitRequest",
]
