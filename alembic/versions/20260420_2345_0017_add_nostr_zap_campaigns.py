"""add nostr zap campaigns

Revision ID: 0017
Revises: 0016
Create Date: 2026-04-20 23:45:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "nostr_campaigns",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=140), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("funding_mode", sa.String(length=20), nullable=False),
        sa.Column("reward_amount_sat", sa.BigInteger(), nullable=False),
        sa.Column("budget_total_sat", sa.BigInteger(), nullable=False),
        sa.Column("budget_reserved_sat", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("budget_spent_sat", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("budget_refunded_sat", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("max_rewards_per_user", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_nostr_campaigns_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_nostr_campaigns"),
        sa.CheckConstraint(
            "status IN ('draft', 'funding_pending', 'active', 'paused', 'completed', 'exhausted', 'cancelled', 'failed')",
            name="status_allowed",
        ),
        sa.CheckConstraint("funding_mode IN ('intraledger', 'external')", name="funding_mode_allowed"),
        sa.CheckConstraint("reward_amount_sat > 0", name="reward_amount_positive"),
        sa.CheckConstraint("budget_total_sat > 0", name="budget_total_positive"),
        sa.CheckConstraint(
            "budget_reserved_sat >= 0 AND budget_spent_sat >= 0 AND budget_refunded_sat >= 0",
            name="budget_non_negative",
        ),
        sa.CheckConstraint("max_rewards_per_user > 0", name="max_rewards_positive"),
        sa.CheckConstraint("end_at IS NULL OR start_at IS NULL OR end_at > start_at", name="campaign_window_positive"),
    )
    op.create_index("ix_nostr_campaigns_user_id", "nostr_campaigns", ["user_id"])
    op.create_index("ix_nostr_campaigns_status", "nostr_campaigns", ["status"])

    op.create_table(
        "nostr_campaign_triggers",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_type", sa.String(length=30), nullable=False),
        sa.Column("operator", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("case_sensitive", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["nostr_campaigns.id"],
            name="fk_nostr_campaign_triggers_campaign_id_nostr_campaigns",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nostr_campaign_triggers"),
        sa.CheckConstraint(
            "trigger_type IN ('hashtag', 'tag', 'content_substring', 'author_pubkey', 'event_kind')",
            name="trigger_type_allowed",
        ),
        sa.CheckConstraint("operator IN ('equals', 'contains', 'in')", name="operator_allowed"),
        sa.CheckConstraint("char_length(trim(value)) > 0", name="value_not_blank"),
    )
    op.create_index("ix_nostr_campaign_triggers_campaign_id", "nostr_campaign_triggers", ["campaign_id"])

    op.create_table(
        "nostr_campaign_fundings",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("wallet_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("funding_mode", sa.String(length=20), nullable=False),
        sa.Column("amount_sat", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("ln_payment_hash", sa.String(length=64), nullable=True),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["nostr_campaigns.id"],
            name="fk_nostr_campaign_fundings_campaign_id_nostr_campaigns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["wallet_id"], ["wallets.id"], name="fk_nostr_campaign_fundings_wallet_id_wallets"),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name="fk_nostr_campaign_fundings_transaction_id_transactions",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nostr_campaign_fundings"),
        sa.CheckConstraint("funding_mode IN ('intraledger', 'external')", name="funding_mode_allowed"),
        sa.CheckConstraint("amount_sat > 0", name="amount_positive"),
        sa.CheckConstraint("status IN ('pending', 'confirmed', 'cancelled', 'refunded')", name="status_allowed"),
    )
    op.create_index("ix_nostr_campaign_fundings_campaign_id", "nostr_campaign_fundings", ["campaign_id"])
    op.create_index("ix_nostr_campaign_fundings_status", "nostr_campaign_fundings", ["status"])

    op.create_table(
        "nostr_campaign_matches",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("relay_url", sa.Text(), nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("event_pubkey", sa.String(length=64), nullable=False),
        sa.Column("event_kind", sa.Integer(), nullable=False),
        sa.Column("match_fingerprint", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="matched"),
        sa.Column("ignore_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["nostr_campaigns.id"],
            name="fk_nostr_campaign_matches_campaign_id_nostr_campaigns",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nostr_campaign_matches"),
        sa.UniqueConstraint("campaign_id", "event_id", name="uq_nostr_campaign_matches_campaign_event"),
        sa.UniqueConstraint(
            "campaign_id",
            "match_fingerprint",
            name="uq_nostr_campaign_matches_campaign_fingerprint",
        ),
        sa.CheckConstraint("status IN ('matched', 'ignored', 'reserved', 'paid', 'failed')", name="status_allowed"),
    )
    op.create_index("ix_nostr_campaign_matches_campaign_id", "nostr_campaign_matches", ["campaign_id"])
    op.create_index("ix_nostr_campaign_matches_status", "nostr_campaign_matches", ["status"])

    op.create_table(
        "nostr_campaign_payouts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("campaign_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("match_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("recipient_pubkey", sa.String(length=64), nullable=False),
        sa.Column("recipient_lud16", sa.String(length=255), nullable=True),
        sa.Column("recipient_lud06", sa.Text(), nullable=True),
        sa.Column("zap_request_event_id", sa.String(length=64), nullable=True),
        sa.Column("zap_invoice", sa.Text(), nullable=True),
        sa.Column("payment_hash", sa.String(length=64), nullable=True),
        sa.Column("amount_sat", sa.BigInteger(), nullable=False),
        sa.Column("fee_sat", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("NOW()")),
        sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["nostr_campaigns.id"],
            name="fk_nostr_campaign_payouts_campaign_id_nostr_campaigns",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["match_id"],
            ["nostr_campaign_matches.id"],
            name="fk_nostr_campaign_payouts_match_id_nostr_campaign_matches",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_nostr_campaign_payouts"),
        sa.CheckConstraint("amount_sat > 0", name="amount_positive"),
        sa.CheckConstraint("fee_sat IS NULL OR fee_sat >= 0", name="fee_non_negative"),
        sa.CheckConstraint("status IN ('pending', 'succeeded', 'failed')", name="status_allowed"),
    )
    op.create_index("ix_nostr_campaign_payouts_campaign_id", "nostr_campaign_payouts", ["campaign_id"])
    op.create_index("ix_nostr_campaign_payouts_match_id", "nostr_campaign_payouts", ["match_id"])
    op.create_index("ix_nostr_campaign_payouts_status", "nostr_campaign_payouts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_nostr_campaign_payouts_status", table_name="nostr_campaign_payouts")
    op.drop_index("ix_nostr_campaign_payouts_match_id", table_name="nostr_campaign_payouts")
    op.drop_index("ix_nostr_campaign_payouts_campaign_id", table_name="nostr_campaign_payouts")
    op.drop_table("nostr_campaign_payouts")

    op.drop_index("ix_nostr_campaign_matches_status", table_name="nostr_campaign_matches")
    op.drop_index("ix_nostr_campaign_matches_campaign_id", table_name="nostr_campaign_matches")
    op.drop_table("nostr_campaign_matches")

    op.drop_index("ix_nostr_campaign_fundings_status", table_name="nostr_campaign_fundings")
    op.drop_index("ix_nostr_campaign_fundings_campaign_id", table_name="nostr_campaign_fundings")
    op.drop_table("nostr_campaign_fundings")

    op.drop_index("ix_nostr_campaign_triggers_campaign_id", table_name="nostr_campaign_triggers")
    op.drop_table("nostr_campaign_triggers")

    op.drop_index("ix_nostr_campaigns_status", table_name="nostr_campaigns")
    op.drop_index("ix_nostr_campaigns_user_id", table_name="nostr_campaigns")
    op.drop_table("nostr_campaigns")
