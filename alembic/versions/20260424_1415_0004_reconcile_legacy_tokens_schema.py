"""reconcile legacy tokens schema with current metadata

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-24 14:15:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bind():
    return op.get_bind()


def _inspector() -> sa.Inspector:
    return sa.inspect(_bind())


def _has_table(table_name: str) -> bool:
    return table_name in _inspector().get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    return any(column.get("name") == column_name for column in _inspector().get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    return any(index.get("name") == index_name for index in _inspector().get_indexes(table_name))


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
    return any(
        constraint.get("name") == constraint_name
        for constraint in _inspector().get_unique_constraints(table_name)
    )


def _has_check_constraint(table_name: str, constraint_name: str) -> bool:
    return any(
        constraint.get("name") == constraint_name
        for constraint in _inspector().get_check_constraints(table_name)
    )


def upgrade() -> None:
    if not _has_table("tokens"):
        return

    if not _has_column("tokens", "liquid_asset_id"):
        op.add_column("tokens", sa.Column("liquid_asset_id", sa.String(length=64), nullable=True))
        if _has_column("tokens", "taproot_asset_id"):
            op.execute("UPDATE tokens SET liquid_asset_id = taproot_asset_id WHERE liquid_asset_id IS NULL")
        op.execute(
            "UPDATE tokens "
            "SET liquid_asset_id = lower(md5(id::text) || md5(id::text)) "
            "WHERE liquid_asset_id IS NULL OR liquid_asset_id = ''"
        )
        op.alter_column("tokens", "liquid_asset_id", nullable=False)

    if not _has_column("tokens", "ticker"):
        op.add_column("tokens", sa.Column("ticker", sa.String(length=10), nullable=True))
        op.execute(
            "UPDATE tokens "
            "SET ticker = left(upper(regexp_replace(liquid_asset_id, '[^A-Za-z0-9]+', '', 'g')), 10) "
            "WHERE ticker IS NULL OR ticker = ''"
        )
        op.execute("UPDATE tokens SET ticker = 'TOKEN' WHERE ticker IS NULL OR ticker = ''")
        op.alter_column("tokens", "ticker", nullable=False)

    if not _has_column("tokens", "circulating_supply"):
        op.add_column(
            "tokens",
            sa.Column("circulating_supply", sa.BigInteger(), nullable=False, server_default="0"),
        )

    if not _has_column("tokens", "unit_price_sat"):
        op.add_column(
            "tokens",
            sa.Column("unit_price_sat", sa.BigInteger(), nullable=False, server_default="0"),
        )

    if not _has_column("tokens", "visibility"):
        op.add_column(
            "tokens",
            sa.Column("visibility", sa.String(length=20), nullable=False, server_default="public"),
        )

    if not _has_column("tokens", "metadata"):
        op.add_column("tokens", sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
        if _has_column("tokens", "metadata_json"):
            op.execute("UPDATE tokens SET metadata = metadata_json WHERE metadata IS NULL")

    if not _has_column("tokens", "minted_at"):
        op.add_column(
            "tokens",
            sa.Column("minted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        )

    if not _has_column("tokens", "created_at"):
        op.add_column(
            "tokens",
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        )

    if not _has_index("tokens", "ix_tokens_asset_id"):
        op.create_index("ix_tokens_asset_id", "tokens", ["asset_id"], unique=False)
    if not _has_index("tokens", "ix_tokens_visibility"):
        op.create_index("ix_tokens_visibility", "tokens", ["visibility"], unique=False)
    if not _has_unique_constraint("tokens", "uq_tokens_liquid_asset_id"):
        op.create_unique_constraint("uq_tokens_liquid_asset_id", "tokens", ["liquid_asset_id"])
    if not _has_check_constraint("tokens", "ck_tokens_visibility_allowed"):
        op.create_check_constraint(
            "ck_tokens_visibility_allowed",
            "tokens",
            "visibility IN ('public', 'private')",
        )


def downgrade() -> None:
    if not _has_table("tokens"):
        return

    if _has_check_constraint("tokens", "ck_tokens_visibility_allowed"):
        op.drop_constraint("ck_tokens_visibility_allowed", "tokens", type_="check")
    if _has_unique_constraint("tokens", "uq_tokens_liquid_asset_id"):
        op.drop_constraint("uq_tokens_liquid_asset_id", "tokens", type_="unique")
    if _has_index("tokens", "ix_tokens_visibility"):
        op.drop_index("ix_tokens_visibility", table_name="tokens")
    if _has_index("tokens", "ix_tokens_asset_id"):
        op.drop_index("ix_tokens_asset_id", table_name="tokens")
    if _has_column("tokens", "created_at"):
        op.drop_column("tokens", "created_at")
    if _has_column("tokens", "minted_at"):
        op.drop_column("tokens", "minted_at")
    if _has_column("tokens", "metadata"):
        op.drop_column("tokens", "metadata")
    if _has_column("tokens", "visibility"):
        op.drop_column("tokens", "visibility")
    if _has_column("tokens", "unit_price_sat"):
        op.drop_column("tokens", "unit_price_sat")
    if _has_column("tokens", "circulating_supply"):
        op.drop_column("tokens", "circulating_supply")
    if _has_column("tokens", "ticker"):
        op.drop_column("tokens", "ticker")
    if _has_column("tokens", "liquid_asset_id"):
        op.drop_column("tokens", "liquid_asset_id")
