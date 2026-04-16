You are working inside the RWA Tokenization Platform repository.

Target area:
- services/wallet
- related shared modules in services/common only when necessary
- tests for wallet behavior

Context:
The current wallet service has real Lightning integration through LND, but on-chain BTC receive/send behavior is still placeholder-based in important areas. The current implementation generates fake deposit addresses, does not reconcile on-chain deposits, and does not build/broadcast real Bitcoin transactions. The service also lacks endpoints for fee estimation, BOLT11 decoding. In addition, the wallet DB balance for Lightning is not periodically synchronized from LND.

Your task is to implement and/or correct the wallet service so that it supports real Bitcoin Core-backed on-chain wallet operations and better Lightning wallet observability, while preserving the project’s existing patterns:
- FastAPI + Pydantic
- SQLAlchemy async DB helpers
- structured JSON logging
- audit logging for state-changing operations
- metrics/business events
- standard error contract: {"error": {"code": "...", "message": "..."}}
- explicit commits after DB mutations
- tests in tests/

Important repository conventions to preserve:
- Use services/common/config.py for settings, never hardcode values.
- Use SQLAlchemy constructs, not raw SQL strings.
- Add audit events for financial state changes.
- Record business events for new wallet actions.
- Reuse existing auth and 2FA patterns already present in services/wallet.
- Keep backward-compatible routes where reasonable if legacy aliases already exist.

Implement these changes:

1. Generate a real BTC receive address
- Replace placeholder address generation.
- Derive a deterministic Taproot receive address from the user seed using BIP-86.
- Ensure the derivation path is consistent with the wallet’s configured network.
- Register or import the address into Bitcoin Core so the node can watch and detect deposits.
- Keep the endpoint behavior compatible with POST /wallet/onchain/address.
- Return the real derived address and type=taproot.

2. Detect incoming BTC deposits and update wallet balance
- Add synchronization with Bitcoin Core so incoming transactions to user deposit addresses are detected.
- Persist detected on-chain deposits into transactions with real txid values.
- Increase wallets.onchain_balance_sat when deposits are detected and confirmed according to a clear policy.
- Avoid double-crediting the same deposit.
- Add any schema changes needed to track derived addresses, scriptPubKeys, import status, confirmation state, and reconciliation metadata.
- Provide tests for deposit reconciliation logic.

3. Send BTC with a real on-chain transaction
- Replace synthetic txid withdrawal logic.
- Build, fund, sign, and broadcast a real Bitcoin transaction through Bitcoin Core RPC.
- Respect the fee_rate_sat_vb request field.
- Persist the real txid and correct fee.
- Deduct wallet balance only after successful broadcast according to a consistent accounting policy.
- Record the transaction row with real txid and status.
- Keep 2FA enforcement.
- Add tests for success, insufficient funds, RPC errors, and idempotency/double-submit safety if needed.

4. Add fee estimation endpoint
- Add a new endpoint that returns low/medium/high fee estimates in sat/vB.
- Use Bitcoin Core fee estimation RPCs.
- Define clear request/response schemas.
- Handle missing estimates safely with reasonable fallbacks and explicit error handling.
- Add tests.

5. Add BOLT11 decode endpoint
- Add an endpoint that accepts a Lightning invoice string and returns decoded metadata such as:
  - payment hash
  - amount
  - description
  - expiry
  - timestamp
  - destination/pubkey if available
- Prefer LND decoding if available; otherwise implement a safe decoder strategy already compatible with project dependencies.
- Add tests for valid and invalid invoices.

6. Synchronize Lightning balance from LND
- Add logic to periodically query LND channel/wallet balances and reflect them in wallets.lightning_balance_sat.
- Implement this safely for the current service architecture.
- If no background worker exists in this service, add a practical synchronization mechanism consistent with the codebase (for example: startup task, periodic async loop, or a sync-on-read helper with clear tradeoffs).
- Make sure balances update without breaking existing invoice/payment flows.
- Add tests.

7. Expose real tx hashes in transaction history
- Update transaction history responses so frontend clients can receive:
  - txHash for on-chain transactions
  - paymentHash for Lightning transactions
- Do not hide these fields anymore.
- Keep pagination behavior unchanged.
- Update schemas, serializers, and tests.

8. Preserve existing working behavior
Do not break:
- create Lightning invoice
- pay Lightning invoice
- consult Lightning invoice status
- JWT auth
- TOTP / 2FA
- wallet summary / portfolio summary / token balances / yield views

9. Documentation and tests
- Update any relevant schemas and inline endpoint documentation.
- Add or update tests in tests/test_wallet.py, tests/test_lightning.py, tests/test_key_manager.py, or other appropriate files.
- If schema changes are required, update services/common/db/metadata.py and add an Alembic migration.
- Summarize all changes made, list new endpoints, list migrations, and mention any assumptions or follow-up work needed.

Before coding, inspect the current implementation in:
- services/wallet/main.py
- services/wallet/db.py
- services/wallet/key_manager.py
- services/wallet/lnd_client.py
- services/wallet/schemas.py
- services/wallet/schemas_wallet.py
- services/wallet/schemas_lnd.py
- services/common/config.py
- services/common/db/metadata.py
- relevant tests

Then implement the changes directly in code.