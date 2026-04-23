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


def upgrade() -> None:
    op.add_column("assets", sa.Column("documents_storage_key", sa.Text(), nullable=True))
    op.add_column("assets", sa.Column("documents_filename", sa.String(length=255), nullable=True))
    op.add_column("assets", sa.Column("documents_content_type", sa.String(length=100), nullable=True))
    op.add_column("assets", sa.Column("documents_size_bytes", sa.BigInteger(), nullable=True))
    op.create_check_constraint(
        "ck_assets_documents_size_non_negative",
        "assets",
        "documents_size_bytes IS NULL OR documents_size_bytes >= 0",
    )

    op.add_column(
        "tokens",
        sa.Column("visibility", sa.String(length=20), nullable=False, server_default="public"),
    )
    op.create_index("ix_tokens_visibility", "tokens", ["visibility"], unique=False)
    op.create_check_constraint(
        "ck_tokens_visibility_allowed",
        "tokens",
        "visibility IN ('public', 'private')",
    )

    op.add_column(
        "escrows",
        sa.Column("multisig_mode", sa.String(length=20), nullable=False, server_default="standard"),
    )
    op.create_check_constraint(
        "ck_escrows_multisig_mode_allowed",
        "escrows",
        "multisig_mode IN ('standard', 'external_api')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_escrows_multisig_mode_allowed", "escrows", type_="check")
    op.drop_column("escrows", "multisig_mode")

    op.drop_constraint("ck_tokens_visibility_allowed", "tokens", type_="check")
    op.drop_index("ix_tokens_visibility", table_name="tokens")
    op.drop_column("tokens", "visibility")

    op.drop_constraint("ck_assets_documents_size_non_negative", "assets", type_="check")
    op.drop_column("assets", "documents_size_bytes")
    op.drop_column("assets", "documents_content_type")
    op.drop_column("assets", "documents_filename")
    op.drop_column("assets", "documents_storage_key")
