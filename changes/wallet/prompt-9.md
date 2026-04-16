Perform a test-focused hardening pass for the recent services/wallet changes related to real on-chain Bitcoin support and Lightning balance synchronization.

Your task:
- Add or update unit tests and integration-style tests using the repository’s current testing conventions.
- Mock async DB access and mocked Bitcoin Core/LND clients instead of requiring real infrastructure.
- Cover happy paths and failure paths.

At minimum include tests for:
1. real BIP-86 address derivation path behavior
2. Bitcoin Core address import/registration
3. deposit reconciliation without double-credit
4. pending vs confirmed deposits
5. real withdrawal flow with txid persistence
6. fee estimation endpoint
7. BOLT11 decode endpoint
8. QR endpoint returns PNG
9. Lightning balance synchronization updates wallets.lightning_balance_sat
10. transaction history now includes txHash/paymentHash

Constraints:
- Follow existing fixture style and patch settings safely.
- Preserve current working Lightning tests.
- Keep tests readable and narrow in scope.

At the end, summarize test coverage added and any uncovered edge cases.