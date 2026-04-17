"""expand marketplace escrow lifecycle states

Revision ID: 0014
Revises: 0013
Create Date: 2026-04-16 19:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("escrows", sa.Column("refund_txid", sa.String(length=64), nullable=True))

    op.drop_constraint("ck_trades_status_allowed", "trades", type_="check")
    op.create_check_constraint(
        "ck_trades_status_allowed",
        "trades",
        "status IN ('pending', 'escrowed', 'settled', 'disputed', 'cancelled')",
    )

    op.drop_constraint("ck_escrows_status_allowed", "escrows", type_="check")
    op.create_check_constraint(
        "ck_escrows_status_allowed",
        "escrows",
        "status IN ('created', 'funded', 'inspection_pending', 'released', 'refunded', 'disputed', 'expired')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_escrows_status_allowed", "escrows", type_="check")
    op.create_check_constraint(
        "ck_escrows_status_allowed",
        "escrows",
        "status IN ('created', 'funded', 'released', 'refunded', 'disputed')",
    )

    op.drop_constraint("ck_trades_status_allowed", "trades", type_="check")
    op.create_check_constraint(
        "ck_trades_status_allowed",
        "trades",
        "status IN ('pending', 'escrowed', 'settled', 'disputed')",
    )

    op.drop_column("escrows", "refund_txid")
