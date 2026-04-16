Implement real on-chain deposit address generation in services/wallet.

Current problem:
POST /wallet/onchain/address returns a random placeholder address-like string. It must instead derive a real Taproot address from the user seed using BIP-86 and make Bitcoin Core aware of the address for deposit detection.

Requirements:
- Inspect services/wallet/main.py, services/wallet/db.py, services/wallet/key_manager.py, services/common/custody.py, and services/common/config.py.
- Reuse existing wallet seed custody/unsealing mechanisms.
- Derive deterministic BIP-86 receive addresses from the user seed.
- Respect the configured network (regtest/testnet/signet/mainnet).
- Keep the endpoint POST /wallet/onchain/address.
- Return:
  {
    "address": "<real taproot address>",
    "type": "taproot"
  }

Implementation expectations:
- Add address derivation support if key_manager.py is incomplete.
- Add persistence for derived addresses so the system can later reconcile deposits.
- Store enough metadata to avoid deriving/importing the same address ambiguously.
- Import/register the address or descriptor into Bitcoin Core using RPC so incoming UTXOs can be monitored.
- Follow project conventions for logging, metrics, and errors.
- Add/update tests for:
  - deterministic derivation
  - network-correct address prefix
  - RPC import call
  - authenticated access
  - repeated calls behavior (whether next address or current receive address is returned; document the chosen policy)

If schema changes are required, update services/common/db/metadata.py and add an Alembic migration.
At the end, summarize files changed and any assumptions.