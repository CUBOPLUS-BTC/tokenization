"""normalize late check constraint names

Revision ID: 0011
Revises: 0010
Create Date: 2026-04-15 16:00:00
"""

from typing import Sequence, Union

from alembic import op


revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _rename_constraint(table_name: str, old_name: str, new_name: str) -> None:
    op.execute(f'ALTER TABLE "{table_name}" RENAME CONSTRAINT "{old_name}" TO "{new_name}"')


def upgrade() -> None:
    rename_pairs = [
        ("disputes", "ck_disputes_ck_disputes_status_allowed", "ck_disputes_status_allowed"),
        ("disputes", "ck_disputes_ck_disputes_resolution_allowed", "ck_disputes_resolution_allowed"),
        ("audit_logs", "ck_audit_logs_ck_audit_logs_outcome_allowed", "ck_audit_logs_outcome_allowed"),
        (
            "kyc_verifications",
            "ck_kyc_verifications_ck_kyc_verifications_status_allowed",
            "ck_kyc_verifications_status_allowed",
        ),
        (
            "referral_rewards",
            "ck_referral_rewards_ck_referral_rewards_amount_positive",
            "ck_referral_rewards_amount_positive",
        ),
        (
            "referral_rewards",
            "ck_referral_rewards_ck_referral_rewards_self_referral_r_c41f",
            "ck_referral_rewards_self_referral_reward_blocked",
        ),
        (
            "referral_rewards",
            "ck_referral_rewards_ck_referral_rewards_reward_type_allowed",
            "ck_referral_rewards_reward_type_allowed",
        ),
        (
            "referral_rewards",
            "ck_referral_rewards_ck_referral_rewards_status_allowed",
            "ck_referral_rewards_status_allowed",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_ck_yield_accruals_quantity_positive",
            "ck_yield_accruals_quantity_positive",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_ck_yield_accruals_reference_price_sat_e88f",
            "ck_yield_accruals_reference_price_sat_positive",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_ck_yield_accruals_amount_positive",
            "ck_yield_accruals_amount_positive",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_ck_yield_accruals_annual_rate_pct_positive",
            "ck_yield_accruals_annual_rate_pct_positive",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_ck_yield_accruals_accrual_window_positive",
            "ck_yield_accruals_accrual_window_positive",
        ),
    ]

    for table_name, old_name, new_name in rename_pairs:
        _rename_constraint(table_name, old_name, new_name)


def downgrade() -> None:
    rename_pairs = [
        ("disputes", "ck_disputes_status_allowed", "ck_disputes_ck_disputes_status_allowed"),
        ("disputes", "ck_disputes_resolution_allowed", "ck_disputes_ck_disputes_resolution_allowed"),
        ("audit_logs", "ck_audit_logs_outcome_allowed", "ck_audit_logs_ck_audit_logs_outcome_allowed"),
        (
            "kyc_verifications",
            "ck_kyc_verifications_status_allowed",
            "ck_kyc_verifications_ck_kyc_verifications_status_allowed",
        ),
        (
            "referral_rewards",
            "ck_referral_rewards_amount_positive",
            "ck_referral_rewards_ck_referral_rewards_amount_positive",
        ),
        (
            "referral_rewards",
            "ck_referral_rewards_self_referral_reward_blocked",
            "ck_referral_rewards_ck_referral_rewards_self_referral_r_c41f",
        ),
        (
            "referral_rewards",
            "ck_referral_rewards_reward_type_allowed",
            "ck_referral_rewards_ck_referral_rewards_reward_type_allowed",
        ),
        (
            "referral_rewards",
            "ck_referral_rewards_status_allowed",
            "ck_referral_rewards_ck_referral_rewards_status_allowed",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_quantity_positive",
            "ck_yield_accruals_ck_yield_accruals_quantity_positive",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_reference_price_sat_positive",
            "ck_yield_accruals_ck_yield_accruals_reference_price_sat_e88f",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_amount_positive",
            "ck_yield_accruals_ck_yield_accruals_amount_positive",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_annual_rate_pct_positive",
            "ck_yield_accruals_ck_yield_accruals_annual_rate_pct_positive",
        ),
        (
            "yield_accruals",
            "ck_yield_accruals_accrual_window_positive",
            "ck_yield_accruals_ck_yield_accruals_accrual_window_positive",
        ),
    ]

    for table_name, old_name, new_name in rename_pairs:
        _rename_constraint(table_name, old_name, new_name)