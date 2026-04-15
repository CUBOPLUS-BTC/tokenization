"""add disputes table

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-14 14:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "disputes",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trade_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("opened_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "status",
            sa.String(length=10),
            nullable=False,
            server_default="open",
        ),
        sa.Column("resolution", sa.String(length=10), nullable=True),
        sa.Column("resolved_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
            ["trade_id"],
            ["trades.id"],
            name="fk_disputes_trade_id_trades",
        ),
        sa.ForeignKeyConstraint(
            ["opened_by"],
            ["users.id"],
            name="fk_disputes_opened_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"],
            ["users.id"],
            name="fk_disputes_resolved_by_users",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_disputes"),
        sa.UniqueConstraint("trade_id", name="uq_disputes_trade_id"),
        sa.CheckConstraint("status IN ('open', 'resolved')", name="ck_disputes_status_allowed"),
        sa.CheckConstraint(
            "resolution IS NULL OR resolution IN ('refund', 'release')",
            name="ck_disputes_resolution_allowed",
        ),
    )
    op.create_index("ix_disputes_status", "disputes", ["status"])


def downgrade() -> None:
    op.drop_index("ix_disputes_status", table_name="disputes")
    op.drop_table("disputes")
