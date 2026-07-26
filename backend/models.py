"""SQLAlchemy models for CodeRadar's non-Benchmark persistence boundary."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class UserRecord(Base):
    __tablename__ = "users"

    user_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    username: Mapped[str] = mapped_column(String(32), nullable=False)
    username_normalized: Mapped[str] = mapped_column(
        String(32), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class UserSessionRecord(Base):
    __tablename__ = "user_sessions"

    session_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )


class AskHistoryRecord(Base):
    __tablename__ = "ask_histories"

    ask_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_target: Mapped[str | None] = mapped_column(String(160), index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )

    __table_args__ = (
        Index("ix_ask_histories_user_created", "user_id", "created_at", "ask_id"),
    )


class EvidenceHistoryRecord(Base):
    __tablename__ = "evidence_histories"

    query_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    question: Mapped[str] = mapped_column(Text, nullable=False)
    competitor: Mapped[str | None] = mapped_column(String(160), index=True)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    response_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    result_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )

    __table_args__ = (
        Index(
            "ix_evidence_histories_user_created",
            "user_id",
            "created_at",
            "query_id",
        ),
    )


class MachineQueryTraceRecord(Base):
    __tablename__ = "machine_query_traces"

    query_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class CompetitorRecord(Base):
    __tablename__ = "competitors"

    competitor_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)
    aliases: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    sources: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class IntelligenceCardRecord(Base):
    __tablename__ = "intelligence_cards"

    card_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    competitor: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    agent_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    alert_level: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    priority_score: Mapped[int] = mapped_column(Integer, nullable=False)
    review_required: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index("ix_cards_competitor_created", "competitor", "created_at", "card_id"),
        Index("ix_cards_priority_created", "priority_score", "created_at", "card_id"),
    )


class CardEvidenceRecord(Base):
    __tablename__ = "card_evidence"

    card_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("intelligence_cards.card_id", ondelete="CASCADE"),
        primary_key=True,
    )
    chunk_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    citation_id: Mapped[str | None] = mapped_column(String(128))
    document_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    version_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    competitor: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    source_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    evidence_level: Mapped[str] = mapped_column(String(8), nullable=False, index=True)
    publish_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (Index("ix_card_evidence_chunk_id", "chunk_id"),)


class CapabilitySnapshotRecord(Base):
    __tablename__ = "capability_snapshots"

    snapshot_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    competitor: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    product_version: Mapped[str] = mapped_column(String(128), nullable=False)
    scoring_version: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_score: Mapped[float] = mapped_column(Float, nullable=False)
    overall_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    coverage_ratio: Mapped[float] = mapped_column(Float, nullable=False)
    previous_snapshot_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("capability_snapshots.snapshot_id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    source_kind: Mapped[str] = mapped_column(
        String(40), nullable=False, default="agent_evidence", index=True
    )
    provenance: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, default=dict
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        Index(
            "ix_snapshots_competitor_date",
            "competitor",
            "snapshot_date",
        ),
        Index(
            "ix_snapshots_scoring_date",
            "scoring_version",
            "snapshot_date",
            "snapshot_id",
        ),
    )


class SnapshotCardRecord(Base):
    __tablename__ = "snapshot_cards"

    snapshot_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("capability_snapshots.snapshot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    card_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("intelligence_cards.card_id", ondelete="CASCADE"),
        primary_key=True,
    )


class WorkflowRecord(Base):
    __tablename__ = "workflows"

    workflow_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        index=True,
    )
    request_fingerprint: Mapped[str | None] = mapped_column(
        String(64)
    )
    competitor: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    partial_failure: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    request_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    worker_id: Mapped[str | None] = mapped_column(String(160), index=True)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[dict[str, Any] | None] = mapped_column(JSON)

    __table_args__ = (
        UniqueConstraint("request_fingerprint"),
        Index("ix_workflows_request_fingerprint", "request_fingerprint"),
        Index(
            "ix_workflows_status_submitted",
            "status",
            "submitted_at",
            "workflow_id",
        ),
    )


class WorkflowBranchRecord(Base):
    __tablename__ = "workflow_branches"

    workflow_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("workflows.workflow_id", ondelete="CASCADE"),
        primary_key=True,
    )
    branch: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    rag_query_id: Mapped[str | None] = mapped_column(String(96), index=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    last_attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_id: Mapped[str | None] = mapped_column(String(96))


class WorkflowAttemptRecord(Base):
    __tablename__ = "workflow_attempts"

    workflow_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("workflows.workflow_id", ondelete="CASCADE"),
        primary_key=True,
    )
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(160), index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    termination_reason: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class WorkflowBranchAttemptRecord(Base):
    __tablename__ = "workflow_branch_attempts"

    workflow_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    attempt: Mapped[int] = mapped_column(Integer, primary_key=True)
    branch: Mapped[str] = mapped_column(String(32), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    rag_query_id: Mapped[str | None] = mapped_column(String(96))
    trace_id: Mapped[str | None] = mapped_column(String(96))
    error: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        ForeignKeyConstraint(
            ["workflow_id", "attempt"],
            ["workflow_attempts.workflow_id", "workflow_attempts.attempt"],
            ondelete="CASCADE",
        ),
    )


class WorkflowWorkerLeaseRecord(Base):
    __tablename__ = "workflow_worker_leases"

    lease_name: Mapped[str] = mapped_column(String(80), primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    heartbeat_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )


class AgentTraceRecord(Base):
    __tablename__ = "agent_traces"

    trace_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    workflow_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("workflows.workflow_id", ondelete="CASCADE"),
        index=True,
    )
    branch: Mapped[str | None] = mapped_column(String(32), index=True)
    agent_kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    llm_used: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fallback_used: Mapped[bool] = mapped_column(Boolean, nullable=False)
    model_name: Mapped[str | None] = mapped_column(String(160))
    llm_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


class BriefingRecord(Base):
    __tablename__ = "briefings"

    briefing_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    competitor: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    snapshot_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("capability_snapshots.snapshot_id", ondelete="SET NULL"),
        index=True,
    )
    workflow_id: Mapped[str | None] = mapped_column(
        String(96),
        ForeignKey("workflows.workflow_id", ondelete="SET NULL"),
        index=True,
    )
    markdown: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)

    __table_args__ = (
        Index(
            "ix_briefings_competitor_created",
            "competitor",
            "created_at",
            "briefing_id",
        ),
    )


class ComparisonMatrixRecord(Base):
    __tablename__ = "comparison_matrices"

    comparison_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    request_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False
    )
    baseline_product: Mapped[str] = mapped_column(String(160), nullable=False, index=True)
    scoring_version: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    comparison_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    official_ranking_ready: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )

    __table_args__ = (
        UniqueConstraint("request_fingerprint"),
        Index("ix_comparison_matrices_request_fingerprint", "request_fingerprint"),
    )


class ComparisonSnapshotRecord(Base):
    __tablename__ = "comparison_snapshots"

    comparison_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("comparison_matrices.comparison_id", ondelete="CASCADE"),
        primary_key=True,
    )
    snapshot_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("capability_snapshots.snapshot_id", ondelete="RESTRICT"),
        primary_key=True,
    )


class ApiAuditEventRecord(Base):
    __tablename__ = "api_audit_events"

    event_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    request_id: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    method: Mapped[str] = mapped_column(String(12), nullable=False)
    path: Mapped[str] = mapped_column(String(320), nullable=False)
    resource_type: Mapped[str | None] = mapped_column(String(80), index=True)
    resource_id: Mapped[str | None] = mapped_column(String(160), index=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=False)
    api_key_id: Mapped[str] = mapped_column(String(40), nullable=False, default="primary")
    client_ip_hash: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, index=True
    )


class ArtifactImportRecord(Base):
    __tablename__ = "artifact_imports"

    import_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_root: Mapped[str] = mapped_column(Text, nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    artifact_version: Mapped[str] = mapped_column(String(80), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    counts: Mapped[dict[str, int]] = mapped_column(JSON, nullable=False)
    manifest: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)


__all__ = [
    "AgentTraceRecord",
    "AskHistoryRecord",
    "ArtifactImportRecord",
    "Base",
    "BriefingRecord",
    "CapabilitySnapshotRecord",
    "CardEvidenceRecord",
    "ComparisonMatrixRecord",
    "ComparisonSnapshotRecord",
    "CompetitorRecord",
    "EvidenceHistoryRecord",
    "IntelligenceCardRecord",
    "MachineQueryTraceRecord",
    "ApiAuditEventRecord",
    "SnapshotCardRecord",
    "UserRecord",
    "UserSessionRecord",
    "WorkflowBranchRecord",
    "WorkflowAttemptRecord",
    "WorkflowBranchAttemptRecord",
    "WorkflowWorkerLeaseRecord",
    "WorkflowRecord",
]
