"""Create the fourth-week non-Benchmark persistence schema.

Revision ID: 0001_sqlite_persistence
Revises:
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0001_sqlite_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "competitors",
        sa.Column("competitor_id", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("aliases", sa.JSON(), nullable=False),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("config_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("competitor_id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "intelligence_cards",
        sa.Column("card_id", sa.String(length=96), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("competitor", sa.String(length=160), nullable=False),
        sa.Column("agent_kind", sa.String(length=32), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("alert_level", sa.String(length=24), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=False),
        sa.Column("priority_score", sa.Integer(), nullable=False),
        sa.Column("review_required", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("card_id"),
    )
    for column in (
        "competitor",
        "agent_kind",
        "event_type",
        "alert_level",
        "review_required",
        "created_at",
    ):
        op.create_index(f"ix_intelligence_cards_{column}", "intelligence_cards", [column])

    op.create_table(
        "card_evidence",
        sa.Column("card_id", sa.String(length=96), nullable=False),
        sa.Column("chunk_id", sa.String(length=128), nullable=False),
        sa.Column("citation_id", sa.String(length=128), nullable=True),
        sa.Column("document_id", sa.String(length=128), nullable=False),
        sa.Column("version_id", sa.String(length=128), nullable=False),
        sa.Column("competitor", sa.String(length=160), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("source_type", sa.String(length=64), nullable=False),
        sa.Column("evidence_level", sa.String(length=8), nullable=False),
        sa.Column("publish_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"], ["intelligence_cards.card_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("card_id", "chunk_id"),
    )
    for column in (
        "document_id",
        "version_id",
        "competitor",
        "source_type",
        "evidence_level",
    ):
        op.create_index(f"ix_card_evidence_{column}", "card_evidence", [column])

    op.create_table(
        "capability_snapshots",
        sa.Column("snapshot_id", sa.String(length=96), nullable=False),
        sa.Column("competitor", sa.String(length=160), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("product_version", sa.String(length=128), nullable=False),
        sa.Column("scoring_version", sa.String(length=80), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_score", sa.Float(), nullable=False),
        sa.Column("overall_confidence", sa.Float(), nullable=False),
        sa.Column("coverage_ratio", sa.Float(), nullable=False),
        sa.Column("previous_snapshot_id", sa.String(length=96), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["previous_snapshot_id"],
            ["capability_snapshots.snapshot_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("snapshot_id"),
    )
    op.create_index("ix_capability_snapshots_competitor", "capability_snapshots", ["competitor"])
    op.create_index("ix_capability_snapshots_snapshot_date", "capability_snapshots", ["snapshot_date"])
    op.create_index("ix_capability_snapshots_scoring_version", "capability_snapshots", ["scoring_version"])
    op.create_index(
        "ix_snapshots_competitor_date",
        "capability_snapshots",
        ["competitor", "snapshot_date"],
    )
    op.create_table(
        "snapshot_cards",
        sa.Column("snapshot_id", sa.String(length=96), nullable=False),
        sa.Column("card_id", sa.String(length=96), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["capability_snapshots.snapshot_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["card_id"], ["intelligence_cards.card_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("snapshot_id", "card_id"),
    )

    op.create_table(
        "workflows",
        sa.Column("workflow_id", sa.String(length=96), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("competitor", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("partial_failure", sa.Boolean(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("workflow_id"),
        sa.UniqueConstraint("request_fingerprint"),
    )
    op.create_index("ix_workflows_request_fingerprint", "workflows", ["request_fingerprint"])
    op.create_index("ix_workflows_competitor", "workflows", ["competitor"])
    op.create_index("ix_workflows_status", "workflows", ["status"])
    op.create_table(
        "workflow_branches",
        sa.Column("workflow_id", sa.String(length=96), nullable=False),
        sa.Column("branch", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=True),
        sa.Column("rag_query_id", sa.String(length=96), nullable=True),
        sa.Column("error", sa.JSON(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.workflow_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("workflow_id", "branch"),
    )
    op.create_index("ix_workflow_branches_status", "workflow_branches", ["status"])
    op.create_index("ix_workflow_branches_rag_query_id", "workflow_branches", ["rag_query_id"])
    op.create_table(
        "agent_traces",
        sa.Column("trace_id", sa.String(length=96), nullable=False),
        sa.Column("workflow_id", sa.String(length=96), nullable=True),
        sa.Column("branch", sa.String(length=32), nullable=True),
        sa.Column("agent_kind", sa.String(length=32), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("llm_used", sa.Boolean(), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("model_name", sa.String(length=160), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.workflow_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("trace_id"),
    )
    op.create_index("ix_agent_traces_workflow_id", "agent_traces", ["workflow_id"])
    op.create_index("ix_agent_traces_branch", "agent_traces", ["branch"])
    op.create_index("ix_agent_traces_agent_kind", "agent_traces", ["agent_kind"])

    op.create_table(
        "briefings",
        sa.Column("briefing_id", sa.String(length=96), nullable=False),
        sa.Column("competitor", sa.String(length=160), nullable=False),
        sa.Column("snapshot_id", sa.String(length=96), nullable=True),
        sa.Column("workflow_id", sa.String(length=96), nullable=True),
        sa.Column("markdown", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["snapshot_id"], ["capability_snapshots.snapshot_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.workflow_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("briefing_id"),
    )
    op.create_index("ix_briefings_competitor", "briefings", ["competitor"])
    op.create_index("ix_briefings_snapshot_id", "briefings", ["snapshot_id"])
    op.create_index("ix_briefings_workflow_id", "briefings", ["workflow_id"])
    op.create_index("ix_briefings_created_at", "briefings", ["created_at"])

    op.create_table(
        "artifact_imports",
        sa.Column("import_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source_root", sa.Text(), nullable=False),
        sa.Column("source_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_version", sa.String(length=80), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("counts", sa.JSON(), nullable=False),
        sa.Column("manifest", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("import_id"),
        sa.UniqueConstraint("source_sha256"),
    )


def downgrade() -> None:
    op.drop_table("artifact_imports")
    op.drop_index("ix_briefings_created_at", table_name="briefings")
    op.drop_index("ix_briefings_workflow_id", table_name="briefings")
    op.drop_index("ix_briefings_snapshot_id", table_name="briefings")
    op.drop_index("ix_briefings_competitor", table_name="briefings")
    op.drop_table("briefings")
    op.drop_index("ix_agent_traces_agent_kind", table_name="agent_traces")
    op.drop_index("ix_agent_traces_branch", table_name="agent_traces")
    op.drop_index("ix_agent_traces_workflow_id", table_name="agent_traces")
    op.drop_table("agent_traces")
    op.drop_index("ix_workflow_branches_rag_query_id", table_name="workflow_branches")
    op.drop_index("ix_workflow_branches_status", table_name="workflow_branches")
    op.drop_table("workflow_branches")
    op.drop_index("ix_workflows_status", table_name="workflows")
    op.drop_index("ix_workflows_competitor", table_name="workflows")
    op.drop_index("ix_workflows_request_fingerprint", table_name="workflows")
    op.drop_table("workflows")
    op.drop_table("snapshot_cards")
    op.drop_index("ix_snapshots_competitor_date", table_name="capability_snapshots")
    op.drop_index("ix_capability_snapshots_scoring_version", table_name="capability_snapshots")
    op.drop_index("ix_capability_snapshots_snapshot_date", table_name="capability_snapshots")
    op.drop_index("ix_capability_snapshots_competitor", table_name="capability_snapshots")
    op.drop_table("capability_snapshots")
    for column in (
        "evidence_level",
        "source_type",
        "competitor",
        "version_id",
        "document_id",
    ):
        op.drop_index(f"ix_card_evidence_{column}", table_name="card_evidence")
    op.drop_table("card_evidence")
    for column in (
        "created_at",
        "review_required",
        "alert_level",
        "event_type",
        "agent_kind",
        "competitor",
    ):
        op.drop_index(f"ix_intelligence_cards_{column}", table_name="intelligence_cards")
    op.drop_table("intelligence_cards")
    op.drop_table("competitors")
