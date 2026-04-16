# Wallet Service Integration Guide

This document describes the wallet service as it is currently implemented in `services/wallet`, with explicit notes where the broader platform specification describes intended behavior that has not yet been fully realized.

## 1. Service Overview

The wallet service is the platform boundary for Bitcoin-denominated user funds and wallet-facing account operations. It is responsible for presenting each user's wallet summary, handling on-chain and Lightning actions, exposing custody posture, starting hosted fiat on-ramp sessions, and surfacing token-holding yield data that is derived from shared trading and tokenization data.

### Purpose of the service

- Maintain a per-user wallet record and transaction history.
- Expose user-facing wallet APIs for balances, transaction history, addresses, withdrawals, invoices, payments, custody metadata, and fiat on-ramp handoff.
- Integrate with Lightning Network Daemon (LND) for invoice creation, invoice lookup, and payment execution.
- Aggregate token holdings and yield information from shared tables populated by tokenization and marketplace flows.
- Enforce wallet-specific security controls such as write-rate limiting, 2FA on sensitive operations, audit logging, and alerts.

### Main responsibilities

- Wallet lifecycle: create a wallet row lazily and persist custody-wrapped seed material.
- Portfolio summary: combine on-chain balance, Lightning balance, token balances, and accrued yield into a single response.
- Yield reporting: accrue pending full-day yield snapshots and return both totals and detailed accrual rows.
- On-chain operations: generate a deposit address and record on-chain withdrawals.
- Lightning operations: create invoices, pay invoices, and look up invoice status.
- Fiat on-ramp: list hosted providers and create a redirect session for external BTC purchase flows.
- Observability and controls: health, readiness, metrics, audit events, business events, structured logs, and alerts.

### Business/domain role within the platform

Within the broader tokenization platform, the wallet service is the user's monetary control plane. Other services may create assets, match trades, or manage identity, but the wallet service is where those activities become balances, transaction history, custody posture, and payment actions that a frontend can actually present to a user.

### Why this service exists separately from the others

- Custody and payment flows are security-sensitive and require tighter controls than general marketplace or content features.
- LND, wallet encryption, 2FA enforcement, and audit requirements introduce operational dependencies that do not belong in auth, tokenization, or education.
- The service isolates Bitcoin and Lightning concerns from identity, token issuance, and order matching so that wallet changes can evolve without coupling directly to every other domain.
- Separate rate limiting, alerting, and observability make sense because wallet operations have higher financial risk than ordinary reads and writes.

### Current implementation status

| Area | Currently implemented | Intended platform role |
| --- | --- | --- |
| Wallet summary | Returns balances, token valuations, and accrued yield from shared tables | Full user portfolio view |
| Yield accrual | Triggered on `GET /wallet` and `GET /wallet/yield/summary` | Ongoing yield accounting for token holders |
| On-chain receive | Generates a random network-prefixed address-like string | Deterministic user deposit address generation |
| On-chain withdraw | Deducts DB balance and records a synthetic txid | Real Bitcoin transaction creation and confirmation tracking |
| Lightning | Uses LND gRPC for invoice creation, payment, and invoice lookup | Real Lightning integration |
| Fiat on-ramp | Lists providers and returns a hosted checkout handoff URL | Full fiat-to-BTC delivery into the user's wallet |
| Custody | Stores a custody envelope and exposes custody posture | Secure custody abstraction with software or HSM backing |
| Taproot Assets | Only readiness checks are wired today | Future wallet-side asset sync or transfer handling |

## 2. Service Relationships

The wallet service relies on both other platform services and shared infrastructure modules. In practice, many of these relationships are implemented through shared database tables and shared `services/common` modules rather than HTTP calls between services.

### Relationships with other platform services

| Service | Purpose of interaction | Interaction type | Current implementation |
| --- | --- | --- | --- |
| `services/auth` | Access-token trust, user lookup, 2FA secret lookup, KYC status lookup | Authentication dependency and shared database access | Wallet validates JWTs locally using the shared `JWT_SECRET`, reads `users`, and imports `auth.kyc_db` to read `kyc_verifications` |
| `services/tokenization` | Token metadata and asset metadata for wallet valuation and yield | Shared database access | Wallet reads `tokens` and `assets` to label token balances and calculate reference pricing/yield |
| `services/marketplace` | Latest settled trade pricing for token valuation; conceptual future escrow transaction types | Shared database access | Wallet reads `trades` to value holdings by latest settled price when available |
| `services/gateway` | Public entrypoint routing | Direct API exposure through reverse proxy | Nginx routes `/v1/wallet/*` to the wallet service on port `8001` |
| `services/admin` | No direct wallet API integration today | None currently | Admin may read wallet-related shared tables elsewhere, but wallet does not call admin |
| `services/education` | No direct runtime dependency today | None currently | No wallet-side code depends on education |
| `services/nostr` | No direct runtime dependency today | None currently | Wallet does not publish or consume Nostr events directly |

### Dependencies on `services/common`

| Shared module | Purpose | Interaction type | Current implementation |
| --- | --- | --- | --- |
| `common.config` | Service configuration and secret resolution | Infrastructure/shared module dependency | Provides all runtime settings, including DB, LND, Bitcoin, custody, logging, and profile-specific validation |
| `common.security` | Request IDs and rate limiting | Infrastructure/shared module dependency | Installs write-rate limiting for all writes and stricter limits for `/lightning/payments`, `/wallet/onchain/withdraw`, and `/onchain/withdraw` |
| `common.logging` | Structured logging with redaction | Infrastructure/shared module dependency | Configures service-level JSON logging and sensitive-data redaction |
| `common.metrics` | Metrics endpoint and business-event metrics | Infrastructure/shared module dependency | Mounts `GET /metrics` and records events such as `wallet_payment` and `wallet_onchain_withdrawal` |
| `common.alerting` | Operational alerts | Infrastructure/shared module dependency | Lightning payment failures emit CRITICAL alerts through the configured dispatcher |
| `common.readiness` | Dependency readiness checks | Infrastructure/shared module dependency | `GET /ready` checks PostgreSQL, Redis, Bitcoin Core, LND, and tapd |
| `common.audit` | Audit trail recording | Infrastructure/shared module dependency | Withdrawal, Lightning payment, and fiat on-ramp session creation write audit logs |
| `common.custody` | Seed generation, custody envelope format, backend description | Infrastructure/shared module dependency | New wallets store custody-sealed seed material in `wallets.encrypted_seed`; custody metadata is exposed via `GET /wallet/custody` |
| `common.onramp` | Provider catalog and handoff session generation | Infrastructure/shared module dependency | Provider discovery and session creation are delegated here |
| `common.incentives` | Yield accrual and yield summaries | Infrastructure/shared module dependency | Wallet summary endpoints call into shared yield logic before responding |
| `common.db.metadata` | Canonical SQLAlchemy table definitions | Infrastructure/shared module dependency | Wallet DB helpers import wallet-owned and shared tables from here |

### External and infrastructure relationships

| Dependency | Purpose | Interaction type | Current implementation |
| --- | --- | --- | --- |
| PostgreSQL | Source of truth for wallet and shared platform data | Direct database dependency | Used heavily for wallet rows, transaction history, token balances, yield, users, KYC, and pricing joins |
| LND | Lightning invoice, pay, and lookup | Direct gRPC integration | `services/wallet/lnd_client.py` establishes a TLS + macaroon gRPC channel |
| Bitcoin Core | Intended on-chain dependency | Infrastructure dependency and readiness check | Checked in readiness, but not used for actual address derivation or withdrawal broadcast today |
| tapd | Intended Taproot Assets dependency | Infrastructure dependency and readiness check | Checked in readiness only; no wallet-side RPC calls currently exist |
| Redis | Platform dependency | Infrastructure dependency and readiness check | Checked in readiness; wallet does not currently publish or consume Redis events |
| Hosted on-ramp providers | External fiat checkout flows | External redirect integration | Session creation returns provider-hosted checkout URLs |

### Notes on absent direct service-to-service calls

- The wallet service does not currently call other platform services over HTTP.
- Most cross-domain behavior is achieved through shared tables and shared helper modules.
- There is no wallet-specific event publishing or event consumption path in the current code.

## 3. Database Documentation

All table definitions live in `services/common/db/metadata.py`, and migrations are platform-wide Alembic revisions. The ownership labels below are operational ownership labels, not isolated schema ownership.

**Assumption:** `wallets` and `transactions` are operationally owned by the wallet service, while several other tables are shared and primarily owned by other services.

### Wallet-owned and wallet-driven tables

| Table | Ownership | Purpose in wallet service | Important fields and constraints | Relationships |
| --- | --- | --- | --- | --- |
| `wallets` | Wallet-owned | Primary wallet record for each user | `id` UUID PK; `user_id` UUID unique FK to `users`; `onchain_balance_sat >= 0`; `lightning_balance_sat >= 0`; `encrypted_seed` required; `derivation_path` required | One-to-one with `users`; referenced by `transactions` |
| `transactions` | Wallet-owned | Immutable wallet transaction ledger returned by history endpoints | `type` constrained to `deposit`, `withdrawal`, `ln_send`, `ln_receive`, `escrow_lock`, `escrow_release`, `fee`; `direction` constrained to `in` or `out`; `status` constrained to `pending`, `confirmed`, `failed`; `amount_sat > 0` | Many-to-one to `wallets` via `wallet_id` |
| `yield_accruals` | Shared, wallet-driven | Snapshot ledger for token yield summaries | `annual_rate_pct > 0`; `quantity_held > 0`; `reference_price_sat > 0`; `amount_sat > 0`; `accrued_to > accrued_from` | FKs to `users` and `tokens`; populated by shared incentives logic invoked by wallet endpoints |

### Shared tables read by the wallet service

| Table | Primary owner | Purpose in wallet service | Important fields and constraints | Relationships |
| --- | --- | --- | --- | --- |
| `users` | Auth-owned | Token validity checks, role capture for audit events, and TOTP verification | `totp_secret`, `role`, `deleted_at` are the fields wallet uses most often | Referenced by `wallets`, `yield_accruals`, and `kyc_verifications` |
| `kyc_verifications` | Auth-owned | Eligibility check for fiat on-ramp session creation | `status` constrained to `pending`, `verified`, `rejected`, `expired`; unique `user_id` | FK to `users`; optionally reviewed by another user |
| `token_balances` | Shared between tokenization, marketplace, and wallet reporting | Denormalized token holdings shown in wallet summary | Unique `(user_id, token_id)`; `balance >= 0` | FK to `users` and `tokens` |
| `tokens` | Tokenization-owned | Asset-level metadata and fallback unit price | `asset_id`, `taproot_asset_id`, `unit_price_sat`, `minted_at` | FK to `assets`; referenced by `token_balances` and `yield_accruals` |
| `assets` | Tokenization-owned | Asset naming and projected ROI for yield calculations | `name`, `projected_roi`, `status`, category and score constraints | FK from `tokens` |
| `trades` | Marketplace-owned | Latest settled trade pricing for wallet valuation | `status` constrained to `pending`, `escrowed`, `settled`, `disputed`; wallet only uses latest settled price | FK to `orders` and `tokens` |

### Table details and inferred usage

#### `wallets`

- Created lazily by `get_or_create_wallet()` in `services/wallet/db.py`.
- New wallets start with `onchain_balance_sat = 0` and `lightning_balance_sat = 0`.
- `encrypted_seed` stores the output of `build_wallet_custody(settings).seal_seed(seed)`.
- Although the column type is `LargeBinary`, the current custody backend stores a JSON envelope encoded as bytes, including backend, cipher, nonce, ciphertext, key reference, fingerprint, and exportability metadata.
- `derivation_path` comes from `common.custody` and uses BIP-86 coin type `0` for mainnet and `1` for testnet/signet/regtest.

#### `transactions`

- Used for on-chain withdrawal records, Lightning receive records, and Lightning send records.
- Response schemas intentionally omit `txid` and `ln_payment_hash`, even though those fields exist in the database.
- Transaction types `escrow_lock`, `escrow_release`, and `fee` are supported by schema and constraints, but the wallet service does not currently create those rows itself.

#### `token_balances`

- The wallet service reads balances only; it does not mutate this table directly.
- Latest unit price is computed with `COALESCE(latest_settled_trade.price_sat, tokens.unit_price_sat)`.
- **Assumption:** Marketplace settlement and tokenization minting are the main writers for this table.

#### `yield_accruals`

- Rows are inserted on-demand when the user calls `GET /wallet` or `GET /wallet/yield/summary`.
- Accrual start time is the latest of prior accrual end, token balance update time, or token mint time.
- Yield is only recorded for full elapsed days and only when `assets.projected_roi > 0`.
- **Important implementation note:** there is no background scheduler in this service. Accrual is request-driven.

#### `users` and `kyc_verifications`

- Wallet uses `users.deleted_at` to invalidate access tokens in routes that use `_get_current_principal`.
- Wallet uses `users.totp_secret` to verify withdrawal 2FA and conditional Lightning-payment 2FA.
- Wallet uses `kyc_verifications.status` to decide whether an on-ramp session may be created.

#### `tokens`, `assets`, and `trades`

- These tables provide the asset names, fallback token prices, projected ROI, and latest settled prices required to present a useful wallet summary.
- The wallet service does not currently connect to tapd for asset-state reconciliation; balances are DB-derived only.

### Relevant Alembic migrations

| Migration | Why it matters to wallet |
| --- | --- |
| `20260412_1200_0001_initial_core_schema.py` | Creates `users` and `wallets` |
| `20260413_1330_0002_remaining_schema_tables.py` | Creates `tokens`, `token_balances`, `transactions`, `orders`, and other supporting shared tables |
| `20260413_1800_0003_align_domain_schema_constraints.py` | Adds the non-negative balance constraint to `token_balances` |
| `20260413_1830_0004_normalize_check_constraint_names.py` | Renames check constraints for `wallets`, `transactions`, and `token_balances` to normalized names |
| `20260415_1030_0009_add_kyc_verifications.py` | Adds `kyc_verifications`, which the wallet service uses for hosted on-ramp eligibility |
| `20260415_1200_0010_add_referrals_yield_and_advanced_orders.py` | Adds `yield_accruals`, enabling wallet yield summaries |
| `20260415_1600_0011_normalize_late_check_constraint_names.py` | Normalizes the late-added constraint names on `kyc_verifications` and `yield_accruals` |

### Assumptions and limitations

- Table ownership is inferred from how the code uses the shared schema; the database itself is shared across services.
- There is no dedicated wallet migration namespace; wallet-related changes are mixed into platform-wide revisions.
- Exact writer services for `token_balances` and `trades` are inferred from the codebase and tests.

## 4. API Endpoints

### Path conventions

- The paths below are the wallet service's internal route paths.
- Through the gateway, these routes are normally reachable under `/v1/wallet/<internal-path-without-leading-slash>`.
- This means internal `GET /wallet` becomes gateway `GET /v1/wallet/wallet`.
- Operational gateway shortcuts also exist for `GET /health/wallet`, `GET /ready/wallet`, and `GET /metrics/wallet`.
- Hidden compatibility aliases exist for some on-chain and transaction-history routes.

### Error response conventions

- Custom wallet `ContractError` responses use the platform shape:

```json
{
  "error": {
    "code": "string_slug",
    "message": "Human-readable description"
  }
}
```

- Some handlers still raise `HTTPException` directly and therefore return FastAPI's default shape:

```json
{
  "detail": "Human-readable description"
}
```

- Frontend consumers must be prepared to parse both formats.

### 4.1 Operational endpoints

#### `GET /health`

- Purpose: liveness probe.
- Authentication: none.
- Query parameters: none.
- Request body: none.
- Response schema:

```json
{
  "status": "ok",
  "service": "wallet",
  "env_profile": "local"
}
```

- Possible error responses: none expected in normal operation.

#### `GET /ready`

- Purpose: readiness probe that reports dependency state.
- Authentication: none.
- Query parameters: none.
- Request body: none.
- Response schema:

```json
{
  "status": "ready|not_ready",
  "service": "wallet",
  "env_profile": "local|staging|beta|production",
  "dependencies": {
    "postgres": {"ok": true, "target": "host:port", "error": null},
    "redis": {"ok": true, "target": "host:port", "error": null},
    "bitcoin": {"ok": true, "target": "host:port", "error": null},
    "lnd": {"ok": true, "target": "host:port", "error": null},
    "tapd": {"ok": true, "target": "host:port", "error": null}
  }
}
```

- Possible error responses:
  - `503` when one or more dependencies are not ready; the body still uses the readiness payload above.

#### `GET /metrics`

- Purpose: expose Prometheus-style metrics and a JSON metrics snapshot.
- Authentication: none.
- Query parameters:
  - `format=json` returns a JSON snapshot instead of Prometheus text.
- Request body: none.
- Response schema:
  - Default: Prometheus plaintext.
  - JSON mode: metric snapshot plus `service`, `env_profile`, `bitcoin_network`, and embedded readiness payload.
- Possible error responses: none expected in normal operation.

### 4.2 Wallet summary and yield endpoints

#### `GET /wallet`

- Purpose: return the aggregate wallet summary for the authenticated user.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Query parameters: none.
- Request body: none.
- Response schema:

```json
{
  "wallet": {
    "id": "uuid",
    "onchain_balance_sat": 0,
    "lightning_balance_sat": 0,
    "token_balances": [
      {
        "token_id": "uuid",
        "asset_name": "Harbor Plaza",
        "symbol": null,
        "balance": 5,
        "unit_price_sat": 125000,
        "accrued_yield_sat": 1200
      }
    ],
    "total_yield_earned_sat": 1200,
    "total_value_sat": 626200
  }
}
```

- Possible error responses:
  - `401` or `403` with `{"detail": ...}` when the JWT is missing or invalid.
  - `404` with `{"detail": "Wallet not found for user"}` when no wallet row exists.
  - `422` with the platform `error` object when request validation fails.

**Important implementation note:** this endpoint does not lazily create a wallet. It fails with `404` if the wallet record does not already exist.

#### `GET /wallet/yield/summary`

- Purpose: return detailed yield totals and underlying accrual rows.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Query parameters: none.
- Request body: none.
- Response schema:

```json
{
  "yield_summary": {
    "total_yield_earned_sat": 7000,
    "by_token": [
      {
        "token_id": "uuid",
        "asset_name": "Deep Ocean Blue",
        "total_yield_sat": 7000
      }
    ],
    "accruals": [
      {
        "id": "uuid",
        "token_id": "uuid",
        "asset_name": "Deep Ocean Blue",
        "amount_sat": 7000,
        "quantity_held": 100,
        "reference_price_sat": 2500,
        "annual_rate_pct": 8.5,
        "accrued_from": "2026-04-01T00:00:00Z",
        "accrued_to": "2026-04-02T00:00:00Z",
        "created_at": "2026-04-02T00:00:00Z"
      }
    ]
  }
}
```

- Possible error responses:
  - `401` or `403` with `{"detail": ...}` when the JWT is missing or invalid.
  - `404` with `{"detail": "Wallet not found for user"}` when no wallet row exists.
  - `422` with the platform `error` object when request validation fails.

#### `GET /wallet/transactions`

- Purpose: return paginated wallet transaction history.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Query parameters:
  - `cursor`: optional transaction UUID to continue pagination.
  - `limit`: optional integer from `1` to `100`, default `20`.
  - `type`: optional filter; one of `deposit`, `withdrawal`, `ln_send`, `ln_receive`, `escrow_lock`, `escrow_release`, `fee`.
- Request body: none.
- Response schema:

```json
{
  "transactions": [
    {
      "id": "uuid",
      "type": "withdrawal",
      "amount_sat": 90000,
      "direction": "out",
      "status": "confirmed",
      "description": "On-chain withdrawal",
      "created_at": "2026-04-15T12:00:00Z"
    }
  ],
  "next_cursor": "uuid_or_null"
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` for missing credentials.
  - `401` with platform `error.code = invalid_token` for invalid or expired access tokens.
  - `400` with platform `error.code = invalid_cursor` for malformed or mismatched cursors.
  - `422` with platform `error.code = validation_error` for invalid query values.

**Legacy alias:** `GET /transactions` behaves the same and is intentionally hidden from the OpenAPI schema.

### 4.3 Custody and fiat on-ramp endpoints

#### `GET /wallet/custody`

- Purpose: return custody posture for the authenticated user's wallet.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Query parameters: none.
- Request body: none.
- Response schema:

```json
{
  "configured_backend": "software|hsm",
  "wallet_backend": "software|hsm",
  "signer_backend": "software|hsm",
  "state": "ready|degraded",
  "key_reference": "string_or_null",
  "signer_key_reference": "string_or_null",
  "derivation_path": "m/86'/1'/0'",
  "seed_exportable": true,
  "withdraw_requires_2fa": true,
  "server_compromise_impact": "text",
  "disclaimers": ["text"]
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` for missing credentials.
  - `401` with platform `error.code = invalid_token` for invalid tokens or deleted users.

**Important implementation note:** unlike `GET /wallet`, this endpoint lazily creates the wallet if it does not already exist.

#### `GET /wallet/fiat/onramp/providers`

- Purpose: list hosted fiat providers and compliance notices.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Query parameters: none.
- Request body: none.
- Response schema:

```json
{
  "providers": [
    {
      "provider_id": "bank-bridge",
      "display_name": "Bank Bridge",
      "state": "ready",
      "supported_fiat_currencies": ["USD", "EUR", "GBP"],
      "supported_countries": ["US", "GB", "DE", "FR", "NL", "ES"],
      "payment_methods": ["bank_transfer"],
      "min_fiat_amount": "25.00",
      "max_fiat_amount": "5000.00",
      "requires_kyc": true,
      "disclaimer": "text",
      "external_handoff_url": "https://bank-bridge.partner.example/checkout"
    }
  ],
  "compliance_notices": ["text"]
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` for missing credentials.
  - `401` with platform `error.code = invalid_token` for invalid tokens or deleted users.

**Important implementation note:** provider `state` is currently returned as `ready` regardless of KYC status. Frontends should use `requires_kyc` and the user's known KYC status, not `state` alone, when deciding whether to show an immediate redirect option.

#### `POST /wallet/fiat/onramp/session`

- Purpose: create a provider-hosted fiat on-ramp redirect session.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Query parameters: none.
- Request body schema:

```json
{
  "provider_id": "bank-bridge",
  "fiat_currency": "USD",
  "fiat_amount": "150.00",
  "country_code": "US",
  "return_url": "https://app.example.com/wallet/fiat/complete",
  "cancel_url": "https://app.example.com/wallet/fiat/cancel"
}
```

- Request validation rules:
  - `provider_id` is normalized to lowercase.
  - `fiat_currency` is normalized to uppercase and must be exactly 3 characters.
  - `country_code` is normalized to uppercase and must be exactly 2 characters.
  - `return_url` and `cancel_url` must be HTTPS URLs or localhost callback URLs.
- Response schema:

```json
{
  "session_id": "uuid",
  "provider_id": "bank-bridge",
  "state": "pending_redirect",
  "handoff_url": "https://bank-bridge.partner.example/checkout?...",
  "deposit_address": "bcrt1p...",
  "destination_wallet_id": "uuid",
  "expires_at": "2026-04-15T12:20:00Z",
  "disclaimer": "text",
  "compliance_action": "review_terms"
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` for missing credentials.
  - `401` with platform `error.code = invalid_token` for invalid tokens or deleted users.
  - `404` with platform `error.code = unsupported_onramp_provider`.
  - `409` with platform `error.code = provider_kyc_required`.
  - `422` with platform codes such as `unsupported_fiat_currency`, `unsupported_country`, `fiat_amount_out_of_range`, `invalid_return_url`, or `invalid_cancel_url`.

**Current implementation vs intended behavior:** the service does create a hosted handoff session, but it does not yet implement the later deposit reconciliation that would credit the purchased BTC back into `wallets.onchain_balance_sat`.

### 4.4 Lightning endpoints

#### `POST /lightning/invoices`

- Purpose: create a Lightning invoice using LND.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Query parameters: none.
- Request body schema:

```json
{
  "amount_sats": 1000,
  "memo": "Fund wallet"
}
```

- Response schema:

```json
{
  "payment_request": "lnbc1...",
  "payment_hash": "hex_hash",
  "r_hash": "hex_hash",
  "amount_sats": 1000,
  "memo": "Fund wallet",
  "status": "OPEN",
  "settled_at": null,
  "created_at": "2026-04-15T12:00:00Z"
}
```

- Possible error responses:
  - `401` or `403` with `{"detail": ...}` when the JWT is missing or invalid.
  - `503` with `{"detail": "Lightning service unavailable"}` when LND is unavailable.
  - `500` with `{"detail": "Internal server error"}` for unexpected failures.
  - `422` with the platform `error` object for validation failures.

**Important implementation note:** if the user does not already have a wallet row, the invoice is still created at LND level, but no wallet transaction row is recorded.

#### `GET /lightning/invoices/{r_hash}`

- Purpose: look up a Lightning invoice by payment hash.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Path parameters:
  - `r_hash`: hex-encoded payment hash.
- Request body: none.
- Response schema: same `Invoice` schema as invoice creation, with `status` mapped from LND state and `settled_at` populated when settled.
- Possible error responses:
  - `401` or `403` with `{"detail": ...}` when the JWT is missing or invalid.
  - `404` with `{"detail": "Invoice not found"}`.
  - `503` with `{"detail": "Lightning service unavailable"}`.
  - `500` with `{"detail": "Internal server error"}`.

**Security note:** the current handler authenticates the caller but does not scope invoice lookup to invoices created by that user.

#### `POST /lightning/payments`

- Purpose: pay a Lightning invoice through LND.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Conditional 2FA requirement:
  - If the user has a `totp_secret`, `X-2FA-Code` is required.
  - If the user does not have 2FA enabled, the payment may proceed without the header.
- Query parameters: none.
- Request body schema:

```json
{
  "payment_request": "lnbc1..."
}
```

- Response schema:

```json
{
  "payment_hash": "hex_hash",
  "payment_preimage": "hex_preimage_or_null",
  "status": "SUCCEEDED|FAILED|IN_FLIGHT",
  "fee_sats": 0,
  "failure_reason": null,
  "created_at": "2026-04-15T12:00:00Z"
}
```

- Possible error responses:
  - `401` or `403` with `{"detail": ...}` for missing or invalid auth/2FA.
  - `404` with `{"detail": "Wallet not found"}` when there is no wallet row.
  - `503` with `{"detail": "Lightning service unavailable"}` when LND is unavailable.
  - `500` with `{"detail": "Internal server error"}` for unexpected failures.
  - `422` with the platform `error` object for validation failures.

**Important implementation note:** an LND payment failure does not raise a non-2xx HTTP error. The endpoint still returns `200` with `status = FAILED` and `failure_reason`, while also recording a failed transaction row and firing a CRITICAL alert.

### 4.5 On-chain endpoints

#### `POST /wallet/onchain/address`

- Purpose: create a new on-chain deposit address.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Query parameters: none.
- Request body: none.
- Response schema:

```json
{
  "address": "bcrt1p...",
  "type": "taproot"
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` for missing credentials.
  - `401` with platform `error.code = invalid_token` for invalid tokens or deleted users.

**Legacy alias:** `POST /onchain/address` behaves identically and is hidden from the OpenAPI schema.

**Current implementation vs intended behavior:** the address is randomly generated with the right network prefix and character set, but it is not derived from the stored seed or registered with Bitcoin Core/LND.

#### `POST /wallet/onchain/withdraw`

- Purpose: submit an on-chain withdrawal.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user with 2FA enabled.
- Required headers:
  - `X-2FA-Code`: six-digit TOTP code.
- Query parameters: none.
- Request body schema:

```json
{
  "address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
  "amount_sat": 100000,
  "fee_rate_sat_vb": 5
}
```

- Request validation rules:
  - `address` must start with `bc1`, `tb1`, or `bcrt1`.
  - `amount_sat >= 1`.
  - `fee_rate_sat_vb` must be between `1` and `1000`.
- Response schema:

```json
{
  "txid": "64_hex_chars",
  "amount_sat": 100000,
  "fee_sat": 705,
  "status": "pending"
}
```

- Possible error responses:
  - `400` with platform `error.code = two_factor_required` when `X-2FA-Code` is missing.
  - `401` with platform `error.code = invalid_token` for invalid access tokens.
  - `401` with platform `error.code = invalid_2fa_code` for invalid TOTP codes.
  - `403` with platform `error.code = two_factor_not_enabled` when the user has not enabled 2FA.
  - `409` with platform `error.code = insufficient_funds` when balance is too low.
  - `422` with platform `error.code = validation_error` for invalid request bodies.

**Legacy alias:** `POST /onchain/withdraw` behaves identically and is hidden from the OpenAPI schema.

**Current implementation vs intended behavior:** the service creates a synthetic txid and deducts database balance, but it does not broadcast a real Bitcoin transaction.

### 4.6 Spec-to-implementation differences that affect integrations

The public API spec in `specs/api-contracts.md` does not fully match the current wallet implementation.

| Area | Public spec | Current implementation |
| --- | --- | --- |
| Lightning create route | `POST /wallet/lightning/invoice` | `POST /lightning/invoices` |
| Lightning pay route | `POST /wallet/lightning/pay` | `POST /lightning/payments` |
| Lightning invoice create body | `amount_sat`, `description` | `amount_sats`, `memo` |
| Lightning invoice create status | `201` in spec | `200` in code |
| On-ramp session expiry examples | Examples imply a generic future expiration window | Code uses `now + 20 minutes` |

Until the spec and implementation are reconciled, integrators should follow the live routes and payloads implemented in `services/wallet/main.py` and `services/wallet/schemas_lnd.py`.

## 5. How to Use the Endpoints

### Prerequisites

- Obtain an access token from the auth service.
- Send it as `Authorization: Bearer <access-token>`.
- Enable 2FA before attempting on-chain withdrawals, and be prepared to send `X-2FA-Code` for Lightning payments when the account has 2FA enabled.
- Expect direct-service examples below to use `http://localhost:8001` for clarity.
- Frontend applications usually go through the gateway instead.

### Example: fetch wallet summary

```bash
curl -X GET "http://localhost:8001/wallet" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Example response:

```json
{
  "wallet": {
    "id": "5d1a9f6d-40f8-4dbf-b4ff-c4eb0ec6a409",
    "onchain_balance_sat": 500000,
    "lightning_balance_sat": 150000,
    "token_balances": [
      {
        "token_id": "7d0a82d3-a5fc-4b51-aa2e-9122d7679a78",
        "asset_name": "Deep Ocean Blue",
        "symbol": null,
        "balance": 100,
        "unit_price_sat": 2500,
        "accrued_yield_sat": 12000
      }
    ],
    "total_yield_earned_sat": 12000,
    "total_value_sat": 912000
  }
}
```

### Example: fetch wallet summary from a frontend through the gateway

```js
const response = await fetch('/v1/wallet/wallet', {
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
});

if (!response.ok) {
  const payload = await response.json();
  throw payload.error ?? payload.detail;
}

const { wallet } = await response.json();
```

### Example: generate an on-chain deposit address

```bash
curl -X POST "http://localhost:8001/wallet/onchain/address" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Example response:

```json
{
  "address": "bcrt1p0n2s9x...",
  "type": "taproot"
}
```

**Workflow note:** today this address is a placeholder string, not a deterministic address derived from the wallet seed. Do not treat it as proof of completed deposit monitoring.

### Example: submit an on-chain withdrawal

```bash
curl -X POST "http://localhost:8001/wallet/onchain/withdraw" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "X-2FA-Code: ${TOTP_CODE}" \
  -H "Content-Type: application/json" \
  -d '{
    "address": "bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh",
    "amount_sat": 100000,
    "fee_rate_sat_vb": 5
  }'
```

Example response:

```json
{
  "txid": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "amount_sat": 100000,
  "fee_sat": 705,
  "status": "pending"
}
```

### Example: create and poll a Lightning invoice

Create the invoice:

```bash
curl -X POST "http://localhost:8001/lightning/invoices" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "amount_sats": 50000,
    "memo": "Fund wallet"
  }'
```

Example response:

```json
{
  "payment_request": "lnbc500u1p...",
  "payment_hash": "010203",
  "r_hash": "010203",
  "amount_sats": 50000,
  "memo": "Fund wallet",
  "status": "OPEN",
  "settled_at": null,
  "created_at": "2026-04-15T12:00:00Z"
}
```

Poll invoice status:

```bash
curl -X GET "http://localhost:8001/lightning/invoices/010203" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

### Example: pay a Lightning invoice

```bash
curl -X POST "http://localhost:8001/lightning/payments" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "X-2FA-Code: ${TOTP_CODE}" \
  -H "Content-Type: application/json" \
  -d '{
    "payment_request": "lnbc500u1p..."
  }'
```

Example success response:

```json
{
  "payment_hash": "040506",
  "payment_preimage": "070809",
  "status": "SUCCEEDED",
  "fee_sats": 50,
  "failure_reason": null,
  "created_at": "2026-04-15T12:00:00Z"
}
```

Example failed-payment response that still returns HTTP `200`:

```json
{
  "payment_hash": "040506",
  "payment_preimage": null,
  "status": "FAILED",
  "fee_sats": 0,
  "failure_reason": "route not found",
  "created_at": "2026-04-15T12:00:00Z"
}
```

### Example: launch a fiat on-ramp session

List providers first:

```bash
curl -X GET "http://localhost:8001/wallet/fiat/onramp/providers" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Create the session:

```bash
curl -X POST "http://localhost:8001/wallet/fiat/onramp/session" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "provider_id": "bank-bridge",
    "fiat_currency": "USD",
    "fiat_amount": "150.00",
    "country_code": "US",
    "return_url": "https://app.example.com/wallet/fiat/complete",
    "cancel_url": "https://app.example.com/wallet/fiat/cancel"
  }'
```

Example response:

```json
{
  "session_id": "96d0f3f7-8d72-4964-a2df-4552b02b9d71",
  "provider_id": "bank-bridge",
  "state": "pending_redirect",
  "handoff_url": "https://bank-bridge.partner.example/checkout?...",
  "deposit_address": "bcrt1p...",
  "destination_wallet_id": "5d1a9f6d-40f8-4dbf-b4ff-c4eb0ec6a409",
  "expires_at": "2026-04-15T12:20:00Z",
  "disclaimer": "Bank Bridge completes cardholder checks...",
  "compliance_action": "review_terms"
}
```

Frontend redirect example:

```js
const sessionResponse = await fetch('/v1/wallet/wallet/fiat/onramp/session', {
  method: 'POST',
  headers: {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${accessToken}`,
  },
  body: JSON.stringify({
    provider_id: 'bank-bridge',
    fiat_currency: 'USD',
    fiat_amount: '150.00',
    country_code: 'US',
    return_url: 'https://app.example.com/wallet/fiat/complete',
    cancel_url: 'https://app.example.com/wallet/fiat/cancel',
  }),
});

const session = await sessionResponse.json();
window.location.assign(session.handoff_url);
```

### Common workflows

#### Wallet overview workflow

1. Obtain an access token from auth.
2. Call `GET /wallet` to fetch the aggregate portfolio.
3. If the user opens a detailed yield screen, call `GET /wallet/yield/summary`.
4. If the user opens transaction history, page through `GET /wallet/transactions` using `next_cursor`.

#### Receive BTC workflow

1. Call `POST /wallet/onchain/address` for a deposit destination.
2. Display the address or QR code to the user.
3. Refresh the wallet summary later, but do not assume the current implementation will auto-credit deposits.

#### Hosted fiat on-ramp workflow

1. Call `GET /wallet/fiat/onramp/providers`.
2. Confirm the user's KYC posture before presenting providers that require KYC.
3. Call `POST /wallet/fiat/onramp/session`.
4. Redirect the user to `handoff_url`.
5. After return, refresh wallet data and show provider-completion guidance, but understand that current deposit reconciliation is incomplete.

## 6. Frontend Integration Recommendations

### Auth, session state, and token refresh

- Treat wallet as a token-protected service. The frontend should obtain and refresh JWTs through the auth service.
- Send `Authorization: Bearer <access-token>` on every authenticated request.
- Send `X-2FA-Code` only when needed, and never persist TOTP codes in browser storage.
- If a request fails because the access token expired, refresh the token through auth and retry only idempotent reads.
- Do not automatically replay `POST /wallet/onchain/withdraw`, `POST /lightning/payments`, or `POST /wallet/fiat/onramp/session` after a token refresh without explicit user confirmation.

### Suggested UI flows

- Wallet home: call `GET /wallet` for top-line balances and value.
- Yield details: lazy-load `GET /wallet/yield/summary` only when the user opens the yield detail screen.
- Transaction history: implement cursor-based infinite scroll for `GET /wallet/transactions`.
- Receive screen: call `POST /wallet/onchain/address`, then render QR and address copy actions.
- Send on-chain screen: validate address and amount client-side, prompt for 2FA code, then call `POST /wallet/onchain/withdraw`.
- Lightning receive screen: create an invoice and poll `GET /lightning/invoices/{r_hash}` until settled or canceled.
- Lightning pay screen: submit the BOLT11 string, detect whether the user has 2FA enabled in broader account settings, and collect `X-2FA-Code` when required.
- Fiat on-ramp screen: list providers, display compliance notices, confirm KYC readiness, then redirect the user with the returned `handoff_url`.

### Validation, loading states, retries, and error handling

- Validate on-chain withdrawal address prefixes before submit, but rely on backend validation as the source of truth.
- Enforce positive satoshi amounts and fee-rate bounds in the UI.
- Normalize fiat currency and country code inputs to uppercase before calling the on-ramp session endpoint.
- Show explicit loading and pending states for Lightning payments and hosted on-ramp session creation.
- Poll invoice status rather than assuming real-time updates. There is no wallet WebSocket or SSE stream.
- Handle both `error.code/message` and `detail` response formats.
- Treat `POST /lightning/payments` returning `200` with `status = FAILED` as a business failure, not a success.

### Caching guidance

| Resource | Cache guidance | Why |
| --- | --- | --- |
| `GET /wallet` | Short-lived cache only, or explicit user refresh | It can trigger yield accrual work and reflects balances that may change due to payments or market activity |
| `GET /wallet/yield/summary` | Fetch on demand | More detailed and potentially heavier than the wallet summary |
| `GET /wallet/transactions` | Cache loaded pages, refresh the first page periodically | Older pages are mostly immutable; newest page changes most often |
| `GET /wallet/custody` | Cache for the current session | Custody posture changes rarely |
| `GET /wallet/fiat/onramp/providers` | Cache briefly, for example 5 to 15 minutes | Provider catalog is mostly static, but compliance messaging may change |
| `GET /lightning/invoices/{r_hash}` | Always fetch fresh while polling | Invoice state changes over time |
| `POST /wallet/onchain/address` | Never cache or reuse blindly | Each request should be treated as a new receive intent |
| `POST /wallet/fiat/onramp/session` | Never cache | Session expiry and handoff URLs are time-sensitive |

### Security recommendations for frontend consumption

- Never store seed material, custody metadata beyond what the backend returns, or TOTP secrets on the client.
- Display provider disclaimers and compliance notices exactly as returned.
- Mask or abbreviate txids, hashes, and deposit addresses in the UI where practical.
- Use HTTPS for all frontend-to-gateway communication.
- Avoid presenting a Lightning invoice lookup tool that accepts arbitrary hashes from end users until backend ownership scoping is clarified.

### Frontend anti-patterns to avoid

- Do not infer that on-ramp provider `state = ready` means the user is KYC-cleared; the session-creation endpoint remains authoritative.
- Do not assume a successful `POST /wallet/onchain/address` means deposit monitoring is active.
- Do not auto-retry withdrawal or payment POSTs after transient network failures without idempotency protection.
- Do not treat `GET /wallet` as a pure read with no side effects; it may insert yield accrual rows.
- Do not assume all errors follow the platform `error` contract today.

## 7. Internal Logic and Important Modules

| File | Role | Notes |
| --- | --- | --- |
| `services/wallet/main.py` | FastAPI app, routing, auth orchestration, on-chain helpers, Lightning handlers, on-ramp handlers | Most request orchestration lives here; it also contains placeholder address and txid generation logic |
| `services/wallet/db.py` | Async DB helpers | Handles wallet creation, token pricing query, transaction inserts, and history listing |
| `services/wallet/auth.py` | Lightweight JWT and 2FA dependencies | Used by wallet summary and Lightning routes |
| `services/wallet/schemas.py` | Pydantic models for on-chain, transaction history, custody, and on-ramp APIs | Defines request and response models for non-Lightning wallet APIs |
| `services/wallet/schemas_wallet.py` | Pydantic models for wallet summary and yield responses | Used by `GET /wallet` and `GET /wallet/yield/summary` |
| `services/wallet/schemas_lnd.py` | Pydantic models for Lightning invoice and payment APIs | Defines `Invoice`, `Payment`, and their request shapes |
| `services/wallet/lnd_client.py` | LND gRPC adapter | Encapsulates TLS + macaroon setup and the three wallet Lightning RPCs |
| `services/wallet/key_manager.py` | Seed generation/encryption wrapper around shared custody | Present in the service, but not currently used by the runtime request path |

### Where the business logic lives

- Route-level orchestration is concentrated in `services/wallet/main.py`.
- Persistence logic lives in `services/wallet/db.py`.
- Yield accrual business rules are implemented in `services/common/incentives.py`.
- Hosted fiat on-ramp provider rules live in `services/common/onramp.py`.
- Custody backend selection, envelope handling, and derivation-path logic live in `services/common/custody.py`.

### Core domain logic vs adapters/integrations

- Core wallet domain orchestration: `main.py` and `db.py`.
- Shared cross-domain business logic: `common.incentives`, `common.onramp`, and `common.custody`.
- External integration adapter: `lnd_client.py`.
- Infrastructure concerns: `common.config`, `common.security`, `common.metrics`, `common.alerting`, and `common.audit`.

### Notable implementation detail

There are two authentication paths in the current service:

- `get_current_user_id()` from `services/wallet/auth.py` is used by wallet summary and Lightning routes.
- `_get_current_principal()` in `services/wallet/main.py` is used by custody, fiat on-ramp, on-chain address, withdrawal, and transaction-history routes.

That split leads to slightly different auth failure shapes and user-existence checks across endpoints.

## 8. Operational Notes

### Port and routing

- Service port: `8001`.
- Gateway prefix: `/v1/wallet/`.
- Dedicated gateway shortcuts also exist for health, readiness, and metrics.

### Required and inferred environment variables

The wallet service loads the shared `Settings` model, so it requires more configuration than it currently uses directly.

| Category | Variables |
| --- | --- |
| Service identity | `SERVICE_NAME`, `SERVICE_PORT`, `ENV_PROFILE`, `LOG_LEVEL` |
| Database | `DATABASE_URL`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, optionally `POSTGRES_PASSWORD` or `POSTGRES_PASSWORD_FILE` |
| Redis | `REDIS_URL` |
| Auth | `JWT_SECRET` or `JWT_SECRET_FILE`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS`, `TOTP_ISSUER` |
| Bitcoin | `BITCOIN_RPC_HOST`, `BITCOIN_RPC_PORT`, `BITCOIN_RPC_USER`, optionally `BITCOIN_RPC_PASSWORD` or `BITCOIN_RPC_PASSWORD_FILE`, `BITCOIN_NETWORK` |
| LND | `LND_GRPC_HOST`, `LND_GRPC_PORT`, `LND_MACAROON_PATH`, `LND_TLS_CERT_PATH` |
| Taproot Assets | `TAPD_GRPC_HOST`, `TAPD_GRPC_PORT`, `TAPD_MACAROON_PATH`, `TAPD_TLS_CERT_PATH` |
| Custody | `CUSTODY_BACKEND`, `WALLET_ENCRYPTION_KEY` or `WALLET_ENCRYPTION_KEY_FILE`, or HSM settings `CUSTODY_HSM_KEY_LABEL`, `CUSTODY_HSM_WRAPPING_KEY[_FILE]`, `CUSTODY_HSM_SIGNING_KEY[_FILE]` |
| Alerting | `ALERT_WEBHOOK_URL` or `ALERT_WEBHOOK_URL_FILE` |
| Shared service URLs | `WALLET_SERVICE_URL`, `TOKENIZATION_SERVICE_URL`, `MARKETPLACE_SERVICE_URL`, `EDUCATION_SERVICE_URL`, `NOSTR_SERVICE_URL` |

### Active vs configured external dependencies

| Dependency | Used actively by handlers | Used by readiness only | Notes |
| --- | --- | --- | --- |
| PostgreSQL | Yes | Yes | Primary persistence layer |
| LND | Yes | Yes | Active Lightning integration |
| Bitcoin Core | No | Yes | No current broadcast or address derivation flow |
| tapd | No | Yes | No wallet-side tapd RPC integration today |
| Redis | No | Yes | No wallet-side event streaming or caching logic today |

### Observability considerations

- `GET /health` exposes liveness.
- `GET /ready` checks PostgreSQL, Redis, Bitcoin Core, LND, and tapd over TCP.
- `GET /metrics` supports Prometheus text and `?format=json`.
- Request metrics are auto-recorded via middleware.
- Business events recorded include:
  - `wallet_custody_status`
  - `wallet_fiat_onramp_providers`
  - `wallet_fiat_onramp_session`
  - `wallet_invoice_create`
  - `wallet_payment`
  - `wallet_onchain_address_create`
  - `wallet_onchain_withdrawal`
- Audit events are recorded for:
  - `wallet.fiat_onramp_session`
  - `wallet.lightning.pay`
  - `wallet.onchain_withdraw`
- Lightning payment failures trigger a CRITICAL alert through the alert dispatcher.

### Security considerations

- JWT verification is local to the service; there is no callback to auth for token introspection.
- Missing or invalid auth may produce either platform `error` responses or FastAPI `detail` responses depending on the route.
- On-chain withdrawals always require `X-2FA-Code`; Lightning payments require it only when the user has 2FA enabled.
- Write endpoints are rate-limited, and sensitive paths have stricter per-path limits.
- Sensitive log values are redacted by `SensitiveDataFilter`.
- In non-local profiles, software custody requires file-backed wallet encryption secrets, and HSM mode requires file-backed wrapping and signing keys.
- The current implementation still contains financial-risk placeholders:
  - random deposit-address generation,
  - synthetic txid creation,
  - invoice lookup not scoped to a user's own invoices,
  - no deposit reconciliation,
  - no Lightning balance synchronization into `wallets.lightning_balance_sat`.

## 9. Example End-to-End Flow

### Flow 1: Portfolio summary with yield accrual

1. The auth service issues an access token after login.
2. The frontend calls `GET /wallet`.
3. The wallet service validates the JWT and loads the user's wallet row.
4. Before responding, wallet calls `accrue_pending_yield_for_user()` from `common.incentives`.
5. That shared logic reads `token_balances`, `tokens`, `assets`, and latest settled `trades` to determine whether a new full-day `yield_accruals` row should be inserted.
6. Wallet then reads token balances and summarized yield totals.
7. The frontend receives a single summary object containing on-chain, Lightning, token, and yield totals.

This flow shows how wallet depends on auth for identity, tokenization for asset metadata, and marketplace for latest settlement prices, without making direct HTTP calls to those services.

### Flow 2: Hosted fiat on-ramp session launch

1. The user authenticates and reaches the wallet funding UI.
2. The frontend calls `GET /wallet/fiat/onramp/providers` to fetch providers and compliance notices.
3. The wallet service looks up the user's KYC row via `auth.kyc_db`.
4. The frontend calls `POST /wallet/fiat/onramp/session` with provider, fiat amount, and return/cancel URLs.
5. Wallet validates the user, lazily creates a wallet if needed, checks KYC status, and delegates session creation to `common.onramp`.
6. The service records an audit event and returns a hosted `handoff_url` plus a deposit address.
7. The frontend redirects the user to the provider's hosted checkout.
8. Intended next step: BTC is delivered to the generated wallet address and later reflected in wallet balance.
9. Current implementation gap: the service does not yet reconcile the resulting deposit back into `wallets.onchain_balance_sat`.

## 10. Open Questions / Assumptions

- **Assumption:** `wallets` and `transactions` are wallet-owned tables, even though all tables live in a shared schema module.
- **Assumption:** frontend traffic should usually go through the gateway, even though direct-service URLs are clearer for local examples.
- **Open question:** should the public contract use the spec routes `POST /wallet/lightning/invoice` and `POST /wallet/lightning/pay`, or the implemented routes `POST /lightning/invoices` and `POST /lightning/payments`?
- **Open question:** should `GET /wallet` lazily create wallets the same way `GET /wallet/custody`, `POST /wallet/onchain/address`, and `GET /wallet/transactions` do?
- **Open question:** what service or background worker is responsible for reconciling incoming on-chain deposits and updating `wallets.onchain_balance_sat`?
- **Open question:** should Lightning channel balances be synchronized from LND into `wallets.lightning_balance_sat`, and if so, on what cadence?
- **Open question:** should invoice lookup be scoped to the authenticated user's own invoices rather than any hash the caller provides?
- **Open question:** is tapd intended to become an active runtime dependency for wallet balance synchronization, or remain a tokenization-only concern?
- **Open question:** should provider state in `GET /wallet/fiat/onramp/providers` reflect KYC readiness instead of always returning `ready`?
- **Assumption:** the current placeholder on-chain address generation and synthetic txid creation are transitional implementation steps, not the final intended custody design.

## Integration Summary

For frontend teams, the wallet service is the API surface for balances, transaction history, Lightning actions, hosted fiat funding flows, and custody posture. Integrate against the live route shapes from `services/wallet/main.py`, handle both `error` and `detail` failure bodies, poll for Lightning state changes, and avoid treating deposit-address creation or on-ramp session creation as proof of completed balance reconciliation.

For backend teams, the wallet service currently acts as an orchestration layer over shared persistence, shared incentives/on-ramp/custody modules, and LND. If you extend it, keep the separation between wallet-owned tables and shared platform tables explicit, reconcile the documented API contract with the implemented Lightning routes, and prioritize closing the current gaps around deterministic address derivation, actual Bitcoin transaction broadcasting, deposit reconciliation, and balance synchronization.