Update wallet transaction history responses to expose real transaction identifiers needed by the frontend.

Current problem:
The transactions table already has txid and Lightning payment hash fields, but the API hides them in responses.

Requirements:
- Update transaction history schemas and serialization so frontend clients receive:
  - txHash for on-chain transactions
  - paymentHash for Lightning transactions
- Preserve pagination and existing filtering behavior.
- Do not remove existing fields unless absolutely necessary.
- Keep nullability appropriate for transaction types that do not use a given hash.

Expected response shape example:
{
  "transactions": [
    {
      "id": "uuid",
      "type": "withdrawal",
      "amount_sat": 90000,
      "direction": "out",
      "status": "confirmed",
      "description": "On-chain withdrawal",
      "created_at": "2026-04-15T12:00:00Z",
      "txHash": "real_txid_here",
      "paymentHash": null
    }
  ],
  "next_cursor": "uuid_or_null"
}

Tasks:
- Inspect current schemas and DB row-to-response mapping.
- Update the relevant Pydantic models.
- Update GET /wallet/transactions and any legacy alias responses.
- Add tests covering:
  - on-chain deposit rows
  - on-chain withdrawal rows
  - Lightning receive rows
  - Lightning send rows

At the end, summarize all response contract changes.