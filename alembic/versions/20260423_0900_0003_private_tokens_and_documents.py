"""private tokens and managed asset documents

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-23 09:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _has_check_constraint(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(
        constraint.get("name") == constraint_name
        for constraint in inspector.get_check_constraints(table_name)
    )


def upgrade() -> None:
    if not _has_column("assets", "documents_storage_key"):
        op.add_column("assets", sa.Column("documents_storage_key", sa.Text(), nullable=True))
    if not _has_column("assets", "documents_filename"):
        op.add_column("assets", sa.Column("documents_filename", sa.String(length=255), nullable=True))
    if not _has_column("assets", "documents_content_type"):
        op.add_column("assets", sa.Column("documents_content_type", sa.String(length=100), nullable=True))
    if not _has_column("assets", "documents_size_bytes"):
        op.add_column("assets", sa.Column("documents_size_bytes", sa.BigInteger(), nullable=True))
    if not _has_check_constraint("assets", "ck_assets_documents_size_non_negative"):
        op.create_check_constraint(
            "ck_assets_documents_size_non_negative",
            "assets",
            "documents_size_bytes IS NULL OR documents_size_bytes >= 0",
        )

    if not _has_column("tokens", "visibility"):
        op.add_column(
            "tokens",
            sa.Column("visibility", sa.String(length=20), nullable=False, server_default="public"),
        )
    if not _has_index("tokens", "ix_tokens_visibility"):
        op.create_index("ix_tokens_visibility", "tokens", ["visibility"], unique=False)
    if not _has_check_constraint("tokens", "ck_tokens_visibility_allowed"):
        op.create_check_constraint(
            "ck_tokens_visibility_allowed",
            "tokens",
            "visibility IN ('public', 'private')",
        )

    if not _has_column("escrows", "multisig_mode"):
        op.add_column(
            "escrows",
            sa.Column("multisig_mode", sa.String(length=20), nullable=False, server_default="standard"),
        )
    if not _has_check_constraint("escrows", "ck_escrows_multisig_mode_allowed"):
        op.create_check_constraint(
            "ck_escrows_multisig_mode_allowed",
            "escrows",
            "multisig_mode IN ('standard', 'external_api')",
        )


def downgrade() -> None:
    if _has_check_constraint("escrows", "ck_escrows_multisig_mode_allowed"):
        op.drop_constraint("ck_escrows_multisig_mode_allowed", "escrows", type_="check")
    if _has_column("escrows", "multisig_mode"):
        op.drop_column("escrows", "multisig_mode")

    if _has_check_constraint("tokens", "ck_tokens_visibility_allowed"):
        op.drop_constraint("ck_tokens_visibility_allowed", "tokens", type_="check")
    if _has_index("tokens", "ix_tokens_visibility"):
        op.drop_index("ix_tokens_visibility", table_name="tokens")
    if _has_column("tokens", "visibility"):
        op.drop_column("tokens", "visibility")

    if _has_check_constraint("assets", "ck_assets_documents_size_non_negative"):
        op.drop_constraint("ck_assets_documents_size_non_negative", "assets", type_="check")
    if _has_column("assets", "documents_size_bytes"):
        op.drop_column("assets", "documents_size_bytes")
    if _has_column("assets", "documents_content_type"):
        op.drop_column("assets", "documents_content_type")
    if _has_column("assets", "documents_filename"):
        op.drop_column("assets", "documents_filename")
    if _has_column("assets", "documents_storage_key"):
        op.drop_column("assets", "documents_storage_key")
