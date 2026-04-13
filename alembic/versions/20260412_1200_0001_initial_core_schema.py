"""initial core schema

Revision ID: 0001
Revises:
Create Date: 2026-04-12 12:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="user"),
        sa.Column("totp_secret", sa.String(length=255), nullable=True),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_role", "users", ["role"], unique=False)

    op.create_table(
        "wallets",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("onchain_balance_sat", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lightning_balance_sat", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("encrypted_seed", sa.LargeBinary(), nullable=False),
        sa.Column("derivation_path", sa.String(length=50), nullable=False, server_default="m/86'/0'/0'"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_wallets_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_wallets"),
        sa.UniqueConstraint("user_id", name="uq_wallets_user_id"),
    )


def downgrade() -> None:
    op.drop_table("wallets")
    op.drop_index("ix_users_role", table_name="users")
    op.drop_table("users")
