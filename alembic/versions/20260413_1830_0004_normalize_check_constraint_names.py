"""normalize check constraint names

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-13 18:30:00
"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rename_constraint(table_name: str, old_name: str, new_name: str) -> None:
    op.execute(
        f'ALTER TABLE "{table_name}" RENAME CONSTRAINT "{old_name}" TO "{new_name}"'
    )


def upgrade() -> None:
    rename_pairs = [
        ("users", "ck_users_ck_users_role_allowed", "ck_users_role_allowed"),
        (
            "wallets",
            "ck_wallets_ck_wallets_balances_non_negative",
            "ck_wallets_balances_non_negative",
        ),
        (
            "transactions",
            "ck_transactions_ck_transactions_amount_positive",
            "ck_transactions_amount_positive",
        ),
        (
            "transactions",
            "ck_transactions_ck_transactions_direction_allowed",
            "ck_transactions_direction_allowed",
        ),
        (
            "transactions",
            "ck_transactions_ck_transactions_status_allowed",
            "ck_transactions_status_allowed",
        ),
        (
            "transactions",
            "ck_transactions_ck_transactions_type_allowed",
            "ck_transactions_type_allowed",
        ),
        (
            "assets",
            "ck_assets_ck_assets_category_allowed",
            "ck_assets_category_allowed",
        ),
        (
            "assets",
            "ck_assets_ck_assets_status_allowed",
            "ck_assets_status_allowed",
        ),
        (
            "assets",
            "ck_assets_ck_assets_ai_score_range",
            "ck_assets_ai_score_range",
        ),
        (
            "token_balances",
            "ck_token_balances_ck_token_balances_balance_non_negative",
            "ck_token_balances_balance_non_negative",
        ),
        (
            "orders",
            "ck_orders_ck_orders_side_allowed",
            "ck_orders_side_allowed",
        ),
        (
            "orders",
            "ck_orders_ck_orders_quantity_positive",
            "ck_orders_quantity_positive",
        ),
        (
            "orders",
            "ck_orders_ck_orders_price_sat_positive",
            "ck_orders_price_sat_positive",
        ),
        (
            "orders",
            "ck_orders_ck_orders_status_allowed",
            "ck_orders_status_allowed",
        ),
        (
            "trades",
            "ck_trades_ck_trades_status_allowed",
            "ck_trades_status_allowed",
        ),
        (
            "escrows",
            "ck_escrows_ck_escrows_status_allowed",
            "ck_escrows_status_allowed",
        ),
        (
            "treasury",
            "ck_treasury_ck_treasury_type_allowed",
            "ck_treasury_type_allowed",
        ),
        (
            "courses",
            "ck_courses_ck_courses_category_allowed",
            "ck_courses_category_allowed",
        ),
        (
            "courses",
            "ck_courses_ck_courses_difficulty_allowed",
            "ck_courses_difficulty_allowed",
        ),
        (
            "enrollments",
            "ck_enrollments_ck_enrollments_progress_range",
            "ck_enrollments_progress_range",
        ),
    ]

    for table_name, old_name, new_name in rename_pairs:
        _rename_constraint(table_name, old_name, new_name)



def downgrade() -> None:
    rename_pairs = [
        ("users", "ck_users_role_allowed", "ck_users_ck_users_role_allowed"),
        (
            "wallets",
            "ck_wallets_balances_non_negative",
            "ck_wallets_ck_wallets_balances_non_negative",
        ),
        (
            "transactions",
            "ck_transactions_amount_positive",
            "ck_transactions_ck_transactions_amount_positive",
        ),
        (
            "transactions",
            "ck_transactions_direction_allowed",
            "ck_transactions_ck_transactions_direction_allowed",
        ),
        (
            "transactions",
            "ck_transactions_status_allowed",
            "ck_transactions_ck_transactions_status_allowed",
        ),
        (
            "transactions",
            "ck_transactions_type_allowed",
            "ck_transactions_ck_transactions_type_allowed",
        ),
        (
            "assets",
            "ck_assets_category_allowed",
            "ck_assets_ck_assets_category_allowed",
        ),
        (
            "assets",
            "ck_assets_status_allowed",
            "ck_assets_ck_assets_status_allowed",
        ),
        (
            "assets",
            "ck_assets_ai_score_range",
            "ck_assets_ck_assets_ai_score_range",
        ),
        (
            "token_balances",
            "ck_token_balances_balance_non_negative",
            "ck_token_balances_ck_token_balances_balance_non_negative",
        ),
        (
            "orders",
            "ck_orders_side_allowed",
            "ck_orders_ck_orders_side_allowed",
        ),
        (
            "orders",
            "ck_orders_quantity_positive",
            "ck_orders_ck_orders_quantity_positive",
        ),
        (
            "orders",
            "ck_orders_price_sat_positive",
            "ck_orders_ck_orders_price_sat_positive",
        ),
        (
            "orders",
            "ck_orders_status_allowed",
            "ck_orders_ck_orders_status_allowed",
        ),
        (
            "trades",
            "ck_trades_status_allowed",
            "ck_trades_ck_trades_status_allowed",
        ),
        (
            "escrows",
            "ck_escrows_status_allowed",
            "ck_escrows_ck_escrows_status_allowed",
        ),
        (
            "treasury",
            "ck_treasury_type_allowed",
            "ck_treasury_ck_treasury_type_allowed",
        ),
        (
            "courses",
            "ck_courses_category_allowed",
            "ck_courses_ck_courses_category_allowed",
        ),
        (
            "courses",
            "ck_courses_difficulty_allowed",
            "ck_courses_ck_courses_difficulty_allowed",
        ),
        (
            "enrollments",
            "ck_enrollments_progress_range",
            "ck_enrollments_ck_enrollments_progress_range",
        ),
    ]

    for table_name, old_name, new_name in rename_pairs:
        _rename_constraint(table_name, old_name, new_name)