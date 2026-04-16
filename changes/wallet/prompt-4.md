Replace placeholder on-chain withdrawal logic in services/wallet with real Bitcoin transaction creation and broadcast via Bitcoin Core RPC.

Current problem:
POST /wallet/onchain/withdraw currently deducts DB balance and returns a synthetic txid, but it does not actually move funds.

Requirements:
- Inspect services/wallet/main.py, services/wallet/db.py, schemas, auth/2FA flow, and settings.
- Preserve JWT auth and mandatory 2FA behavior for on-chain withdrawals.
- Use Bitcoin Core RPC to build, fund, sign, and broadcast a real transaction.
- Respect the requested fee_rate_sat_vb value.
- Return a real txid, actual fee_sat, amount_sat, and status.
- Persist the withdrawal transaction with the real txid.
- Update wallet accounting consistently and safely.
- Handle insufficient funds, invalid address, RPC failures, signing failures, and broadcast failures.

Implementation expectations:
- Prefer a robust Bitcoin Core RPC flow such as:
  - createpsbt / walletcreatefundedpsbt
  - walletprocesspsbt
  - finalizepsbt
  - sendrawtransaction
  or an equivalent safe flow supported by the configured node/wallet mode.
- Avoid deducting wallet balance before successful transaction creation/broadcast unless your accounting model explicitly tracks reserved funds; document the chosen approach.
- Keep error responses aligned with the project contract.
- Add metrics, audit logging, and structured logs.
- Protect against accidental duplicate submissions where feasible.

Tests to add/update:
- successful withdrawal
- insufficient funds
- invalid 2FA
- Bitcoin Core RPC error
- real txid persistence
- fee propagation
- transaction row correctness

If schema changes are needed, create the migration and update metadata.
At the end, summarize the implementation and any operational assumptions about Bitcoin Core wallet/descriptors.