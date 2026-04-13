from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

users = sa.Table(
    "users",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("email", sa.String(length=255), nullable=True),
    sa.Column("password_hash", sa.String(length=255), nullable=True),
    sa.Column("display_name", sa.String(length=100), nullable=False),
    sa.Column("role", sa.String(length=20), nullable=False, server_default="user"),
    sa.Column("totp_secret", sa.String(length=255), nullable=True),
    sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("email", name="uq_users_email"),
)

wallets = sa.Table(
    "wallets",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, unique=True),
    sa.Column("onchain_balance_sat", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("lightning_balance_sat", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("encrypted_seed", sa.LargeBinary(), nullable=False),
    sa.Column("derivation_path", sa.String(length=50), nullable=False, server_default="m/86'/0'/0'"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_wallets_user_id_users"),
)
