"""Add formal API query, comparison provenance, and audit persistence.

Revision ID: 0003_api_closure
Revises: 0002_async_workflows
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_api_closure"
down_revision = "0002_async_workflows"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("capability_snapshots") as batch:
        batch.add_column(
            sa.Column(
                "source_kind",
                sa.String(length=40),
                nullable=False,
                server_default="agent_evidence",
            )
        )
        batch.add_column(
            sa.Column("provenance", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.create_index("ix_capability_snapshots_source_kind", ["source_kind"])
        batch.create_index(
            "ix_snapshots_scoring_date",
            ["scoring_version", "snapshot_date", "snapshot_id"],
        )

    with op.batch_alter_table("intelligence_cards") as batch:
        batch.create_index(
            "ix_cards_competitor_created",
            ["competitor", "created_at", "card_id"],
        )
        batch.create_index(
            "ix_cards_priority_created",
            ["priority_score", "created_at", "card_id"],
        )

    with op.batch_alter_table("card_evidence") as batch:
        batch.create_index("ix_card_evidence_chunk_id", ["chunk_id"])

    with op.batch_alter_table("briefings") as batch:
        batch.create_index(
            "ix_briefings_competitor_created",
            ["competitor", "created_at", "briefing_id"],
        )

    with op.batch_alter_table("workflows") as batch:
        batch.create_index(
            "ix_workflows_status_submitted",
            ["status", "submitted_at", "workflow_id"],
        )

    op.create_table(
        "comparison_matrices",
        sa.Column("comparison_id", sa.String(length=96), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("baseline_product", sa.String(length=160), nullable=False),
        sa.Column("scoring_version", sa.String(length=80), nullable=False),
        sa.Column("comparison_date", sa.Date(), nullable=False),
        sa.Column(
            "official_ranking_ready",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("comparison_id"),
        sa.UniqueConstraint("request_fingerprint"),
    )
    for name, columns in (
        ("ix_comparison_matrices_request_fingerprint", ["request_fingerprint"]),
        ("ix_comparison_matrices_baseline_product", ["baseline_product"]),
        ("ix_comparison_matrices_scoring_version", ["scoring_version"]),
        ("ix_comparison_matrices_comparison_date", ["comparison_date"]),
        ("ix_comparison_matrices_official_ranking_ready", ["official_ranking_ready"]),
        ("ix_comparison_matrices_created_at", ["created_at"]),
    ):
        op.create_index(name, "comparison_matrices", columns)

    op.create_table(
        "comparison_snapshots",
        sa.Column("comparison_id", sa.String(length=96), nullable=False),
        sa.Column("snapshot_id", sa.String(length=96), nullable=False),
        sa.ForeignKeyConstraint(
            ["comparison_id"],
            ["comparison_matrices.comparison_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["capability_snapshots.snapshot_id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("comparison_id", "snapshot_id"),
    )

    op.create_table(
        "api_audit_events",
        sa.Column("event_id", sa.String(length=96), nullable=False),
        sa.Column("request_id", sa.String(length=80), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("method", sa.String(length=12), nullable=False),
        sa.Column("path", sa.String(length=320), nullable=False),
        sa.Column("resource_type", sa.String(length=80)),
        sa.Column("resource_id", sa.String(length=160)),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Float(), nullable=False),
        sa.Column("api_key_id", sa.String(length=40), nullable=False),
        sa.Column("client_ip_hash", sa.String(length=64)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    for name, columns in (
        ("ix_api_audit_events_request_id", ["request_id"]),
        ("ix_api_audit_events_action", ["action"]),
        ("ix_api_audit_events_resource_type", ["resource_type"]),
        ("ix_api_audit_events_resource_id", ["resource_id"]),
        ("ix_api_audit_events_status_code", ["status_code"]),
        ("ix_api_audit_events_created_at", ["created_at"]),
    ):
        op.create_index(name, "api_audit_events", columns)


def downgrade() -> None:
    op.drop_table("api_audit_events")
    op.drop_table("comparison_snapshots")
    op.drop_table("comparison_matrices")

    with op.batch_alter_table("workflows") as batch:
        batch.drop_index("ix_workflows_status_submitted")
    with op.batch_alter_table("briefings") as batch:
        batch.drop_index("ix_briefings_competitor_created")
    with op.batch_alter_table("card_evidence") as batch:
        batch.drop_index("ix_card_evidence_chunk_id")
    with op.batch_alter_table("intelligence_cards") as batch:
        batch.drop_index("ix_cards_priority_created")
        batch.drop_index("ix_cards_competitor_created")
    with op.batch_alter_table("capability_snapshots") as batch:
        batch.drop_index("ix_snapshots_scoring_date")
        batch.drop_index("ix_capability_snapshots_source_kind")
        batch.drop_column("provenance")
        batch.drop_column("source_kind")
