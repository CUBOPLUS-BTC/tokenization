Implement Lightning balance synchronization in services/wallet so wallets.lightning_balance_sat reflects the real LND balance.

Current problem:
Lightning invoice creation, payment, and lookup work, but the DB wallet lightning balance is not updated from LND.

Requirements:
- Inspect services/wallet/lnd_client.py and existing wallet summary logic.
- Add support for reading LND balances relevant to the wallet’s user-facing Lightning balance.
- Update wallets.lightning_balance_sat periodically or through a practical sync mechanism compatible with the current service architecture.
- Make the implementation safe and explicit about what “Lightning balance” means:
  - channel balance
  - local balance
  - available balance
  - wallet balance
- Choose a clear definition and document it in code comments.

Implementation expectations:
- If there is no dedicated scheduler infrastructure, add a lightweight periodic sync loop or a sync-on-read helper with minimal side effects.
- Ensure the solution does not break existing Lightning invoice/payment flows.
- Update GET /wallet so the returned lightning balance is reasonably fresh.
- Add tests for:
  - successful sync from mocked LND
  - stale value update
  - LND unavailable behavior
  - wallet summary still works when sync fails gracefully

Also add business events and any useful logs.
At the end, summarize the chosen synchronization strategy and tradeoffs.