"""Add durable asynchronous Workflow queue state.

Revision ID: 0002_async_workflows
Revises: 0001_sqlite_persistence
Create Date: 2026-07-22
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_async_workflows"
down_revision = "0001_sqlite_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflows") as batch:
        batch.add_column(sa.Column("request_payload", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("progress", sa.Integer(), nullable=False, server_default="100")
        )
        batch.add_column(
            sa.Column(
                "cancel_requested",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )
        batch.add_column(
            sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="2")
        )
        batch.add_column(sa.Column("submitted_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("updated_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("available_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("claimed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("worker_id", sa.String(length=160)))
        batch.add_column(sa.Column("deadline_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("last_error", sa.JSON()))
        batch.create_index("ix_workflows_submitted_at", ["submitted_at"])
        batch.create_index("ix_workflows_available_at", ["available_at"])
        batch.create_index("ix_workflows_lease_expires_at", ["lease_expires_at"])
        batch.create_index("ix_workflows_worker_id", ["worker_id"])

    with op.batch_alter_table("workflow_branches") as batch:
        batch.add_column(
            sa.Column("progress", sa.Integer(), nullable=False, server_default="100")
        )
        batch.add_column(
            sa.Column("last_attempt", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("started_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("completed_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("trace_id", sa.String(length=96)))

    with op.batch_alter_table("agent_traces") as batch:
        batch.add_column(
            sa.Column("llm_call_count", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0")
        )

    op.create_table(
        "workflow_attempts",
        sa.Column("workflow_id", sa.String(length=96), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("worker_id", sa.String(length=160)),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Float()),
        sa.Column("termination_reason", sa.String(length=80)),
        sa.Column("error", sa.JSON()),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_id"], ["workflows.workflow_id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("workflow_id", "attempt"),
    )
    op.create_index("ix_workflow_attempts_status", "workflow_attempts", ["status"])
    op.create_index(
        "ix_workflow_attempts_worker_id", "workflow_attempts", ["worker_id"]
    )

    op.create_table(
        "workflow_branch_attempts",
        sa.Column("workflow_id", sa.String(length=96), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("branch", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("duration_ms", sa.Float()),
        sa.Column("rag_query_id", sa.String(length=96)),
        sa.Column("trace_id", sa.String(length=96)),
        sa.Column("error", sa.JSON()),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_id", "attempt"],
            ["workflow_attempts.workflow_id", "workflow_attempts.attempt"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("workflow_id", "attempt", "branch"),
    )
    op.create_index(
        "ix_workflow_branch_attempts_status",
        "workflow_branch_attempts",
        ["status"],
    )

    op.create_table(
        "workflow_worker_leases",
        sa.Column("lease_name", sa.String(length=80), nullable=False),
        sa.Column("worker_id", sa.String(length=160), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("lease_name"),
    )
    op.create_index(
        "ix_workflow_worker_leases_worker_id",
        "workflow_worker_leases",
        ["worker_id"],
    )
    op.create_index(
        "ix_workflow_worker_leases_expires_at",
        "workflow_worker_leases",
        ["expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_workflow_worker_leases_expires_at",
        table_name="workflow_worker_leases",
    )
    op.drop_index(
        "ix_workflow_worker_leases_worker_id",
        table_name="workflow_worker_leases",
    )
    op.drop_table("workflow_worker_leases")
    op.drop_index(
        "ix_workflow_branch_attempts_status",
        table_name="workflow_branch_attempts",
    )
    op.drop_table("workflow_branch_attempts")
    op.drop_index("ix_workflow_attempts_worker_id", table_name="workflow_attempts")
    op.drop_index("ix_workflow_attempts_status", table_name="workflow_attempts")
    op.drop_table("workflow_attempts")

    with op.batch_alter_table("agent_traces") as batch:
        batch.drop_column("total_tokens")
        batch.drop_column("output_tokens")
        batch.drop_column("input_tokens")
        batch.drop_column("llm_call_count")

    with op.batch_alter_table("workflow_branches") as batch:
        batch.drop_column("trace_id")
        batch.drop_column("completed_at")
        batch.drop_column("started_at")
        batch.drop_column("last_attempt")
        batch.drop_column("progress")

    with op.batch_alter_table("workflows") as batch:
        batch.drop_index("ix_workflows_worker_id")
        batch.drop_index("ix_workflows_lease_expires_at")
        batch.drop_index("ix_workflows_available_at")
        batch.drop_index("ix_workflows_submitted_at")
        batch.drop_column("last_error")
        batch.drop_column("deadline_at")
        batch.drop_column("worker_id")
        batch.drop_column("lease_expires_at")
        batch.drop_column("claimed_at")
        batch.drop_column("available_at")
        batch.drop_column("updated_at")
        batch.drop_column("submitted_at")
        batch.drop_column("max_attempts")
        batch.drop_column("attempt_count")
        batch.drop_column("cancel_requested")
        batch.drop_column("progress")
        batch.drop_column("request_payload")
