Implement on-chain deposit detection and wallet balance reconciliation in services/wallet.

Current problem:
The wallet service does not detect incoming BTC deposits and does not credit wallets.onchain_balance_sat when funds arrive.

Requirements:
- Inspect current wallet transaction persistence and any existing Bitcoin Core readiness/config usage.
- Add logic to detect transactions paying to addresses previously derived/imported for a user.
- Persist deposit transactions with real txid values.
- Update wallets.onchain_balance_sat when deposits are detected according to a clear confirmation policy.
- Ensure idempotency: the same deposit must never be credited twice.
- Store enough metadata to track:
  - txid
  - vout if needed
  - address
  - amount
  - confirmation count
  - credited_at / reconciled_at
  - wallet owner
  - current status (pending/confirmed)

Implementation suggestions:
- Add a dedicated table for derived on-chain addresses and/or deposit UTXO reconciliation state if needed.
- Add a Bitcoin Core RPC client/helper if one does not already exist in wallet.
- Support a practical synchronization path for the current architecture:
  - either a periodic background reconciliation loop inside wallet service
  - or an explicit sync helper invoked from wallet reads/writes
- Be explicit in code comments about the confirmation threshold used for crediting.
- Update GET /wallet and GET /wallet/transactions behavior so credited deposits become visible.

Add tests for:
- single deposit credited once
- repeated sync does not double-credit
- pending vs confirmed deposit handling
- multiple deposits to same wallet
- deposits to different users
- RPC failures handled safely

Also record business events and any needed audit events.
At the end, summarize design decisions and migration changes.