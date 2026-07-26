"""Add application users, sessions, and user-owned query history.

Revision ID: 0004_user_history
Revises: 0003_api_closure
Create Date: 2026-07-26
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_user_history"
down_revision = "0003_api_closure"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("user_id", sa.String(length=96), nullable=False),
        sa.Column("username", sa.String(length=32), nullable=False),
        sa.Column("username_normalized", sa.String(length=32), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
        sa.UniqueConstraint("username_normalized"),
    )
    op.create_index("ix_users_username_normalized", "users", ["username_normalized"])
    op.create_index("ix_users_created_at", "users", ["created_at"])

    op.create_table(
        "user_sessions",
        sa.Column("session_id", sa.String(length=96), nullable=False),
        sa.Column("user_id", sa.String(length=96), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("session_id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"])
    op.create_index("ix_user_sessions_expires_at", "user_sessions", ["expires_at"])

    op.create_table(
        "ask_histories",
        sa.Column("ask_id", sa.String(length=96), nullable=False),
        sa.Column("user_id", sa.String(length=96), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("analysis_target", sa.String(length=160)),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("ask_id"),
    )
    op.create_index("ix_ask_histories_user_id", "ask_histories", ["user_id"])
    op.create_index(
        "ix_ask_histories_analysis_target", "ask_histories", ["analysis_target"]
    )
    op.create_index("ix_ask_histories_created_at", "ask_histories", ["created_at"])
    op.create_index(
        "ix_ask_histories_user_created",
        "ask_histories",
        ["user_id", "created_at", "ask_id"],
    )

    op.create_table(
        "evidence_histories",
        sa.Column("query_id", sa.String(length=96), nullable=False),
        sa.Column("user_id", sa.String(length=96), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("competitor", sa.String(length=160)),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("response_payload", sa.JSON(), nullable=False),
        sa.Column("result_count", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.user_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("query_id"),
    )
    op.create_index(
        "ix_evidence_histories_user_id", "evidence_histories", ["user_id"]
    )
    op.create_index(
        "ix_evidence_histories_competitor", "evidence_histories", ["competitor"]
    )
    op.create_index(
        "ix_evidence_histories_created_at", "evidence_histories", ["created_at"]
    )
    op.create_index(
        "ix_evidence_histories_user_created",
        "evidence_histories",
        ["user_id", "created_at", "query_id"],
    )

    with op.batch_alter_table("workflows") as batch:
        batch.add_column(sa.Column("user_id", sa.String(length=96)))
        batch.create_foreign_key(
            "fk_workflows_user_id_users",
            "users",
            ["user_id"],
            ["user_id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_workflows_user_id", ["user_id"])


def downgrade() -> None:
    with op.batch_alter_table("workflows") as batch:
        batch.drop_index("ix_workflows_user_id")
        batch.drop_constraint("fk_workflows_user_id_users", type_="foreignkey")
        batch.drop_column("user_id")

    op.drop_index(
        "ix_evidence_histories_user_created", table_name="evidence_histories"
    )
    op.drop_index("ix_evidence_histories_created_at", table_name="evidence_histories")
    op.drop_index("ix_evidence_histories_competitor", table_name="evidence_histories")
    op.drop_index("ix_evidence_histories_user_id", table_name="evidence_histories")
    op.drop_table("evidence_histories")

    op.drop_index("ix_ask_histories_user_created", table_name="ask_histories")
    op.drop_index("ix_ask_histories_created_at", table_name="ask_histories")
    op.drop_index("ix_ask_histories_analysis_target", table_name="ask_histories")
    op.drop_index("ix_ask_histories_user_id", table_name="ask_histories")
    op.drop_table("ask_histories")

    op.drop_index("ix_user_sessions_expires_at", table_name="user_sessions")
    op.drop_index("ix_user_sessions_token_hash", table_name="user_sessions")
    op.drop_index("ix_user_sessions_user_id", table_name="user_sessions")
    op.drop_table("user_sessions")

    op.drop_index("ix_users_created_at", table_name="users")
    op.drop_index("ix_users_username_normalized", table_name="users")
    op.drop_table("users")
