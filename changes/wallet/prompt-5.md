Add a BOLT11 decode endpoint to services/wallet.

Goal:
Allow frontend clients to submit a Lightning invoice string and receive decoded invoice metadata.

Requirements:
- Add a new authenticated endpoint in services/wallet.
- Input: a BOLT11 payment request string.
- Output should include as many of the following as are available:
  - payment_hash
  - amount_sat or amount_msat
  - description
  - description_hash if present
  - created_at / timestamp
  - expires_at or expiry seconds
  - destination / payee pubkey
  - network
  - route hints if reasonably available

Implementation expectations:
- Prefer using LND decoding capabilities if available in the current integration setup.
- Otherwise implement a safe BOLT11 decode path compatible with repo dependencies.
- Validate malformed invoices and return a proper error contract.
- Add tests for:
  - valid invoice with amount and description
  - valid invoice without explicit amount
  - invalid invoice
  - auth enforcement

At the end, summarize the endpoint path, schema, and whether decoding depends on LND or local parsing.