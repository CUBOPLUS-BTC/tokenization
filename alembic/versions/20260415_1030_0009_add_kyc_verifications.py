"""add kyc verifications

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-15 10:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "kyc_verifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("reviewed_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("document_url", sa.Text(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_kyc_verifications_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"],
            ["users.id"],
            name="fk_kyc_verifications_reviewed_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_kyc_verifications"),
        sa.UniqueConstraint("user_id", name="uq_kyc_verifications_user_id"),
        sa.CheckConstraint(
            "status IN ('pending', 'verified', 'rejected', 'expired')",
            name="ck_kyc_verifications_status_allowed",
        ),
    )
    op.create_index("ix_kyc_verifications_status", "kyc_verifications", ["status"])
    op.create_index("ix_kyc_verifications_user_id", "kyc_verifications", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_kyc_verifications_user_id", table_name="kyc_verifications")
    op.drop_index("ix_kyc_verifications_status", table_name="kyc_verifications")
    op.drop_table("kyc_verifications")
