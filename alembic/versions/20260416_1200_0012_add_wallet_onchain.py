"""Add wallet_addresses and onchain_deposits

Revision ID: 0012_add_wallet_onchain
Revises: 0011_normalize_late_check_constraint_names
Create Date: 2026-04-16 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '0012_add_wallet_onchain'
down_revision = '0011_normalize_late_check_constraint_names'
branch_labels = None
depends_on = None

def upgrade() -> None:
    # Add fee_sat to transactions
    op.add_column('transactions', sa.Column('fee_sat', sa.BigInteger(), nullable=True))

    # Create wallet_addresses table
    op.create_table('wallet_addresses',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('wallet_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('address', sa.String(length=100), nullable=False),
        sa.Column('derivation_index', sa.Integer(), nullable=False),
        sa.Column('script_pubkey', sa.String(length=100), nullable=False),
        sa.Column('imported_to_node', sa.Boolean(), server_default=sa.text('false'), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.ForeignKeyConstraint(['wallet_id'], ['wallets.id'], name=op.f('fk_wallet_addresses_wallet_id_wallets')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_wallet_addresses')),
        sa.UniqueConstraint('address', name=op.f('uq_wallet_addresses_address')),
        sa.UniqueConstraint('wallet_id', 'derivation_index', name=op.f('uq_wallet_addresses_wallet_derivation'))
    )
    op.create_index(op.f('ix_wallet_addresses_address'), 'wallet_addresses', ['address'], unique=False)
    op.create_index(op.f('ix_wallet_addresses_wallet_id'), 'wallet_addresses', ['wallet_id'], unique=False)

    # Create onchain_deposits table
    op.create_table('onchain_deposits',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('wallet_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('wallet_address_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('txid', sa.String(length=64), nullable=False),
        sa.Column('vout', sa.Integer(), nullable=False),
        sa.Column('amount_sat', sa.BigInteger(), nullable=False),
        sa.Column('confirmations', sa.Integer(), server_default='0', nullable=False),
        sa.Column('status', sa.String(length=20), server_default='pending', nullable=False),
        sa.Column('credited_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
        sa.CheckConstraint("status IN ('pending', 'confirmed', 'credited')", name=op.f('ck_onchain_deposits_status_allowed')),
        sa.CheckConstraint('amount_sat > 0', name=op.f('ck_onchain_deposits_amount_positive')),
        sa.ForeignKeyConstraint(['wallet_address_id'], ['wallet_addresses.id'], name=op.f('fk_onchain_deposits_wallet_address_id_wallet_addrs')),
        sa.ForeignKeyConstraint(['wallet_id'], ['wallets.id'], name=op.f('fk_onchain_deposits_wallet_id_wallets')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_onchain_deposits')),
        sa.UniqueConstraint('txid', 'vout', name=op.f('uq_onchain_deposits_txid_vout'))
    )
    op.create_index(op.f('ix_onchain_deposits_status'), 'onchain_deposits', ['status'], unique=False)
    op.create_index(op.f('ix_onchain_deposits_wallet_id'), 'onchain_deposits', ['wallet_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_onchain_deposits_wallet_id'), table_name='onchain_deposits')
    op.drop_index(op.f('ix_onchain_deposits_status'), table_name='onchain_deposits')
    op.drop_table('onchain_deposits')
    
    op.drop_index(op.f('ix_wallet_addresses_wallet_id'), table_name='wallet_addresses')
    op.drop_index(op.f('ix_wallet_addresses_address'), table_name='wallet_addresses')
    op.drop_table('wallet_addresses')

    op.drop_column('transactions', 'fee_sat')
