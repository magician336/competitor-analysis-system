"""Persist ownership markers for API-key Mini-RAG query traces.

Revision ID: 0005_machine_query_ownership
Revises: 0004_user_history
Create Date: 2026-07-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_machine_query_ownership"
down_revision = "0004_user_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "machine_query_traces",
        sa.Column("query_id", sa.String(length=96), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("query_id"),
    )
    op.create_index(
        "ix_machine_query_traces_created_at",
        "machine_query_traces",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_machine_query_traces_created_at",
        table_name="machine_query_traces",
    )
    op.drop_table("machine_query_traces")
