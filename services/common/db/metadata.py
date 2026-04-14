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
    sa.Column("backup_codes", postgresql.JSONB, nullable=True),
    sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    sa.UniqueConstraint("email", name="uq_users_email"),
    sa.Index("ix_users_role", "role"),
    sa.CheckConstraint(
        "role IN ('user', 'seller', 'admin', 'auditor')",
        name="role_allowed",
    ),
)

refresh_token_sessions = sa.Table(
    "refresh_token_sessions",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("token_jti", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("replaced_by_jti", postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(
        ["user_id"],
        ["users.id"],
        name="fk_refresh_token_sessions_user_id_users",
    ),
    sa.UniqueConstraint("token_jti", name="uq_refresh_token_sessions_token_jti"),
    sa.Index("ix_refresh_token_sessions_user_id", "user_id"),
    sa.Index("ix_refresh_token_sessions_expires_at", "expires_at"),
)

wallets = sa.Table(
    "wallets",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("onchain_balance_sat", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("lightning_balance_sat", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("encrypted_seed", sa.LargeBinary(), nullable=False),
    sa.Column("derivation_path", sa.String(length=50), nullable=False, server_default="m/86'/0'/0'"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_wallets_user_id_users"),
    sa.UniqueConstraint("user_id", name="uq_wallets_user_id"),
    sa.CheckConstraint(
        "onchain_balance_sat >= 0 AND lightning_balance_sat >= 0",
        name="balances_non_negative",
    ),
)

nostr_identities = sa.Table(
    "nostr_identities",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("pubkey", sa.String(length=64), nullable=False),
    sa.Column("relay_urls", postgresql.ARRAY(sa.Text()), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_nostr_identities_user_id_users"),
    sa.UniqueConstraint("pubkey", name="uq_nostr_identities_pubkey"),
)

transactions = sa.Table(
    "transactions",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("type", sa.String(length=30), nullable=False),
    sa.Column("amount_sat", sa.BigInteger(), nullable=False),
    sa.Column("direction", sa.String(length=4), nullable=False),
    sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
    sa.Column("txid", sa.String(length=64), nullable=True),
    sa.Column("ln_payment_hash", sa.String(length=64), nullable=True),
    sa.Column("description", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], name="fk_transactions_wallet_id_wallets"),
    sa.Index("ix_transactions_wallet_id", "wallet_id"),
    sa.Index("ix_transactions_type", "type"),
    sa.Index("ix_transactions_status", "status"),
    sa.Index("ix_transactions_created_at", "created_at"),
    sa.CheckConstraint("amount_sat > 0", name="amount_positive"),
    sa.CheckConstraint(
        "direction IN ('in', 'out')",
        name="direction_allowed",
    ),
    sa.CheckConstraint(
        "status IN ('pending', 'confirmed', 'failed')",
        name="status_allowed",
    ),
    sa.CheckConstraint(
        "type IN ('deposit', 'withdrawal', 'ln_send', 'ln_receive', 'escrow_lock', 'escrow_release', 'fee')",
        name="type_allowed",
    ),
)

assets = sa.Table(
    "assets",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("name", sa.String(length=200), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("category", sa.String(length=50), nullable=False),
    sa.Column("valuation_sat", sa.BigInteger(), nullable=False),
    sa.Column("documents_url", sa.Text(), nullable=True),
    sa.Column("ai_score", sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column("ai_analysis", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("projected_roi", sa.Numeric(precision=5, scale=2), nullable=True),
    sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(["owner_id"], ["users.id"], name="fk_assets_owner_id_users"),
    sa.Index("ix_assets_owner_id", "owner_id"),
    sa.Index("ix_assets_status", "status"),
    sa.Index("ix_assets_category", "category"),
    sa.CheckConstraint(
        "category IN ('real_estate', 'commodity', 'invoice', 'art', 'other')",
        name="category_allowed",
    ),
    sa.CheckConstraint(
        "status IN ('pending', 'evaluating', 'approved', 'rejected', 'tokenized')",
        name="status_allowed",
    ),
    sa.CheckConstraint(
        "ai_score IS NULL OR (ai_score >= 0 AND ai_score <= 100)",
        name="ai_score_range",
    ),
)

tokens = sa.Table(
    "tokens",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("taproot_asset_id", sa.String(length=64), nullable=False),
    sa.Column("total_supply", sa.BigInteger(), nullable=False),
    sa.Column("circulating_supply", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("unit_price_sat", sa.BigInteger(), nullable=False),
    sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("minted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(["asset_id"], ["assets.id"], name="fk_tokens_asset_id_assets"),
    sa.UniqueConstraint("taproot_asset_id", name="uq_tokens_taproot_asset_id"),
    sa.Index("ix_tokens_asset_id", "asset_id"),
)

token_balances = sa.Table(
    "token_balances",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("token_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("balance", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_token_balances_user_id_users"),
    sa.ForeignKeyConstraint(["token_id"], ["tokens.id"], name="fk_token_balances_token_id_tokens"),
    sa.UniqueConstraint("user_id", "token_id", name="uq_token_balances_user_token"),
    sa.Index("ix_token_balances_user_id", "user_id"),
    sa.Index("ix_token_balances_token_id", "token_id"),
    sa.CheckConstraint("balance >= 0", name="balance_non_negative"),
)

orders = sa.Table(
    "orders",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("token_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("side", sa.String(length=4), nullable=False),
    sa.Column("quantity", sa.BigInteger(), nullable=False),
    sa.Column("price_sat", sa.BigInteger(), nullable=False),
    sa.Column("filled_quantity", sa.BigInteger(), nullable=False, server_default="0"),
    sa.Column("status", sa.String(length=20), nullable=False, server_default="open"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_orders_user_id_users"),
    sa.ForeignKeyConstraint(["token_id"], ["tokens.id"], name="fk_orders_token_id_tokens"),
    sa.Index("ix_orders_user_id", "user_id"),
    sa.Index("ix_orders_token_id", "token_id"),
    sa.CheckConstraint("side IN ('buy', 'sell')", name="side_allowed"),
    sa.CheckConstraint("quantity > 0", name="quantity_positive"),
    sa.CheckConstraint("price_sat > 0", name="price_sat_positive"),
    sa.CheckConstraint(
        "status IN ('open', 'partially_filled', 'filled', 'cancelled')",
        name="status_allowed",
    ),
)

trades = sa.Table(
    "trades",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("buy_order_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("sell_order_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("token_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("quantity", sa.BigInteger(), nullable=False),
    sa.Column("price_sat", sa.BigInteger(), nullable=False),
    sa.Column("total_sat", sa.BigInteger(), nullable=False),
    sa.Column("fee_sat", sa.BigInteger(), nullable=False),
    sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(["buy_order_id"], ["orders.id"], name="fk_trades_buy_order_id_orders"),
    sa.ForeignKeyConstraint(["sell_order_id"], ["orders.id"], name="fk_trades_sell_order_id_orders"),
    sa.ForeignKeyConstraint(["token_id"], ["tokens.id"], name="fk_trades_token_id_tokens"),
    sa.Index("ix_trades_token_id", "token_id"),
    sa.Index("ix_trades_status", "status"),
    sa.CheckConstraint(
        "status IN ('pending', 'escrowed', 'settled', 'disputed')",
        name="status_allowed",
    ),
)

escrows = sa.Table(
    "escrows",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("trade_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("multisig_address", sa.String(length=100), nullable=False),
    sa.Column("buyer_pubkey", sa.String(length=66), nullable=False),
    sa.Column("seller_pubkey", sa.String(length=66), nullable=False),
    sa.Column("platform_pubkey", sa.String(length=66), nullable=False),
    sa.Column("locked_amount_sat", sa.BigInteger(), nullable=False),
    sa.Column("funding_txid", sa.String(length=64), nullable=True),
    sa.Column("release_txid", sa.String(length=64), nullable=True),
    sa.Column("collected_signatures", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column("status", sa.String(length=20), nullable=False, server_default="created"),
    sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(["trade_id"], ["trades.id"], name="fk_escrows_trade_id_trades"),
    sa.UniqueConstraint("trade_id", name="uq_escrows_trade_id"),
    sa.Index("ix_escrows_status", "status"),
    sa.CheckConstraint(
        "status IN ('created', 'funded', 'released', 'refunded', 'disputed')",
        name="status_allowed",
    ),
)

treasury = sa.Table(
    "treasury",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("source_trade_id", postgresql.UUID(as_uuid=True), nullable=True),
    sa.Column("type", sa.String(length=20), nullable=False),
    sa.Column("amount_sat", sa.BigInteger(), nullable=False),
    sa.Column("balance_after_sat", sa.BigInteger(), nullable=False),
    sa.Column("description", sa.Text(), nullable=True),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.ForeignKeyConstraint(["source_trade_id"], ["trades.id"], name="fk_treasury_source_trade_id_trades"),
    sa.Index("ix_treasury_type", "type"),
    sa.Index("ix_treasury_created_at", "created_at"),
    sa.CheckConstraint(
        "type IN ('fee_income', 'disbursement', 'adjustment')",
        name="type_allowed",
    ),
)

courses = sa.Table(
    "courses",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("title", sa.String(length=200), nullable=False),
    sa.Column("description", sa.Text(), nullable=False),
    sa.Column("content_url", sa.Text(), nullable=False),
    sa.Column("category", sa.String(length=50), nullable=False),
    sa.Column("difficulty", sa.String(length=20), nullable=False),
    sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.CheckConstraint(
        "category IN ('bitcoin', 'finance', 'programming', 'entrepreneurship')",
        name="category_allowed",
    ),
    sa.CheckConstraint(
        "difficulty IN ('beginner', 'intermediate', 'advanced')",
        name="difficulty_allowed",
    ),
)

enrollments = sa.Table(
    "enrollments",
    metadata,
    sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
    sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("course_id", postgresql.UUID(as_uuid=True), nullable=False),
    sa.Column("progress", sa.Numeric(precision=5, scale=2), nullable=False, server_default="0"),
    sa.Column("enrolled_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
    sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_enrollments_user_id_users"),
    sa.ForeignKeyConstraint(["course_id"], ["courses.id"], name="fk_enrollments_course_id_courses"),
    sa.UniqueConstraint("user_id", "course_id", name="uq_enrollments_user_course"),
    sa.CheckConstraint("progress >= 0 AND progress <= 100", name="progress_range"),
)
