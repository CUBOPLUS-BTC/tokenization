# Marketplace Service Integration Guide

> Migration notice: this guide predates the Liquid / Elements cutover. References to tapd, Taproot-era readiness, or Taproot asset identifiers are historical unless explicitly marked otherwise.

This document describes the marketplace service as it is currently implemented in `services/marketplace`, with explicit notes where repository specs or service README text describe intended behavior that is not yet fully realized in code.

## 1. Service Overview

The marketplace service is the trading and escrow boundary for tokenized assets. It owns order placement, order matching, trade creation, escrow lifecycle transitions, dispute handling, market-data reads, and the realtime streams that notify clients about fills and escrow state changes.

### Purpose of the service

- Accept buy and sell orders for tokenized assets.
- Match compatible orders using price-time priority.
- Create a 2-of-3 multisig escrow record for each matched trade.
- Track escrow funding, release, and dispute states.
- Maintain trade history and aggregated order-book views.
- Emit market and settlement events into Redis-backed realtime streams.

### Main responsibilities

- Order intake and validation, including support for `limit` and `stop_limit` orders.
- Balance and inventory prechecks before orders are accepted.
- High-value trade gating through KYC verification checks.
- Matchmaking between opposing orders while preventing self-matching.
- Escrow creation with buyer, seller, and platform public keys.
- Settlement and dispute resolution through shared database mutations.
- Market-data delivery through REST and WebSocket APIs.
- Audit logging, metrics, rate limiting, and critical settlement alerts.

### Business and domain role within the platform

Within the overall tokenization platform, marketplace is where tokenized assets become tradable instruments. Tokenization creates the assets and balances, auth establishes who the actors are, and wallet represents user funds, but marketplace is the service that turns those ingredients into executable orders, matched trades, escrow records, fee ledger entries, and dispute workflows.

### Why this service exists separately from the others

- Order matching and escrow state transitions are a distinct financial domain with their own invariants.
- Multisig address construction, funding checks, release logic, and dispute flows create a much tighter coupling to Bitcoin trade settlement than auth, tokenization, or education should carry.
- Market-data APIs and realtime feeds have different latency, caching, and operational characteristics than user-profile or wallet-summary endpoints.
- The service needs its own rate-limited write surface, audit events, settlement failure alerts, and domain metrics.

### Current implementation status

| Area | Current implementation | Intended or documented platform behavior |
| --- | --- | --- |
| Matching model | Matching creates a `pending` trade plus a `created` escrow and locks seller tokens immediately | Aligned with the platform design |
| Advanced orders | `limit` and `stop_limit` orders are implemented, including trigger activation | Not yet reflected in the older marketplace section of `specs/api-contracts.md` |
| Trade fees | Schema, settlement helpers, and treasury entries support `fee_sat`, but `POST /orders` never calculates a non-zero fee when it calls `create_trade_escrow()` | Service README says platform commission should be extracted |
| Escrow funding | Funding is detected lazily when `GET /escrows/{trade_id}` scans Bitcoin Core via `scantxoutset` | There is no separate funding webhook or mutation endpoint |
| Escrow release | Buyer or seller submits a hex `partial_signature`; the service adds a platform HMAC-derived signature and generates a synthetic `release_txid` | This is not yet a real on-chain multisig signing and broadcast flow |
| Dispute routes | Code exposes `POST /trades/{trade_id}/dispute` and `POST /trades/{trade_id}/dispute/resolve` | `specs/api-contracts.md` still documents escrow-prefixed and admin-prefixed routes with different resolution names |
| Wallet synchronization | Seller tokens are locked at match time, but buyer wallet balances are only debited when the escrow is released or dispute resolution chooses `release` | Assumption: a future wallet-side lock or real funding reconciliation is expected |

## 2. Service Relationships

The marketplace service interacts with other platform services mostly through shared database tables, shared `services/common` modules, and Redis-backed event streams rather than direct HTTP calls.

### Relationships with other platform services

| Service | Purpose of interaction | Interaction type | Current implementation |
| --- | --- | --- | --- |
| `services/auth` | Access-token validation and KYC enforcement | Authentication dependency and shared database access | Marketplace imports `decode_token()` and reads `users` plus `kyc_verifications` through `auth.kyc_db` |
| `services/wallet` | User wallet balances and escrow key material | Shared database access and shared custody helper dependency | Marketplace reads and mutates `wallets` directly, and derives escrow key material from wallet custody metadata rather than calling wallet HTTP APIs |
| `services/tokenization` | Tradable token existence and owner-side notifications | Shared database access and event-driven communication | Marketplace reads `tokens` directly and consumes tokenization's `ai.evaluation.complete` event for the personal notifications WebSocket |
| `services/nostr` | Downstream publication of market activity | Event-driven communication | Marketplace emits `trade.matched`, which the Nostr service subscribes to and republishes to relays |
| `services/admin` | Administrative dispute resolution and treasury/dispute visibility | Authorization dependency and shared database access | Marketplace itself enforces `admin` role for dispute resolution; admin service is expected to read the shared `disputes`, `escrows`, and `treasury` tables separately |
| `services/education` | Treasury funding destination at the business level | Shared database access only | Marketplace writes `treasury` fee-income rows, but there is no direct call into education service |
| `services/gateway` | Public routing boundary | Direct API exposure through reverse proxy | Nginx maps `/v1/marketplace/*` to this service on port `8003` |
| `services/nostr` and frontend clients | Realtime market updates | Event-driven communication | Marketplace mirrors events to Redis streams and exposes WebSocket readers over those streams |

### Dependencies on `services/common`

| Shared module | Purpose | Interaction type | Current implementation |
| --- | --- | --- | --- |
| `common.config` | Runtime settings, profile handling, secret resolution | Infrastructure/shared module dependency | Provides service port, DB URL, Redis URL, Bitcoin RPC settings, JWT secret, custody settings, and rate limits |
| `common.db.metadata` | Canonical table definitions | Infrastructure/shared module dependency | Marketplace imports shared table metadata for orders, trades, escrows, disputes, wallets, token balances, treasury, users, tokens, and nostr identities |
| `common.events` | Internal publish-subscribe and Redis stream mirroring | Event-driven infrastructure dependency | `InternalEventBus` publishes market events and `RedisStreamMirror` writes them into Redis streams |
| `common.realtime` | Resume-token encoding and Redis stream consumption | Event-driven infrastructure dependency | `RedisStreamFeed`, `encode_resume_token()`, and `decode_resume_token()` power the WebSocket streams |
| `common.audit` | Audit-log writes for state-changing actions | Infrastructure/shared module dependency | Order placement, order cancellation, escrow signing, dispute creation, and dispute resolution write audit records |
| `common.metrics` | `/metrics` endpoint and business-event counters | Infrastructure/shared module dependency | Tracks `order_place`, `trade_match`, `escrow_fund`, `escrow_release`, `dispute_open`, `dispute_resolve`, and settlement failures |
| `common.alerting` | Critical alert dispatch | Infrastructure/shared module dependency | Settlement and funding scan failures fire CRITICAL alerts |
| `common.security` | Request IDs and rate limiting | Infrastructure/shared module dependency | Applies write-rate limiting to marketplace writes, especially `/orders`, `/escrows/`, and `/trades/` |
| `common.logging` | Structured JSON logs with redaction | Infrastructure/shared module dependency | Configured at startup for the marketplace service |
| `common.readiness` | Shared dependency checks | Infrastructure/shared module dependency | `GET /ready` checks PostgreSQL, Redis, Bitcoin Core, LND, and tapd |
| `common.custody` | Platform signer and escrow key derivation helpers | Infrastructure/shared module dependency | Used to derive the platform escrow pubkey and participant escrow material |

### Event relationships

| Topic | Produced by marketplace | Consumed by | Purpose |
| --- | --- | --- | --- |
| `trade.matched` | Yes | Nostr service, marketplace price stream, marketplace notification stream | Announces newly matched trades and powers price updates |
| `escrow.funded` | Yes | Marketplace notification stream | Announces that a previously created escrow now has funding |
| `escrow.released` | Yes | Marketplace notification stream | Announces settlement release |
| `ai.evaluation.complete` | No | Marketplace notification stream | Lets a user's notification channel surface tokenization-side AI evaluation completion |

### Architectural coupling to call out explicitly

- Marketplace does not call wallet APIs. It mutates shared `wallets` and `token_balances` rows directly during settlement and dispute resolution.
- Marketplace does not call auth APIs. It validates JWTs locally with the shared `JWT_SECRET` and then checks the `users` table.
- Marketplace does not call tokenization APIs. It reads `tokens` directly and consumes tokenization events through Redis streams.

## 3. Database Documentation

All table definitions live in `services/common/db/metadata.py`, and migrations are platform-wide Alembic revisions. The database is shared across services, so "ownership" below means domain ownership rather than physical schema isolation.

### Marketplace-owned tables

| Table | Ownership | Purpose | Important fields and constraints | Relationships |
| --- | --- | --- | --- | --- |
| `orders` | Marketplace-owned | Persistent order book | `side` in `buy|sell`; `order_type` in `limit|stop_limit`; `quantity > 0`; `price_sat > 0`; `trigger_price_sat` required only for stop-limit; `status` in `open|partially_filled|filled|cancelled`; `filled_quantity` tracks cumulative fills | FK to `users` and `tokens`; referenced by `trades` |
| `trades` | Marketplace-owned | Matched execution record | `buy_order_id`, `sell_order_id`, `token_id`; `quantity`, `price_sat`, `total_sat`, `fee_sat`; `status` in `pending|escrowed|settled|disputed`; `settled_at` nullable until release or dispute resolution | FK to `orders` and `tokens`; one-to-one with `escrows`; one-to-one with `disputes` once disputed |
| `escrows` | Marketplace-owned | Multisig escrow state | `trade_id` unique; buyer/seller/platform compressed pubkeys; `multisig_address`; `locked_amount_sat`; `funding_txid`; `release_txid`; `collected_signatures` JSONB; `status` in `created|funded|released|refunded|disputed`; `expires_at` required | FK to `trades`; unique `trade_id` enforces one escrow per trade |
| `disputes` | Marketplace-owned | Formal record of a contested escrow | `trade_id` unique; `opened_by`; `reason`; `status` in `open|resolved`; `resolution` nullable but constrained to `refund|release` when set; `resolved_by`, `resolved_at` optional until closure | FK to `trades` and `users`; unique `trade_id` enforces one dispute per trade |

### Shared tables that marketplace reads or writes

| Table | Primary owner | How marketplace uses it | Important fields and constraints | Notes |
| --- | --- | --- | --- | --- |
| `wallets` | Wallet-owned | Reads buyer and seller balances; debits buyer and credits seller at release or dispute resolution | `onchain_balance_sat >= 0`, `lightning_balance_sat >= 0`, `encrypted_seed`, `derivation_path` | Marketplace never creates wallet rows; missing wallet rows block settlement |
| `token_balances` | Shared between tokenization, marketplace, and wallet reporting | Verifies sell-side inventory, locks seller tokens at match time, credits buyer on release, refunds seller on dispute refund | Unique `(user_id, token_id)`; `balance >= 0` | Seller balances are reduced at escrow creation, not release |
| `tokens` | Tokenization-owned | Validates tradable asset existence and provides fallback reference price | `unit_price_sat`, `asset_id`, `taproot_asset_id` | Marketplace does not read `assets` directly in current code |
| `users` | Auth-owned | Validates principals, reads roles, checks `totp_secret`, confirms soft-delete state | `role`, `totp_secret`, `deleted_at` | JWT claims are not trusted alone; the user row is reloaded from DB |
| `kyc_verifications` | Auth-owned | Blocks high-value trades above `kyc_trade_threshold_sat` | `status` in `pending|verified|rejected|expired`; unique `user_id` | Read through `auth.kyc_db`, not raw SQL in marketplace |
| `nostr_identities` | Auth/Nostr-owned | Provides a participant pubkey for escrow-key resolution when present | `pubkey` unique | If missing, marketplace derives a placeholder escrow pubkey from wallet custody metadata |
| `treasury` | Shared with admin and education reporting | Records `fee_income` rows when a settled trade has `fee_sat > 0` | `type` in `fee_income|disbursement|adjustment|referral_reward`; running `balance_after_sat` | Marketplace writes fee-income entries, but current HTTP order flow leaves `fee_sat` at `0` |

### Table behavior in the current implementation

#### `orders`

- `create_order()` always inserts with status `open`.
- `stop_limit` orders remain dormant until `triggered_at` is set.
- The matching query excludes the requester's own orders and ignores untriggered stop orders.
- There is no dedicated "my orders" query. `GET /orders` returns market-wide results, not user-scoped results.

#### `trades`

- Trade rows are created in `pending` state by `create_trade_escrow()`.
- `mark_escrow_funded()` transitions the trade to `escrowed`.
- `process_escrow_signature()` or `resolve_dispute()` transitions the trade to `settled`.
- A disputed trade moves from `escrowed` to `disputed`, then back to `settled` after resolution.

#### `escrows`

- Escrow rows are created at match time with a 24-hour expiration window.
- `collected_signatures` is stored as JSONB and updated incrementally.
- The public API does not expose collected signatures or participant pubkeys, only status-level escrow details.
- Funding is discovered only when the escrow details endpoint checks Bitcoin Core.

#### `wallets` and `token_balances`

- Seller token balances are decremented immediately when a trade is matched and escrow is created.
- Buyer wallet balances are not decremented at match time or funding time. They are only decremented on final release or dispute resolution with `release`.
- This means the shared wallet ledger does not model locked buyer funds while an escrow remains `created`, `funded`, or `disputed`.

#### `treasury`

- `record_trade_fee_income()` is idempotent for a given `source_trade_id`.
- A `fee_income` row is only written if `fee_sat > 0`.
- `create_order()` and the match loop do not compute fees today, so normal order-driven trades currently write zero treasury income unless another caller sets `fee_sat` explicitly.

### Relevant Alembic migrations

| Migration | Relevance to marketplace |
| --- | --- |
| `20260413_1330_0002_remaining_schema_tables.py` | Introduces `orders`, `trades`, `escrows`, `treasury`, `tokens`, `token_balances`, `transactions`, and `nostr_identities` |
| `20260414_1200_0006_add_escrow_signatures.py` | Adds `escrows.collected_signatures` as JSONB with `{}` default |
| `20260414_1400_0007_add_disputes.py` | Adds the `disputes` table and its resolution/status constraints |
| `20260415_1200_0010_add_referrals_yield_and_advanced_orders.py` | Adds `orders.order_type`, `orders.trigger_price_sat`, `orders.triggered_at`, and extends `treasury` for referral-linked entries |
| `20260413_1800_0003_align_domain_schema_constraints.py`, `20260413_1830_0004_normalize_check_constraint_names.py`, `20260415_1600_0011_normalize_late_check_constraint_names.py` | Normalize or add check-constraint coverage on shared tables that marketplace depends on, especially `token_balances`, `orders`, `disputes`, and later-added treasury fields |

### Assumptions and caveats

- Assumption: `treasury` is operationally shared between marketplace, admin, and education reporting because no service-specific treasury table exists.
- Assumption: buyer-side actual Bitcoin funding comes from outside the wallet-service ledger until a tighter wallet integration is implemented.

## 4. API Endpoints

### Path conventions

- Internal service routes are mounted directly on the marketplace service at port `8003`.
- Through the gateway, clients typically use `/v1/marketplace/<internal-path-without-leading-slash>`.
- Example: internal `POST /orders` becomes gateway `POST /v1/marketplace/orders`.
- Health, readiness, and metrics also have direct gateway shortcuts: `/health/marketplace`, `/ready/marketplace`, and `/metrics/marketplace`.
- WebSocket clients should normally connect through `/v1/marketplace/ws/...` behind the gateway.

### Common auth and error conventions

- All HTTP endpoints except `/health`, `/ready`, and `/metrics` require a Bearer access token.
- Most validation and contract errors use the platform error shape:

```json
{
  "error": {
    "code": "string_slug",
    "message": "Human-readable description"
  }
}
```

- Request-model validation is normalized to `422 validation_error`.
- Sensitive write routes are rate limited and return `429 rate_limit_exceeded` with `Retry-After`, `X-RateLimit-Limit`, and `X-RateLimit-Remaining` headers.

### Common error codes you should expect

| Code | Typical status | Meaning |
| --- | --- | --- |
| `authentication_required` | 401 | Missing access token |
| `invalid_token` | 401 | Token invalid, expired, or linked to a deleted user |
| `validation_error` | 422 | Request payload or parameters failed schema validation |
| `invalid_cursor` | 422 | Cursor is not a valid UUID or is not present in the current filtered result set |
| `forbidden` | 403 | User is not allowed to access the resource |
| `rate_limit_exceeded` | 429 | Write or sensitive-path rate limit exceeded |
| `token_not_found` | 404 | Requested token does not exist |
| `trade_not_found` | 404 | Requested trade does not exist |
| `escrow_not_found` | 404 | Trade exists but has no escrow row |
| `order_not_found` | 404 | Requested order does not exist |

### 4.1 Operational endpoints

#### `GET /health`

- Gateway path: `/v1/marketplace/health` or `/health/marketplace`
- Purpose: Liveness probe.
- Authentication: None.
- Request parameters: None.
- Request body: None.
- Response schema: `{status, service, env_profile}`.
- Possible error responses: None expected during normal operation.

#### `GET /ready`

- Gateway path: `/v1/marketplace/ready` or `/ready/marketplace`
- Purpose: Readiness probe.
- Authentication: None.
- Request parameters: None.
- Request body: None.
- Response schema: `{status, service, env_profile, dependencies}` where dependencies include `postgres`, `redis`, `bitcoin`, `lnd`, and `tapd`.
- Possible error responses:
  - `503` when any dependency is not ready.

#### `GET /metrics`

- Gateway path: `/v1/marketplace/metrics` or `/metrics/marketplace`
- Purpose: Observability endpoint.
- Authentication: None.
- Query parameters:
  - `format=json` for JSON output.
  - Omit `format` for Prometheus text exposition.
- Request body: None.
- Response schema:
  - Prometheus text by default.
  - JSON metrics snapshot plus readiness payload when `format=json` is supplied.
- Possible error responses: None expected during normal operation.

### 4.2 Orders and market data

#### `POST /orders`

- Gateway path: `/v1/marketplace/orders`
- Purpose: Create a new order and, if possible, immediately match it against resting orders.
- Authentication: Bearer access token required.
- Authorization: Any authenticated user with a wallet; KYC may be required for high-value trades.
- Request parameters: None.
- Request body schema:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `token_id` | UUID | Yes | Token to trade |
| `side` | `buy` or `sell` | Yes | Trade direction |
| `order_type` | `limit` or `stop_limit` | No | Defaults to `limit` |
| `quantity` | integer > 0 | Yes | Token units |
| `price_sat` | integer > 0 | Yes | Satoshis per token unit |
| `trigger_price_sat` | integer > 0 or `null` | Conditional | Required for `stop_limit`, forbidden for `limit` |

- Response schema: `OrderResponse` with one `order` object containing `id`, `token_id`, `side`, `order_type`, `quantity`, `price_sat`, `trigger_price_sat`, `triggered_at`, `is_triggered`, `filled_quantity`, `status`, and `created_at`.
- Possible error responses:
  - `404 token_not_found`
  - `404 wallet_not_found`
  - `409 insufficient_sats`
  - `409 insufficient_token_balance`
  - `403 kyc_required`
  - `403 kyc_pending`
  - `403 kyc_rejected`
  - `403 kyc_not_verified`
  - `401 authentication_required`
  - `401 invalid_token`
  - `422 validation_error`

#### `GET /orders`

- Gateway path: `/v1/marketplace/orders`
- Purpose: List orders with optional market filters.
- Authentication: Bearer access token required.
- Authorization: Any authenticated user.
- Request parameters:
  - `token_id`: optional UUID filter.
  - `side`: optional `buy|sell`.
  - `status`: optional `open|partially_filled|filled|cancelled`.
  - `cursor`: optional UUID of the last seen row.
  - `limit`: optional integer between `1` and `100`, default `20`.
- Request body: None.
- Response schema: `OrderListResponse` with `orders[]` and `next_cursor`.
- Possible error responses:
  - `401 authentication_required`
  - `401 invalid_token`
  - `422 invalid_cursor`

Important behavior:

- This is a market-wide order listing endpoint, not a user-scoped "my orders" endpoint.
- `cursor` is not opaque. It must match a row currently present in the filtered result set.

#### `GET /orderbook/{token_id}`

- Gateway path: `/v1/marketplace/orderbook/{token_id}`
- Purpose: Return aggregated bid and ask depth for a token.
- Authentication: Bearer access token required.
- Authorization: Any authenticated user.
- Request parameters:
  - `token_id` path parameter as UUID.
- Request body: None.
- Response schema: `OrderBookResponse` with `token_id`, `bids[]`, `asks[]`, `last_trade_price_sat`, and `volume_24h`.
- Possible error responses:
  - `404 token_not_found`
  - `401 authentication_required`
  - `401 invalid_token`

Important behavior:

- Only `open` and `partially_filled` orders contribute to the book.
- Untriggered `stop_limit` orders are excluded until `triggered_at` is set.

#### `DELETE /orders/{order_id}`

- Gateway path: `/v1/marketplace/orders/{order_id}`
- Purpose: Cancel an open or partially filled order.
- Authentication: Bearer access token required.
- Authorization: Only the order owner may cancel.
- Request parameters:
  - `order_id` path parameter as UUID.
- Request body: None.
- Response schema: `CancelOrderResponse` with `{order: {id, status}}`.
- Possible error responses:
  - `404 order_not_found`
  - `403 forbidden`
  - `409 order_state_conflict`
  - `401 authentication_required`
  - `401 invalid_token`

#### `GET /trades`

- Gateway path: `/v1/marketplace/trades`
- Purpose: Return global trade history with optional token filtering.
- Authentication: Bearer access token required.
- Authorization: Any authenticated user.
- Request parameters:
  - `token_id`: optional UUID filter.
  - `cursor`: optional UUID cursor.
  - `limit`: optional integer between `1` and `100`, default `20`.
- Request body: None.
- Response schema: `TradeListResponse` with `trades[]` and `next_cursor`.
- Possible error responses:
  - `404 token_not_found` when a `token_id` filter is supplied for a missing token.
  - `422 invalid_cursor`
  - `401 authentication_required`
  - `401 invalid_token`

Important behavior:

- Like `GET /orders`, this is a market-wide feed and not a user-scoped personal trade ledger.

### 4.3 Escrow endpoints

#### `GET /escrows/{trade_id}`

- Gateway path: `/v1/marketplace/escrows/{trade_id}`
- Purpose: Return escrow details for a trade and opportunistically refresh funding state.
- Authentication: Bearer access token required.
- Authorization: Buyer, seller, or admin only.
- Request parameters:
  - `trade_id` path parameter as UUID.
- Request body: None.
- Response schema: `EscrowResponse` with `{id, trade_id, multisig_address, locked_amount_sat, funding_txid, release_txid, status, expires_at}`.
- Possible error responses:
  - `404 trade_not_found`
  - `404 escrow_not_found`
  - `403 forbidden`
  - `401 authentication_required`
  - `401 invalid_token`

Important behavior:

- If the escrow is still `created`, the handler may scan Bitcoin Core and persist a transition to `funded` before returning.
- This endpoint is currently the only public path that triggers escrow funding refresh.

#### `POST /escrows/{trade_id}/sign`

- Gateway path: `/v1/marketplace/escrows/{trade_id}/sign`
- Purpose: Submit a participant signature toward escrow release.
- Authentication: Bearer access token required.
- Authorization: Buyer or seller only.
- Additional header:
  - `X-2FA-Code`: required if the authenticated user has a `totp_secret` configured.
- Request parameters:
  - `trade_id` path parameter as UUID.
- Request body schema:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `partial_signature` | non-empty hex string | Yes | Stored as submitted; platform signature is derived server-side |

- Response schema: `EscrowResponse` showing the latest escrow state after the signature is recorded.
- Possible error responses:
  - `404 trade_not_found`
  - `404 escrow_not_found`
  - `403 forbidden`
  - `403 2fa_required`
  - `403 2fa_invalid`
  - `409 escrow_state_conflict`
  - `422 invalid_signature`
  - `401 authentication_required`
  - `401 invalid_token`

Important behavior:

- The service always injects a platform signature alongside the participant signature.
- When the signature threshold is considered met, the service settles the trade, updates balances, and returns the escrow in `released` state.
- The public response does not expose `collected_signatures`, so clients cannot inspect partial signature progress directly.

### 4.4 Dispute endpoints

#### `POST /trades/{trade_id}/dispute`

- Gateway path: `/v1/marketplace/trades/{trade_id}/dispute`
- Purpose: Open a dispute against an escrowed trade.
- Authentication: Bearer access token required.
- Authorization: Buyer or seller only.
- Request parameters:
  - `trade_id` path parameter as UUID.
- Request body schema:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `reason` | string, 1-2000 chars | Yes | Human-readable dispute explanation |

- Response schema: `DisputeResponse` with the created dispute row.
- Possible error responses:
  - `404 trade_not_found`
  - `403 forbidden`
  - `409 trade_state_conflict`
  - `409 dispute_already_exists`
  - `401 authentication_required`
  - `401 invalid_token`
  - `422 validation_error`

#### `POST /trades/{trade_id}/dispute/resolve`

- Gateway path: `/v1/marketplace/trades/{trade_id}/dispute/resolve`
- Purpose: Resolve a previously opened dispute.
- Authentication: Bearer access token required.
- Authorization: Admin only.
- Request parameters:
  - `trade_id` path parameter as UUID.
- Request body schema:

| Field | Type | Required | Notes |
| --- | --- | --- | --- |
| `resolution` | `refund` or `release` | Yes | `release` transfers value to seller and tokens to buyer; `refund` returns locked tokens to seller |

- Response schema: `DisputeResponse` with the resolved dispute row.
- Possible error responses:
  - `403 forbidden`
  - `404 trade_not_found`
  - `404 dispute_not_found`
  - `409 trade_state_conflict`
  - `409 dispute_already_resolved`
  - `409 resolution_conflict`
  - `401 authentication_required`
  - `401 invalid_token`

Implementation note:

- The current code does not require 2FA on dispute resolution, even though older spec text implies admin-side 2FA for escrow resolution.

### 4.5 Realtime endpoints

#### `WS /ws/prices/{token_id}`

- Gateway path: `/v1/marketplace/ws/prices/{token_id}`
- Purpose: Stream token-level price snapshots.
- Authentication: None.
- Query parameters:
  - `last_event_id`: optional Redis stream event ID to resume from.
- Initial behavior:
  - Without `last_event_id`, the connection immediately sends a snapshot message.
  - With `last_event_id`, the server resumes from Redis and waits for new matching events.
- Message shape:

```json
{
  "event": "price_update",
  "id": "1713142805000-0",
  "data": {
    "token_id": "uuid",
    "last_price_sat": 101000,
    "bid": 100500,
    "ask": 101500,
    "volume_24h": 25,
    "timestamp": "2026-04-15T01:00:05Z"
  }
}
```

- Error behavior:
  - If the token does not exist and no resume ID is supplied, the server sends a `token_not_found` error payload and closes with WebSocket code `1008`.

#### `WS /ws/notifications`

- Gateway path: `/v1/marketplace/ws/notifications`
- Purpose: Stream authenticated user notifications related to orders, escrows, and tokenization AI events.
- Authentication: Required.
- Supported auth mechanisms:
  - Query string `?access_token=<token>`.
  - `Authorization: Bearer <token>` header.
  - First JSON message within 5 seconds containing `{"access_token": "...", "resume_token": "..."}`.
- Resume behavior:
  - Clients may send `resume_token` via query string or first JSON frame.
  - Each server message includes an updated `resume_token` that encodes the latest per-topic stream positions.
- Outbound event types:
  - `order_filled`
  - `escrow_funded`
  - `escrow_released`
  - `ai_evaluation_complete`
- Example message:

```json
{
  "event": "order_filled",
  "id": "trade.matched:1713142805000-0",
  "resume_token": "eyJ0cmFkZS5tYXRjaGVkIjoiMTcxMzE0MjgwNTAwMC0wIn0",
  "data": {
    "order_id": "uuid",
    "trade_id": "uuid",
    "token_id": "uuid",
    "filled_quantity": 4,
    "price_sat": 100000,
    "status": "pending"
  }
}
```

- Error behavior:
  - Missing or invalid auth returns an error payload and closes with WebSocket code `1008`.
  - Invalid resume token returns `invalid_resume_token` and closes with `1008`.

Important behavior:

- `order_filled` is how a client currently learns the `trade_id` associated with its filled order.
- The notification stream is also the only public channel carrying tokenization-side `ai_evaluation_complete` events to end users.

## 5. How to Use the Endpoints

### Prerequisites

- Obtain a Bearer access token from the auth service.
- Know the `token_id` you want to trade; these are created by the tokenization service.
- Ensure the user has a wallet row and enough balance for the intended action.
- For sell orders, ensure the user already holds the token balance.
- For high-value orders, ensure the user is KYC-verified if the order value meets or exceeds `KYC_TRADE_THRESHOLD_SAT`.
- If the user has TOTP enabled, collect an `X-2FA-Code` before calling the escrow-sign endpoint.

### Common workflow: place an order and monitor for a fill

1. Connect to `/v1/marketplace/ws/notifications` before placing the order so you can capture `order_filled` events that contain `trade_id`.
2. Submit `POST /v1/marketplace/orders`.
3. If the order matches immediately, wait for `order_filled` on the notification stream.
4. Use the returned `trade_id` to call `GET /v1/marketplace/escrows/{trade_id}`.
5. Poll that escrow endpoint or wait for `escrow_funded` on the notification stream.
6. Once funded, collect a user-side signature and submit `POST /v1/marketplace/escrows/{trade_id}/sign`.

### cURL: place a limit buy order

```bash
curl -X POST http://localhost:8000/v1/marketplace/orders \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "token_id": "11111111-2222-3333-4444-555555555555",
    "side": "buy",
    "order_type": "limit",
    "quantity": 5,
    "price_sat": 100000
  }'
```

Example response:

```json
{
  "order": {
    "id": "9a9e8ca3-1d3b-46c2-a3e8-444528dbf1e1",
    "token_id": "11111111-2222-3333-4444-555555555555",
    "side": "buy",
    "order_type": "limit",
    "quantity": 5,
    "price_sat": 100000,
    "trigger_price_sat": null,
    "triggered_at": null,
    "is_triggered": true,
    "filled_quantity": 0,
    "status": "open",
    "created_at": "2026-04-15T15:30:00Z"
  }
}
```

### cURL: place a stop-limit sell order

```bash
curl -X POST http://localhost:8000/v1/marketplace/orders \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "token_id": "11111111-2222-3333-4444-555555555555",
    "side": "sell",
    "order_type": "stop_limit",
    "quantity": 10,
    "price_sat": 98000,
    "trigger_price_sat": 99000
  }'
```

Important note:

- A dormant stop-limit order will return `is_triggered: false` and will not appear in `GET /orderbook/{token_id}` until its trigger fires.

### cURL: inspect the order book

```bash
curl http://localhost:8000/v1/marketplace/orderbook/11111111-2222-3333-4444-555555555555 \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

Example response:

```json
{
  "token_id": "11111111-2222-3333-4444-555555555555",
  "bids": [
    {"price_sat": 100000, "total_quantity": 13},
    {"price_sat": 99500, "total_quantity": 12}
  ],
  "asks": [
    {"price_sat": 101000, "total_quantity": 10},
    {"price_sat": 102000, "total_quantity": 7}
  ],
  "last_trade_price_sat": 100500,
  "volume_24h": 21
}
```

### cURL: fetch escrow details after a fill

```bash
curl http://localhost:8000/v1/marketplace/escrows/$TRADE_ID \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

Example response:

```json
{
  "escrow": {
    "id": "ecfce1ce-7eab-4b7c-b1d2-2db64997ad16",
    "trade_id": "f7e37b86-11f2-44f2-9f5f-d4e71f920c20",
    "multisig_address": "bcrt1qexamplemultisigaddress",
    "locked_amount_sat": 500000,
    "funding_txid": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd",
    "release_txid": null,
    "status": "funded",
    "expires_at": "2026-04-16T15:30:00Z"
  }
}
```

### cURL: sign escrow release

```bash
curl -X POST http://localhost:8000/v1/marketplace/escrows/$TRADE_ID/sign \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "X-2FA-Code: $TOTP_CODE" \
  -H "Content-Type: application/json" \
  -d '{
    "partial_signature": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
  }'
```

Example response after release:

```json
{
  "escrow": {
    "id": "ecfce1ce-7eab-4b7c-b1d2-2db64997ad16",
    "trade_id": "f7e37b86-11f2-44f2-9f5f-d4e71f920c20",
    "multisig_address": "bcrt1qexamplemultisigaddress",
    "locked_amount_sat": 500000,
    "funding_txid": "cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd",
    "release_txid": "abababababababababababababababababababababababababababababababab",
    "status": "released",
    "expires_at": "2026-04-16T15:30:00Z"
  }
}
```

### cURL: open a dispute

```bash
curl -X POST http://localhost:8000/v1/marketplace/trades/$TRADE_ID/dispute \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "reason": "Counterparty did not complete the off-chain obligations for this trade."
  }'
```

### cURL: resolve a dispute as admin

```bash
curl -X POST http://localhost:8000/v1/marketplace/trades/$TRADE_ID/dispute/resolve \
  -H "Authorization: Bearer $ADMIN_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "resolution": "refund"
  }'
```

### JavaScript `fetch` example for order placement

```javascript
async function placeMarketplaceOrder({ accessToken, tokenId, side, quantity, priceSat, orderType = "limit", triggerPriceSat = null }) {
  const response = await fetch("/v1/marketplace/orders", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      token_id: tokenId,
      side,
      order_type: orderType,
      quantity,
      price_sat: priceSat,
      trigger_price_sat: triggerPriceSat,
    }),
  });

  const payload = await response.json();
  if (!response.ok) {
    throw new Error(payload.error?.message || "Marketplace order placement failed.");
  }
  return payload.order;
}
```

### JavaScript WebSocket example for personal notifications

```javascript
function connectMarketplaceNotifications({ accessToken, resumeToken, onMessage }) {
  const query = new URLSearchParams({ access_token: accessToken });
  if (resumeToken) query.set("resume_token", resumeToken);

  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const socket = new WebSocket(`${protocol}://${location.host}/v1/marketplace/ws/notifications?${query}`);

  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    onMessage(message);
  };

  return socket;
}
```

### Workflow caveat for frontend teams

- `POST /orders` does not return the created trade or escrow when a match happens.
- `GET /orders` and `GET /trades` are global feeds, not personal ledgers.
- In practice, clients should subscribe to `/ws/notifications` before placing orders and persist the `order_id`, `trade_id`, and `resume_token` they receive there.

## 6. Frontend Integration Recommendations

### Recommended UI flows

- Use a trading form that supports both `limit` and `stop_limit` order types, with conditional display of `trigger_price_sat`.
- Subscribe to the notification WebSocket as part of the authenticated session bootstrap so fills and escrow changes arrive without polling.
- Show order placement as a two-step UX: "order accepted" first, then "trade matched" only after a notification arrives.
- Present escrow details as a dedicated trade-status screen because the order response alone does not surface settlement state.
- Expose dispute actions only when the trade is known to be `escrowed` and the current user is one of the trade participants.
- Expose dispute resolution controls only in admin UI.

### Validation and client-side input handling

- Validate positive integer inputs for `quantity`, `price_sat`, and `trigger_price_sat` before submission.
- Require `trigger_price_sat` in the UI only when `order_type === "stop_limit"`.
- Validate `partial_signature` as hex before attempting submission.
- Do not rely on cached wallet or token balances alone for preflight validation; the backend rechecks all balances and reserved commitments.

### Loading states and retries

- Treat `POST /orders`, `POST /escrows/{trade_id}/sign`, and dispute actions as non-idempotent writes. Avoid blind automatic retries.
- Retry `GET /escrows/{trade_id}` and WebSocket reconnects with exponential backoff.
- When a write returns `429`, honor `Retry-After`.
- Use `X-Request-ID` from responses when surfacing support or debugging information.

### Auth and session state

- Attach Bearer tokens to every HTTP request except public operational endpoints.
- Refresh expired access tokens through the auth service and then reconnect the notification WebSocket.
- For `/ws/notifications`, prefer query-param auth only if the environment accepts that risk; otherwise send the token via `Authorization` header or first JSON message when your client library supports it.
- Collect `X-2FA-Code` only when needed for escrow signing and only for users with TOTP enabled.

### Caching guidance

- Cache `GET /orderbook/{token_id}` briefly and refresh it aggressively after local order placement, fills, or price-stream messages.
- Cache `GET /trades` by token and pagination cursor, but expect fast staleness in active markets.
- Always fetch `GET /escrows/{trade_id}` fresh because the handler can mutate state by discovering funding.
- Persist WebSocket `resume_token` after every notification message so reconnects can replay missed events.

### Realtime considerations

- Use `/ws/prices/{token_id}` for lightweight price widgets and order-book summary cards.
- Use `/ws/notifications` for authenticated user workflows because it is the only channel that currently reveals `trade_id` for personal fills.
- Be prepared for out-of-order user experience between REST and WebSocket data. The WebSocket can tell you a fill happened before the client has refetched the latest REST views.

### Security recommendations for frontend consumption

- Treat `release_txid` as an application-level settlement identifier, not proof of an on-chain Bitcoin transaction.
- Never display escrow release as final based only on local optimistic state; wait for the server response or `escrow_released` notification.
- Do not expose admin dispute-resolution controls to non-admin sessions.
- Avoid logging access tokens, `partial_signature`, or TOTP codes in browser diagnostics.

### Anti-patterns to avoid

- Do not assume `GET /orders` returns only the current user's orders.
- Do not assume `GET /trades` returns only the current user's trade history.
- Do not assume a matched order has already moved buyer funds out of the shared wallet ledger.
- Do not assume every settled trade generated treasury fee income; current matching flow leaves `fee_sat` at zero.
- Do not hardcode the older dispute routes from `specs/api-contracts.md` without checking the live service paths.

## 7. Internal Logic and Important Modules

| File | Role | Notes |
| --- | --- | --- |
| `services/marketplace/main.py` | FastAPI app, auth helpers, request handlers, KYC checks, TOTP verification, matching loop, WebSocket protocol | This is where HTTP behavior, error contracts, audit calls, and event publishing are orchestrated |
| `services/marketplace/db.py` | Core persistence and transactional business logic | Owns order creation, matching queries, escrow creation, funding transitions, settlement, treasury writes, and dispute resolution |
| `services/marketplace/schemas.py` | Pydantic request and response models | Defines the public contract types actually exposed by the service |
| `services/marketplace/escrow.py` | Escrow address and key helper logic | Normalizes participant pubkeys, derives compressed pubkeys, builds a P2WSH 2-of-3 witness script, and encodes a bech32 address |
| `services/marketplace/bitcoin_rpc.py` | Bitcoin Core RPC adapter | Wraps `scantxoutset` to discover whether an escrow address has been funded |
| `services/marketplace/README.md` | Short service summary | Useful for intent, but less precise than the code |

### Where the business logic lives

- Order acceptance and match-loop orchestration live in `main.py`.
- Transactional state transitions live in `db.py`.
- Escrow cryptography and address construction live in `escrow.py`.
- Infrastructure adapters live in `bitcoin_rpc.py` and `services/common/*`.

### Core domain logic versus adapters

Core domain logic:

- Validate order-side constraints and trigger semantics.
- Match incoming orders against the best available resting order.
- Lock seller tokens, create trade rows, create escrow rows, and settle or dispute them atomically.
- Enforce KYC thresholds, participant authorization, and admin-only dispute resolution.

Adapters and integrations:

- JWT decoding and TOTP checks.
- Bitcoin Core `scantxoutset` funding scan.
- Redis stream publication and consumption.
- Platform-signature derivation from shared custody helpers.
- Audit, metrics, alerting, and readiness helpers from `services/common`.

### Important implementation details for maintainers

- `settle_trade()` still exists in `db.py`, but the current HTTP flow does not use it. The live path is `create_trade_escrow()` followed later by `process_escrow_signature()` or `resolve_dispute()`.
- Escrow participant pubkeys come from the earliest Nostr identity if available; otherwise they are deterministically derived from wallet custody metadata rather than real spendable wallet keys.
- The platform counter-signature is currently HMAC-based, not a real Bitcoin Schnorr signature.

## 8. Operational Notes

### Port and routing

- Service port: `8003`
- Gateway prefix: `/v1/marketplace`
- Gateway health shortcut: `/health/marketplace`
- Gateway readiness shortcut: `/ready/marketplace`
- Gateway metrics shortcut: `/metrics/marketplace`

### Required environment variables and settings

The service uses the shared `Settings` model, so not every configured variable is equally important to marketplace runtime.

#### Directly used by marketplace logic

| Setting | Why marketplace uses it |
| --- | --- |
| `DATABASE_URL` | Async SQLAlchemy connection for all reads and writes |
| `REDIS_URL` | Redis stream mirror and Redis-backed WebSocket feed |
| `JWT_SECRET` or `JWT_SECRET_FILE` | Local JWT validation for authenticated endpoints |
| `BITCOIN_RPC_HOST`, `BITCOIN_RPC_PORT`, `BITCOIN_RPC_USER`, `BITCOIN_RPC_PASSWORD` or `BITCOIN_RPC_PASSWORD_FILE` | Bitcoin Core funding scans for escrow refresh |
| `BITCOIN_NETWORK` | Network-specific multisig address HRP (`bc`, `tb`, `bcrt`) |
| `KYC_TRADE_THRESHOLD_SAT` | Threshold for KYC enforcement on large orders |
| `CUSTODY_BACKEND` | Controls how the platform signer and escrow key material are derived |
| `WALLET_ENCRYPTION_KEY` or `WALLET_ENCRYPTION_KEY_FILE` | Used indirectly in software-signing mode for platform signing material |
| `CUSTODY_HSM_*` settings | Used when `CUSTODY_BACKEND=hsm` |
| `LOG_LEVEL` | Structured log verbosity |
| `RATE_LIMIT_WINDOW_SECONDS`, `RATE_LIMIT_WRITE_REQUESTS`, `RATE_LIMIT_SENSITIVE_REQUESTS` | Write-rate limiting rules |
| `ALERT_WEBHOOK_URL` or `ALERT_WEBHOOK_URL_FILE` | Optional alert sink for settlement failures |

#### Indirectly required because of shared readiness checks

| Setting group | Why it still matters |
| --- | --- |
| `LND_*` | `GET /ready` checks LND reachability even though marketplace does not call LND directly |
| `TAPD_*` | `GET /ready` checks tapd reachability even though marketplace does not call tapd directly |
| PostgreSQL host and port fields | Used by readiness alongside `DATABASE_URL` |

### External dependencies

| Dependency | Current role |
| --- | --- |
| PostgreSQL | Source of truth for orders, trades, escrows, disputes, wallets, token balances, treasury, users, and token metadata |
| Redis | Event persistence and replay for price and notification streams |
| Bitcoin Core RPC | Optional but functionally important for escrow funding discovery |
| LND | Readiness-only dependency in current marketplace code |
| tapd | Readiness-only dependency in current marketplace code |

### Observability considerations

- Structured JSON logging is configured at startup.
- `GET /metrics` exposes Prometheus-compatible metrics and JSON snapshots.
- Business events emitted by marketplace include `order_place`, `trade_match`, `escrow_fund`, `escrow_release`, `dispute_open`, `dispute_resolve`, and `settlement_failure`.
- Audit events are recorded for order placement, order cancellation, escrow signing, dispute opening, and dispute resolution.
- Settlement failures increment `marketplace_settlement_failures_total` and trigger CRITICAL alerts.

### Security considerations

- Local JWT validation falls back to `dev-secret-change-me` if `JWT_SECRET` is unset in local mode. This is acceptable only for development.
- Two-factor verification is implemented manually in the service with a 30-second TOTP window and no lockout policy.
- The platform escrow signer uses HMAC-based signatures, not Bitcoin-native Schnorr signatures.
- `release_txid` is synthetic and should not be treated as a blockchain confirmation artifact.
- Rate limiting is in-memory and therefore not shared across replicas.
- Because the service writes `wallets` and `token_balances` directly, wallet consistency depends on careful cross-service discipline rather than service-boundary isolation.

## 9. Example End-to-End Flow

### Flow 1: matched trade through escrow release

1. A seller already holds token inventory, usually created by the tokenization service.
2. The seller places a sell order through marketplace.
3. A buyer places a compatible buy order.
4. Marketplace validates both sides, checks KYC if necessary, finds the best resting match, and creates:
   - a `trades` row in `pending` status
   - an `escrows` row in `created` status
   - an immediate decrement of the seller's `token_balances`
5. Marketplace emits `trade.matched` to Redis. The Nostr service may relay it externally, and frontend clients can receive it through `/ws/notifications` or `/ws/prices/{token_id}`.
6. The buyer funds the escrow address off-platform or through future wallet coordination.
7. A client calls `GET /escrows/{trade_id}`. Marketplace scans Bitcoin Core, sees funding, and updates the escrow to `funded` and the trade to `escrowed`.
8. Marketplace emits `escrow.funded` to Redis, and the user notification stream can surface it.
9. Buyer or seller submits `POST /escrows/{trade_id}/sign` with `partial_signature` and, if needed, `X-2FA-Code`.
10. Marketplace adds a platform signature, settles the trade, debits buyer wallet balance, credits seller wallet balance, credits buyer token balance, and emits `escrow.released`.

### Flow 2: dispute and administrative resolution

1. A trade reaches `escrowed` state after funding.
2. Buyer or seller submits `POST /trades/{trade_id}/dispute`.
3. Marketplace marks the trade and escrow as `disputed` and creates a `disputes` row.
4. An admin reviews the case and submits `POST /trades/{trade_id}/dispute/resolve` with either `refund` or `release`.
5. If resolution is `release`, marketplace performs the same balance transfer and buyer token crediting that normal escrow release performs.
6. If resolution is `refund`, marketplace restores the seller's locked token balance and marks escrow `refunded`.
7. Marketplace resolves the dispute row and records an audit event.

## 10. Open Questions / Assumptions

- Current gap: `specs/api-contracts.md` still documents `POST /escrows/{trade_id}/dispute` and `POST /admin/escrows/{trade_id}/resolve`, while the service code exposes trade-prefixed dispute routes.
- Current gap: normal order matching never computes a non-zero `fee_sat`, so treasury fee extraction is structurally present but not active in the main order-placement path.
- Current gap: there is no public endpoint to mark an escrow funded. Funding refresh only happens when the escrow-details route scans Bitcoin Core.
- Current gap: buyer wallet balances are not locked or debited when the escrow is created or funded, only when the trade is released or dispute resolution chooses `release`.
- Current gap: the service exposes no dedicated "my orders", "my trades", or "my escrows" REST endpoints. Clients must combine cached order IDs, trade IDs, and notifications.
- Current gap: the escrow response does not include signature progress, participant pubkeys, or buyer and seller identities.
- Assumption: the business intent is for treasury rows created here to be reported by education and admin surfaces later, because marketplace does not expose treasury APIs itself.
- Assumption: buyer funding of the multisig address may come from outside the wallet-service ledger until a tighter wallet integration is implemented.
- Assumption: readiness checks for LND and tapd are inherited from the shared helper rather than reflecting direct marketplace runtime dependencies.

## 11. Integration Summary

For frontend teams, the most important integration rule is to treat marketplace as a hybrid REST plus realtime service. Use REST for order submission and point-in-time reads, but rely on `/ws/notifications` to learn when an order actually matched and to obtain the `trade_id` needed for escrow follow-up. Always fetch escrow state fresh, parse contract-style errors, and honor rate-limit and 2FA flows.

For backend teams, the key maintenance concern is that marketplace currently owns trading workflows but crosses service boundaries by writing shared wallet, token-balance, and treasury tables directly. Preserve the atomic transaction boundaries in `db.py`, keep audit and metrics emissions intact, and decide explicitly whether to close the current gaps around fee calculation, buyer-fund locking, real Bitcoin signatures, and spec-route drift before wider client adoption.