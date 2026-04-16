Create the database schema changes required to support real on-chain wallet operations in services/wallet.

Goal:
Add the minimum robust schema support needed for:
- deterministic receive addresses
- address import/watch state
- on-chain deposit reconciliation
- real txid exposure
- idempotent balance updates

Requirements:
- Inspect existing wallet-related tables in services/common/db/metadata.py and current Alembic migrations.
- Propose and implement schema changes for one or more of the following:
  - derived wallet addresses table
  - on-chain deposits / UTXO reconciliation table
  - extra columns on transactions for txid/payment hash visibility and reconciliation metadata if not already present
- Preserve existing constraints and naming conventions.
- Add upgrade() and downgrade() implementations.
- Update any SQLAlchemy metadata definitions accordingly.

Also:
- describe how the new tables/columns are used by the wallet service
- mention indexes and uniqueness constraints needed to prevent double-crediting

Do not implement endpoint logic in this task unless necessary for compile correctness.
At the end, summarize the migration plan and the invariants it protects.