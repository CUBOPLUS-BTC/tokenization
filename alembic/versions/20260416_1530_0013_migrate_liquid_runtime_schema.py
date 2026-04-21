"""migrate liquid runtime schema

Revision ID: 0013
Revises: 0012
Create Date: 2026-04-16 15:30:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: Union[str, None] = "0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "tokens",
        "taproot_asset_id",
        new_column_name="liquid_asset_id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.drop_constraint("uq_tokens_taproot_asset_id", "tokens", type_="unique")
    op.create_unique_constraint("uq_tokens_liquid_asset_id", "tokens", ["liquid_asset_id"])

    op.add_column(
        "escrows",
        sa.Column("settlement_metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.alter_column(
        "wallets",
        "derivation_path",
        existing_type=sa.String(length=50),
        server_default="m/44'/1776'/0'",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "wallets",
        "derivation_path",
        existing_type=sa.String(length=50),
        server_default="m/86'/0'/0'",
        existing_nullable=False,
    )

    op.drop_column("escrows", "settlement_metadata")

    op.drop_constraint("uq_tokens_liquid_asset_id", "tokens", type_="unique")
    op.alter_column(
        "tokens",
        "liquid_asset_id",
        new_column_name="taproot_asset_id",
        existing_type=sa.String(length=64),
        existing_nullable=False,
    )
    op.create_unique_constraint("uq_tokens_taproot_asset_id", "tokens", ["taproot_asset_id"])