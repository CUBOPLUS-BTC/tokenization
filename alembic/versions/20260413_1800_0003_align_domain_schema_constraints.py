"""align domain schema constraints

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-13 18:00:00
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("tokens", "metadata_json", new_column_name="metadata")

    op.create_check_constraint(
        "ck_assets_category_allowed",
        "assets",
        "category IN ('real_estate', 'commodity', 'invoice', 'art', 'other')",
    )
    op.create_check_constraint(
        "ck_assets_status_allowed",
        "assets",
        "status IN ('pending', 'evaluating', 'approved', 'rejected', 'tokenized')",
    )
    op.create_check_constraint(
        "ck_assets_ai_score_range",
        "assets",
        "ai_score IS NULL OR (ai_score >= 0 AND ai_score <= 100)",
    )

    op.create_check_constraint(
        "ck_token_balances_balance_non_negative",
        "token_balances",
        "balance >= 0",
    )

    op.create_check_constraint(
        "ck_orders_side_allowed",
        "orders",
        "side IN ('buy', 'sell')",
    )
    op.create_check_constraint(
        "ck_orders_quantity_positive",
        "orders",
        "quantity > 0",
    )
    op.create_check_constraint(
        "ck_orders_price_sat_positive",
        "orders",
        "price_sat > 0",
    )
    op.create_check_constraint(
        "ck_orders_status_allowed",
        "orders",
        "status IN ('open', 'partially_filled', 'filled', 'cancelled')",
    )

    op.create_check_constraint(
        "ck_trades_status_allowed",
        "trades",
        "status IN ('pending', 'escrowed', 'settled', 'disputed')",
    )

    op.create_check_constraint(
        "ck_escrows_status_allowed",
        "escrows",
        "status IN ('created', 'funded', 'released', 'refunded', 'disputed')",
    )

    op.drop_constraint("fk_treasury_trade_id_trades", "treasury", type_="foreignkey")
    op.create_foreign_key(
        "fk_treasury_source_trade_id_trades",
        "treasury",
        "trades",
        ["source_trade_id"],
        ["id"],
    )
    op.create_check_constraint(
        "ck_treasury_type_allowed",
        "treasury",
        "type IN ('fee_income', 'disbursement', 'adjustment')",
    )

    op.create_check_constraint(
        "ck_courses_category_allowed",
        "courses",
        "category IN ('bitcoin', 'finance', 'programming', 'entrepreneurship')",
    )
    op.create_check_constraint(
        "ck_courses_difficulty_allowed",
        "courses",
        "difficulty IN ('beginner', 'intermediate', 'advanced')",
    )

    op.create_check_constraint(
        "ck_enrollments_progress_range",
        "enrollments",
        "progress >= 0 AND progress <= 100",
    )


def downgrade() -> None:
    op.drop_constraint("ck_enrollments_progress_range", "enrollments", type_="check")

    op.drop_constraint("ck_courses_difficulty_allowed", "courses", type_="check")
    op.drop_constraint("ck_courses_category_allowed", "courses", type_="check")

    op.drop_constraint("ck_treasury_type_allowed", "treasury", type_="check")
    op.drop_constraint("fk_treasury_source_trade_id_trades", "treasury", type_="foreignkey")
    op.create_foreign_key(
        "fk_treasury_trade_id_trades",
        "treasury",
        "trades",
        ["source_trade_id"],
        ["id"],
    )

    op.drop_constraint("ck_escrows_status_allowed", "escrows", type_="check")
    op.drop_constraint("ck_trades_status_allowed", "trades", type_="check")

    op.drop_constraint("ck_orders_status_allowed", "orders", type_="check")
    op.drop_constraint("ck_orders_price_sat_positive", "orders", type_="check")
    op.drop_constraint("ck_orders_quantity_positive", "orders", type_="check")
    op.drop_constraint("ck_orders_side_allowed", "orders", type_="check")

    op.drop_constraint(
        "ck_token_balances_balance_non_negative",
        "token_balances",
        type_="check",
    )

    op.drop_constraint("ck_assets_ai_score_range", "assets", type_="check")
    op.drop_constraint("ck_assets_status_allowed", "assets", type_="check")
    op.drop_constraint("ck_assets_category_allowed", "assets", type_="check")

    op.alter_column("tokens", "metadata", new_column_name="metadata_json")
