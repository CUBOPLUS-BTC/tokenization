"""add referrals, yield accruals, and advanced orders

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-15 12:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("referrer_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("users", sa.Column("referral_code", sa.String(length=12), nullable=True))
    op.execute(
        """
        UPDATE users
        SET referral_code = UPPER(SUBSTRING(REPLACE(id::text, '-', '') FROM 1 FOR 10))
        WHERE referral_code IS NULL;
        """
    )
    op.alter_column("users", "referral_code", nullable=False)
    op.create_foreign_key("fk_users_referrer_id_users", "users", "users", ["referrer_id"], ["id"])
    op.create_unique_constraint("uq_users_referral_code", "users", ["referral_code"])
    op.create_index("ix_users_referrer_id", "users", ["referrer_id"])
    op.create_check_constraint(
        "self_referral_blocked",
        "users",
        "referrer_id IS NULL OR referrer_id <> id",
    )

    op.add_column("orders", sa.Column("order_type", sa.String(length=20), nullable=False, server_default="limit"))
    op.add_column("orders", sa.Column("trigger_price_sat", sa.BigInteger(), nullable=True))
    op.add_column("orders", sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_orders_order_type", "orders", ["order_type"])
    op.create_check_constraint(
        "order_type_allowed",
        "orders",
        "order_type IN ('limit', 'stop_limit')",
    )
    op.create_check_constraint(
        "trigger_price_sat_positive",
        "orders",
        "trigger_price_sat IS NULL OR trigger_price_sat > 0",
    )
    op.create_check_constraint(
        "trigger_price_required",
        "orders",
        "(order_type = 'limit' AND trigger_price_sat IS NULL) OR "
        "(order_type = 'stop_limit' AND trigger_price_sat IS NOT NULL)",
    )

    op.create_table(
        "referral_rewards",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("referrer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("referred_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reward_type", sa.String(length=20), nullable=False, server_default="signup_bonus"),
        sa.Column("amount_sat", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="credited"),
        sa.Column("eligibility_event", sa.String(length=30), nullable=False, server_default="kyc_verified"),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("credited_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["referrer_id"], ["users.id"], name="fk_referral_rewards_referrer_id_users"),
        sa.ForeignKeyConstraint(["referred_user_id"], ["users.id"], name="fk_referral_rewards_referred_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_referral_rewards"),
        sa.UniqueConstraint("referred_user_id", "reward_type", name="uq_referral_rewards_referred_user_reward_type"),
        sa.CheckConstraint("amount_sat > 0", name="ck_referral_rewards_amount_positive"),
        sa.CheckConstraint("referrer_id <> referred_user_id", name="ck_referral_rewards_self_referral_reward_blocked"),
        sa.CheckConstraint("reward_type IN ('signup_bonus')", name="ck_referral_rewards_reward_type_allowed"),
        sa.CheckConstraint("status IN ('credited', 'reversed')", name="ck_referral_rewards_status_allowed"),
    )
    op.create_index("ix_referral_rewards_referrer_id", "referral_rewards", ["referrer_id"])
    op.create_index("ix_referral_rewards_status", "referral_rewards", ["status"])
    op.create_index("ix_referral_rewards_created_at", "referral_rewards", ["created_at"])

    op.create_table(
        "yield_accruals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("annual_rate_pct", sa.Numeric(precision=5, scale=2), nullable=False),
        sa.Column("quantity_held", sa.BigInteger(), nullable=False),
        sa.Column("reference_price_sat", sa.BigInteger(), nullable=False),
        sa.Column("amount_sat", sa.BigInteger(), nullable=False),
        sa.Column("accrued_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accrued_to", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_yield_accruals_user_id_users"),
        sa.ForeignKeyConstraint(["token_id"], ["tokens.id"], name="fk_yield_accruals_token_id_tokens"),
        sa.PrimaryKeyConstraint("id", name="pk_yield_accruals"),
        sa.CheckConstraint("quantity_held > 0", name="ck_yield_accruals_quantity_positive"),
        sa.CheckConstraint("reference_price_sat > 0", name="ck_yield_accruals_reference_price_sat_positive"),
        sa.CheckConstraint("amount_sat > 0", name="ck_yield_accruals_amount_positive"),
        sa.CheckConstraint("annual_rate_pct > 0", name="ck_yield_accruals_annual_rate_pct_positive"),
        sa.CheckConstraint("accrued_to > accrued_from", name="ck_yield_accruals_accrual_window_positive"),
    )
    op.create_index("ix_yield_accruals_user_id", "yield_accruals", ["user_id"])
    op.create_index("ix_yield_accruals_token_id", "yield_accruals", ["token_id"])
    op.create_index("ix_yield_accruals_created_at", "yield_accruals", ["created_at"])

    op.drop_constraint("type_allowed", "treasury", type_="check")
    op.add_column("treasury", sa.Column("source_referral_reward_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_treasury_source_referral_reward_id_referral_rewards",
        "treasury",
        "referral_rewards",
        ["source_referral_reward_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "uq_treasury_source_referral_reward_id",
        "treasury",
        ["source_referral_reward_id"],
    )
    op.create_check_constraint(
        "type_allowed",
        "treasury",
        "type IN ('fee_income', 'disbursement', 'adjustment', 'referral_reward')",
    )


def downgrade() -> None:
    op.drop_constraint("type_allowed", "treasury", type_="check")
    op.drop_constraint("uq_treasury_source_referral_reward_id", "treasury", type_="unique")
    op.drop_constraint(
        "fk_treasury_source_referral_reward_id_referral_rewards",
        "treasury",
        type_="foreignkey",
    )
    op.drop_column("treasury", "source_referral_reward_id")
    op.create_check_constraint(
        "type_allowed",
        "treasury",
        "type IN ('fee_income', 'disbursement', 'adjustment')",
    )

    op.drop_index("ix_yield_accruals_created_at", table_name="yield_accruals")
    op.drop_index("ix_yield_accruals_token_id", table_name="yield_accruals")
    op.drop_index("ix_yield_accruals_user_id", table_name="yield_accruals")
    op.drop_table("yield_accruals")

    op.drop_index("ix_referral_rewards_created_at", table_name="referral_rewards")
    op.drop_index("ix_referral_rewards_status", table_name="referral_rewards")
    op.drop_index("ix_referral_rewards_referrer_id", table_name="referral_rewards")
    op.drop_table("referral_rewards")

    op.drop_constraint("trigger_price_required", "orders", type_="check")
    op.drop_constraint("trigger_price_sat_positive", "orders", type_="check")
    op.drop_constraint("order_type_allowed", "orders", type_="check")
    op.drop_index("ix_orders_order_type", table_name="orders")
    op.drop_column("orders", "triggered_at")
    op.drop_column("orders", "trigger_price_sat")
    op.drop_column("orders", "order_type")

    op.drop_constraint("self_referral_blocked", "users", type_="check")
    op.drop_index("ix_users_referrer_id", table_name="users")
    op.drop_constraint("uq_users_referral_code", "users", type_="unique")
    op.drop_constraint("fk_users_referrer_id_users", "users", type_="foreignkey")
    op.drop_column("users", "referral_code")
    op.drop_column("users", "referrer_id")