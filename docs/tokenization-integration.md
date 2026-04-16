# Tokenization Service Integration Guide

This document describes the tokenization service as it is currently implemented in `services/tokenization`, with explicit notes where the broader platform materials describe intended behavior that has not yet been fully realized.

## 1. Service Overview

The tokenization service is the platform boundary for asset onboarding, automated evaluation, and the registration of tokenized real-world assets inside the shared platform database. It accepts seller-submitted asset metadata, runs an in-process heuristic evaluation pass, verifies an already-issued Taproot Asset through `tapd`, and creates the shared token records that downstream marketplace and wallet flows rely on.

### Purpose of the service

- Accept seller-submitted asset metadata for tokenization review.
- Persist an asset lifecycle from initial submission through evaluation and tokenization.
- Produce automated risk, timing, and projected-ROI outputs for submitted assets.
- Verify Taproot Asset issuance details against `tapd` before recording token metadata.
- Seed the seller's initial token balance so the asset can participate in downstream trading flows.
- Publish tokenization-domain events that other services can mirror or consume.

### Main responsibilities

- Asset intake: validate and store asset name, description, category, valuation, and diligence-document URL.
- Evaluation orchestration: queue and execute background asset evaluation, persist scores and analysis, and update asset status.
- Token registration: validate an approved asset against a Taproot Asset ID, capture issuance metadata, and create the `tokens` row.
- Initial balance seeding: create the originating seller's first `token_balances` row.
- Observability and controls: health, readiness, metrics, structured logs, request IDs, audit logs, and rate limiting.
- Event publication: publish `asset.created`, `ai.evaluation.complete`, and `token.minted` events to the shared internal event bus and Redis stream mirrors.

### Business/domain role within the platform

Within the broader platform, the tokenization service is where an off-chain asset description becomes a platform-recognized tokenized instrument. Auth provides identity and seller/admin roles, wallet later surfaces the holdings and yield profile, marketplace turns the issued token into a tradable instrument, and Nostr can broadcast selected tokenization events. Tokenization is the bridge between asset metadata and the shared token records that the rest of the platform uses.

### Why this service exists separately from the others

- Asset onboarding and token issuance have a distinct lifecycle and approval model that does not belong in auth, wallet, or marketplace.
- Taproot Asset verification through `tapd` is a specialized external dependency with different operational and security concerns than ordinary CRUD services.
- The evaluation pipeline has its own state machine, audit needs, and alerting semantics.
- Separating asset-token creation from trading allows marketplace to focus on matching, escrow, and settlement instead of issuance.
- The service creates a clear ownership boundary for `assets` and `tokens`, even though the platform uses a shared database schema.

### Current implementation status

| Area | Currently implemented | Intended platform role |
| --- | --- | --- |
| Asset submission | Persists a pending asset row with metadata and a required HTTP(S) document URL | Seller onboarding for real-world assets |
| Evaluation | Runs an in-process heuristic scorer and stores `ai_score`, `ai_analysis`, and `projected_roi` | AI-assisted diligence and risk scoring |
| Evaluation execution model | Uses `asyncio.create_task()` inside the API process | Durable background processing or worker-backed evaluation |
| Token issuance | Verifies an already-existing Taproot Asset in `tapd` and stores issuance metadata | End-to-end issuance orchestration, potentially including minting |
| Token allocation | Credits the full issued supply to the originating seller in `token_balances` | Initial distribution model for newly tokenized assets |
| Eventing | Publishes internal events and mirrors selected topics to Redis streams | Cross-service async integration |
| Catalog | Lets any authenticated user list and fetch assets | Frontend/investor browsing and seller asset management |
| Debug tapd endpoints | Exposes unauthenticated `/tapd/info` and `/tapd/assets` passthrough endpoints | Internal diagnostics only |

## 2. Service Relationships

The tokenization service mostly interacts with the rest of the platform through shared database tables, shared modules in `services/common`, and Redis-backed event mirroring rather than through direct HTTP calls to sibling services.

### Relationships with other platform services

| Service | Purpose of interaction | Interaction type | Current implementation |
| --- | --- | --- | --- |
| `services/auth` | Access-token trust, user existence checks, and seller/admin role enforcement | Authentication dependency and shared database access | Tokenization decodes JWTs locally using the shared `JWT_SECRET`, reads `users`, rejects deleted users, and enforces role/ownership rules from token claims plus DB state |
| `services/wallet` | Downstream token holdings, valuation, and yield reporting | Shared database access by downstream service | Tokenization does not call wallet directly, but wallet reads `assets`, `tokens`, and `token_balances` created by tokenization |
| `services/marketplace` | Downstream trading of issued tokens and seller-facing realtime evaluation notifications | Shared database access and event-driven communication | Tokenization does not call marketplace directly, but marketplace reads `tokens` and `token_balances`, and marketplace realtime feeds can surface `ai.evaluation.complete` |
| `services/nostr` | External publication of selected tokenization events | Event-driven communication | Nostr listens to mirrored Redis streams for `asset.created` and `ai.evaluation.complete` and maps them into signed Nostr events |
| `services/gateway` | Public entrypoint routing | Direct API exposure through reverse proxy | Nginx routes `/v1/tokenization/*` to the tokenization service on port `8002` and exposes health/readiness/metrics shortcuts |
| `services/admin` | No direct runtime dependency today | None currently | Admin does not call tokenization directly; any tokenization-related reporting happens elsewhere through shared data |
| `services/education` | No direct runtime dependency today | None currently | Tokenization does not read education data or call education endpoints |

### Dependencies on `services/common`

| Shared module | Purpose | Interaction type | Current implementation |
| --- | --- | --- | --- |
| `common.config` | Shared settings model and secret resolution | Infrastructure/shared module dependency | Provides DB, Redis, tapd, auth, logging, alerting, and platform-wide service URL settings |
| `common.db.metadata` | Canonical SQLAlchemy table definitions | Infrastructure/shared module dependency | Tokenization imports `users`, `assets`, `tokens`, and `token_balances` from the shared metadata module |
| `common.security` | Request IDs and write-rate limiting | Infrastructure/shared module dependency | Installs request ID middleware and rate limits writes, with `/assets` paths treated as sensitive |
| `common.logging` | Structured logging and sensitive-data redaction | Infrastructure/shared module dependency | Configures service-level logging with shared redaction behavior |
| `common.metrics` | Request instrumentation and business-event metrics | Infrastructure/shared module dependency | Mounts `GET /metrics` and records business events such as `asset_submit`, `asset_evaluation_request`, `asset_evaluation_complete`, and `asset_tokenize` |
| `common.alerting` | Operational alerts | Infrastructure/shared module dependency | Background evaluation failures trigger a CRITICAL alert |
| `common.audit` | Audit trail persistence | Infrastructure/shared module dependency | Asset submission, evaluation requests, and tokenization create audit records |
| `common.readiness` | Dependency readiness checks | Infrastructure/shared module dependency | `GET /ready` checks PostgreSQL, Redis, Bitcoin Core, LND, and tapd over TCP |
| `common.events` | Internal event bus plus Redis stream mirroring | Infrastructure/shared module dependency | Tokenization publishes internal events and mirrors selected topics to Redis streams |

### External and infrastructure relationships

| Dependency | Purpose | Interaction type | Current implementation |
| --- | --- | --- | --- |
| PostgreSQL | Source of truth for asset, token, balance, and auth-linked user data | Direct database dependency | Used heavily for `assets`, `tokens`, `token_balances`, and `users` lookups |
| tapd | Taproot Asset lookup and metadata retrieval | Direct gRPC integration | `services/tokenization/tapd_client.py` uses `FetchAsset`, `FetchAssetMeta`, `GetInfo`, and `ListAssets` |
| Redis | Event-stream mirroring and readiness check | Event infrastructure and readiness dependency | `asset.created` and `ai.evaluation.complete` are subscribed to Redis stream mirrors via `RedisStreamMirror`; `token.minted` is published through the same bus, but no stream-mirror subscription is configured in this service |
| Bitcoin Core | Readiness-only dependency | Infrastructure dependency | Checked by readiness, but tokenization handlers do not call Bitcoin RPCs |
| LND | Readiness-only dependency | Infrastructure dependency | Checked by readiness, but tokenization handlers do not call LND |

### Internal events emitted by tokenization

| Topic | When it is emitted | Payload purpose | Downstream usage in current codebase |
| --- | --- | --- | --- |
| `asset.created` | After successful asset submission | Announces a new pending asset | Mirrored to Redis streams and consumed by Nostr |
| `ai.evaluation.complete` | After background evaluation persists results | Announces approved/rejected outcome with score and analysis summary | Mirrored to Redis streams, consumed by Nostr, and exposed through marketplace realtime notifications |
| `token.minted` | After successful tokenization | Announces token record creation and supply metadata | Published by tokenization; no direct runtime consumer is visible in other service code today |

### Notes on absent direct service-to-service calls

- Tokenization does not call sibling microservices over HTTP.
- Cross-domain behavior is mostly achieved through shared tables and Redis-backed event streams.
- JWT verification is local to the service rather than delegated back to auth.
- The service publishes events but does not subscribe to or consume other services' events.

## 3. Database Documentation

All table definitions live in `services/common/db/metadata.py`, and migrations are platform-wide Alembic revisions. The ownership labels below are operational ownership labels, not isolated schema ownership.

**Assumption:** `assets` and `tokens` are operationally owned by the tokenization service even though they live in a shared schema module.

### Tokenization-owned and tokenization-driven tables

| Table | Ownership | Purpose in tokenization service | Important fields and constraints | Relationships |
| --- | --- | --- | --- | --- |
| `assets` | Tokenization-owned | Primary asset onboarding and evaluation record | `id` UUID PK; `owner_id` FK to `users`; `category` constrained to `real_estate`, `commodity`, `invoice`, `art`, or `other`; `status` constrained to `pending`, `evaluating`, `approved`, `rejected`, `tokenized`; `ai_score` constrained to `0-100` when present | Many-to-one to `users`; joined to `tokens` when returning asset detail |
| `tokens` | Tokenization-owned | Platform token record linked to a tokenized asset | `id` UUID PK; `asset_id` FK to `assets`; unique `taproot_asset_id`; `metadata` JSONB for issuance details; `minted_at` timestamp | Many-to-one to `assets`; referenced by `token_balances`, `orders`, `trades`, and `yield_accruals` |
| `token_balances` | Shared, tokenization-driven | Holder balance table seeded at tokenization time | Unique `(user_id, token_id)`; `balance >= 0` | FKs to `users` and `tokens`; initial seller allocation written by tokenization; later updated by marketplace and read by wallet |

### Shared tables read by the tokenization service

| Table | Primary owner | Purpose in tokenization service | Important fields and constraints | Relationships |
| --- | --- | --- | --- | --- |
| `users` | Auth-owned | Validates that token callers still exist and captures role/deleted state | `role` constrained to `user`, `seller`, `admin`, `auditor`; `deleted_at` soft-delete field | Referenced by `assets.owner_id`; consulted during auth dependency resolution |

### Table details and inferred usage

#### `assets`

- Created by `create_asset()` in `services/tokenization/db.py` with `status = pending`.
- Evaluations update `ai_score`, `ai_analysis`, `projected_roi`, `status`, and `updated_at` through `complete_asset_evaluation()`.
- `begin_asset_evaluation()` only allows transitions from `pending`, `approved`, or `rejected` into `evaluating`.
- API-level validation requires a non-empty name and description, a positive `valuation_sat`, and an HTTP(S) `documents_url`.
- **Important implementation note:** the database allows `documents_url` to be `NULL`, but the public create endpoint currently requires it.
- **Important implementation note:** there is no database check constraint enforcing `valuation_sat > 0`; that rule currently lives at the API layer.

#### `tokens`

- Created by `create_asset_token()` only after the asset is in `approved` status and a Taproot Asset lookup succeeds.
- `metadata` stores the converted `tapd` asset and asset-meta payloads, including genesis details, anchor data, script/group keys, and the raw meta reveal.
- `taproot_asset_id` is the on-chain asset ID from `tapd`, not the internal token row ID.
- `create_asset_token()` updates the asset status to `tokenized` and inserts the token row in the same database transaction.
- **Important implementation note:** the service treats one token per asset as a lifecycle invariant, but there is no database-level unique constraint on `tokens.asset_id`.
- **Important implementation note:** there are no DB-level positive checks for `total_supply`, `circulating_supply`, or `unit_price_sat`; those are enforced by the request schema and route logic.

#### `token_balances`

- The tokenization service writes exactly one balance row during tokenization: the originating seller receives the full `circulating_supply`.
- In the live route implementation, `circulating_supply` is passed as the full issued supply, so the seller begins with all fractions and all issued units are treated as circulating immediately.
- Marketplace later debits and credits this table during trading flows.
- Wallet reads this table to render portfolio balances and calculate value/yield.

#### `users`

- Tokenization auth checks `users.deleted_at` and rejects deleted users even if their JWT still decodes.
- The role from JWT claims is used for authorization, but the service also fetches the user row so deleted accounts cannot keep acting.
- `assets.owner_id` points back to `users.id`.

### Relevant Alembic migrations

| Migration | Why it matters to tokenization |
| --- | --- |
| `20260413_1330_0002_remaining_schema_tables.py` | Creates `assets`, `tokens`, and `token_balances` |
| `20260413_1800_0003_align_domain_schema_constraints.py` | Renames `tokens.metadata_json` to `metadata` and adds the key `assets` and `token_balances` check constraints |
| `20260413_1830_0004_normalize_check_constraint_names.py` | Normalizes the generated check-constraint names for `assets` and `token_balances` |

### Assumptions and limitations

- Table ownership is inferred from service behavior; the database itself is shared across services.
- The tokenization service does not directly use marketplace tables such as `orders` or `trades`, even though those tables reference `tokens` downstream.
- The service treats `assets -> tokens` as effectively one-to-one, but that relationship is enforced operationally rather than through a unique DB constraint on `asset_id`.

## 4. API Endpoints

### Path conventions

- The paths below are the tokenization service's internal route paths.
- Through the gateway, these routes are normally reachable under `/v1/tokenization/<internal-path-without-leading-slash>`.
- This means internal `GET /assets` becomes gateway `GET /v1/tokenization/assets`.
- Operational gateway shortcuts also exist for `GET /health/tokenization`, `GET /ready/tokenization`, and `GET /metrics/tokenization`.

### Error response conventions

- Most business handlers use the platform `ContractError` response shape:

```json
{
  "error": {
    "code": "string_slug",
    "message": "Human-readable description"
  }
}
```

- Validation failures include a `details` array:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request payload failed validation.",
    "details": [
      {
        "field": "documents_url",
        "message": "Field required"
      }
    ]
  }
}
```

- The tapd debugging endpoints still return a simpler error shape on failure:

```json
{
  "error": "Failed to connect to tapd",
  "detail": "Human-readable exception"
}
```

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
  "service": "tokenization",
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
  "service": "tokenization",
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

#### `GET /tapd/info`

- Purpose: return the raw `tapd` `GetInfo` response for diagnostics.
- Authentication: none.
- Query parameters: none.
- Request body: none.
- Response schema:
  - JSON object produced by converting the `tapd` `GetInfo` protobuf response with `MessageToDict`.
  - The exact fields depend on the connected `tapd` version and should be treated as operational/debug output, not a stable public contract.
- Possible error responses:
  - `500` with `{"error": "Failed to connect to tapd", "detail": "..."}`.

#### `GET /tapd/assets`

- Purpose: return the raw `tapd` `ListAssets` response for diagnostics.
- Authentication: none.
- Query parameters: none.
- Request body: none.
- Response schema:
  - JSON object produced by converting the `tapd` `ListAssets` protobuf response with `MessageToDict`.
  - The exact fields depend on the connected `tapd` version and should be treated as operational/debug output, not a stable integration contract.
- Possible error responses:
  - `500` with `{"error": "Failed to list assets from tapd", "detail": "..."}`.

### 4.2 Asset submission and catalog endpoints

#### `POST /assets`

- Purpose: submit a new asset for tokenization review.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: caller must have role `seller` or `admin`.
- Query parameters: none.
- Request body schema:

```json
{
  "name": "Downtown Office Building",
  "description": "3-story commercial office building in the central business district.",
  "category": "real_estate",
  "valuation_sat": 100000000,
  "documents_url": "https://storage.example.com/docs/abc123"
}
```

- Request validation rules:
  - `name` is required, stripped, and must be 1 to 200 characters after trimming.
  - `description` is required, stripped, and must not be blank.
  - `category` must be one of `real_estate`, `commodity`, `invoice`, `art`, or `other`.
  - `valuation_sat` must be an integer greater than zero.
  - `documents_url` must be a valid HTTP or HTTPS URL.
- Response schema:

```json
{
  "asset": {
    "id": "uuid",
    "owner_id": "uuid",
    "name": "Downtown Office Building",
    "description": "3-story commercial office building in the central business district.",
    "category": "real_estate",
    "valuation_sat": 100000000,
    "documents_url": "https://storage.example.com/docs/abc123",
    "status": "pending",
    "created_at": "2026-04-15T12:00:00Z",
    "updated_at": "2026-04-15T12:00:00Z"
  }
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` when credentials are missing.
  - `401` with platform `error.code = invalid_token` when the access token is invalid, expired, or belongs to a deleted user.
  - `403` with platform `error.code = forbidden` when the caller is not a `seller` or `admin`.
  - `422` with platform `error.code = validation_error` for invalid or missing fields.
  - `429` with platform `error.code = rate_limit_exceeded` when write-rate limits are exceeded.

**Important implementation note:** the created asset is always owned by the authenticated principal. There is no admin-only override for creating assets on behalf of another seller.

#### `GET /assets`

- Purpose: list assets with optional filtering and cursor pagination.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Query parameters:
  - `status`: optional asset status filter.
  - `category`: optional category filter.
  - `cursor`: optional asset UUID used as a pagination cursor.
  - `limit`: optional integer from `1` to `100`, default `20`.
- Request body: none.
- Response schema:

```json
{
  "assets": [
    {
      "id": "uuid",
      "owner_id": "uuid",
      "name": "Downtown Office Building",
      "description": "3-story commercial office building in the central business district.",
      "category": "real_estate",
      "valuation_sat": 100000000,
      "documents_url": "https://storage.example.com/docs/abc123",
      "status": "approved",
      "created_at": "2026-04-15T12:00:00Z",
      "updated_at": "2026-04-15T12:05:00Z"
    }
  ],
  "next_cursor": "uuid_or_null"
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` when credentials are missing.
  - `401` with platform `error.code = invalid_token` when the access token is invalid, expired, or belongs to a deleted user.
  - `400` with platform `error.code = invalid_cursor` when the cursor is malformed or does not exist inside the filtered result set.
  - `422` with platform `error.code = validation_error` for invalid filter values or invalid `limit`.

**Important implementation notes:**

- Any authenticated user can browse the full asset catalog; the endpoint is not seller-scoped.
- Pagination is implemented after loading the filtered rows from the database and sorting them by `created_at DESC, id DESC`.
- The cursor must reference an asset that exists inside the filtered result set, not merely any valid UUID.

#### `GET /assets/{asset_id}`

- Purpose: return one asset with optional evaluation and tokenization details.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization: any authenticated user.
- Path parameters:
  - `asset_id`: UUID of the asset.
- Request body: none.
- Response schema:

```json
{
  "asset": {
    "id": "uuid",
    "owner_id": "uuid",
    "name": "Downtown Office Building",
    "description": "3-story commercial office building in the central business district.",
    "category": "real_estate",
    "valuation_sat": 100000000,
    "documents_url": "https://storage.example.com/docs/abc123",
    "ai_score": 78.5,
    "ai_analysis": {
      "model_version": "heuristic-v1",
      "risk_level": "moderate",
      "market_timing": "favorable",
      "projected_roi_annual": 8.8,
      "summary": "The real estate submission scored 78.50/100 with moderate risk..."
    },
    "projected_roi": 8.8,
    "status": "tokenized",
    "created_at": "2026-04-15T12:00:00Z",
    "updated_at": "2026-04-15T12:10:00Z",
    "token": {
      "id": "uuid",
      "taproot_asset_id": "64_hex_chars",
      "total_supply": 1000,
      "circulating_supply": 1000,
      "unit_price_sat": 100000,
      "issuance_metadata": {
        "asset_id": "64_hex_chars",
        "asset_name": "Downtown Office Building"
      },
      "minted_at": "2026-04-15T12:10:00Z"
    }
  }
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` when credentials are missing.
  - `401` with platform `error.code = invalid_token` when the access token is invalid, expired, or belongs to a deleted user.
  - `404` with platform `error.code = asset_not_found` when the asset does not exist.
  - `422` with platform `error.code = validation_error` for malformed UUID path parameters.

**Important implementation note:** fields with `null` values are omitted from the response because the handler serializes with `exclude_none=True`. That means `ai_score`, `ai_analysis`, `projected_roi`, and `token` are absent until they exist.

### 4.3 Evaluation and tokenization endpoints

#### `POST /assets/{asset_id}/evaluate`

- Purpose: request automated evaluation for an owned asset.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization:
  - caller must have role `seller` or `admin`;
  - caller must also own the asset.
- Path parameters:
  - `asset_id`: UUID of the asset.
- Request body: none.
- Response schema:

```json
{
  "message": "Evaluation started",
  "estimated_completion": "2026-04-15T12:05:00Z"
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` when credentials are missing.
  - `401` with platform `error.code = invalid_token` when the access token is invalid, expired, or belongs to a deleted user.
  - `403` with platform `error.code = forbidden` when the caller is not a `seller` or `admin`, or when the caller does not own the asset.
  - `404` with platform `error.code = asset_not_found` when the asset does not exist.
  - `409` with platform `error.code = asset_state_conflict` when the asset is already evaluating, already tokenized, or changed state before the update could be persisted.
  - `500` with platform `error.code = evaluation_dispatch_failed` when the service cannot start the background task.
  - `422` with platform `error.code = validation_error` for malformed UUID path parameters.
  - `429` with platform `error.code = rate_limit_exceeded` when write-rate limits are exceeded.

**Important implementation notes:**

- The evaluation is not delegated to an external worker queue. It runs as an in-process background task created with `asyncio.create_task()`.
- The current evaluator is deterministic heuristic logic from `services/tokenization/evaluation.py`, not an external ML or LLM call.
- Assets in `pending`, `approved`, or `rejected` status can be re-evaluated. Only `evaluating` and `tokenized` are blocked.
- The response always estimates completion as `now + 5 minutes`, even though the actual evaluation logic is synchronous once the background task runs.
- On evaluation failure, the service fires a CRITICAL alert and attempts to restore the asset's previous status.

#### `POST /assets/{asset_id}/tokenize`

- Purpose: register an approved asset as a tokenized instrument backed by an existing Taproot Asset.
- Authentication: Bearer JWT via `Authorization` header.
- Authorization:
  - caller must have role `seller` or `admin`;
  - caller must also own the asset.
- Path parameters:
  - `asset_id`: UUID of the asset.
- Request body schema:

```json
{
  "taproot_asset_id": "64_hex_chars",
  "total_supply": 1000,
  "unit_price_sat": 100000
}
```

- Request validation rules:
  - `taproot_asset_id` is normalized to lowercase hex and must be exactly 64 hex characters.
  - `total_supply` must be an integer greater than zero.
  - `unit_price_sat` must be an integer greater than zero.
- Preconditions:
  - the asset must exist;
  - the caller must own it;
  - the asset must be in `approved` status;
  - no token must already be attached through the service lifecycle;
  - `tapd` must return a matching asset ID;
  - the Taproot Asset amount must equal `total_supply`.
- Response schema:

```json
{
  "asset": {
    "id": "uuid",
    "owner_id": "uuid",
    "name": "Downtown Office Building",
    "description": "3-story commercial office building in the central business district.",
    "category": "real_estate",
    "valuation_sat": 100000000,
    "documents_url": "https://storage.example.com/docs/abc123",
    "ai_score": 78.5,
    "ai_analysis": {
      "model_version": "heuristic-v1",
      "risk_level": "moderate"
    },
    "projected_roi": 8.8,
    "status": "tokenized",
    "created_at": "2026-04-15T12:00:00Z",
    "updated_at": "2026-04-15T12:10:00Z",
    "token": {
      "id": "uuid",
      "taproot_asset_id": "64_hex_chars",
      "total_supply": 1000,
      "circulating_supply": 1000,
      "unit_price_sat": 100000,
      "issuance_metadata": {
        "asset_id": "64_hex_chars",
        "asset_name": "Downtown Office Building",
        "asset_type": "normal",
        "genesis_point": "txid:vout",
        "meta_hash": "64_hex_chars",
        "output_index": 0,
        "script_key": "64_hex_chars",
        "group_key": "64_hex_chars",
        "anchor_outpoint": "txid:vout",
        "anchor_block_hash": "64_hex_chars",
        "anchor_block_height": 144,
        "meta_reveal": {
          "type": "opaque",
          "data": "{\"issuer\":\"tapd\"}"
        }
      },
      "minted_at": "2026-04-15T12:10:00Z"
    }
  }
}
```

- Possible error responses:
  - `401` with platform `error.code = authentication_required` when credentials are missing.
  - `401` with platform `error.code = invalid_token` when the access token is invalid, expired, or belongs to a deleted user.
  - `403` with platform `error.code = forbidden` when the caller is not a `seller` or `admin`, or when the caller does not own the asset.
  - `404` with platform `error.code = asset_not_found` when the asset does not exist.
  - `404` with platform `error.code = taproot_asset_not_found` when `tapd` cannot find the provided asset ID.
  - `409` with platform `error.code = asset_state_conflict` when the asset is not approved, is already tokenized, or changes state during token creation.
  - `409` with platform `error.code = taproot_asset_mismatch` when `tapd` returns an asset ID different from the requested one.
  - `409` with platform `error.code = taproot_supply_mismatch` when the Taproot Asset amount does not equal the requested `total_supply`.
  - `502` with platform `error.code = taproot_lookup_failed` when `tapd` lookup fails unexpectedly.
  - `422` with platform `error.code = validation_error` for malformed UUIDs or invalid request fields.
  - `429` with platform `error.code = rate_limit_exceeded` when write-rate limits are exceeded.

**Current implementation vs intended behavior:** despite the route name, the service does not mint a new Taproot Asset itself. It verifies an already-issued asset in `tapd`, stores the issuance snapshot in `tokens.metadata`, sets the asset status to `tokenized`, and seeds the seller's balance.

### 4.4 Spec-to-implementation differences that affect integrations

The broader project materials and the live implementation diverge in a few places that matter for integrators.

| Area | Higher-level description | Current implementation |
| --- | --- | --- |
| Evaluation engine | Service README describes "AI/ML model assessment" | Code uses deterministic heuristic scoring in `evaluation.py`; no external model call is made |
| Taproot issuance | Service README implies the service mints Taproot Assets | Code only verifies a pre-existing Taproot Asset and records it |
| Submit role examples | Platform docs often describe seller-only submission | The live route allows both `seller` and `admin`, but ownership always remains the caller |
| Tapd diagnostics | Public contract sections focus on asset lifecycle endpoints | Live service also exposes unauthenticated `/tapd/info` and `/tapd/assets` debug endpoints |
| `documents_url` nullability | Database allows `NULL` | Public create API requires a valid HTTP(S) URL |

Until the implementation and broader platform narratives are reconciled, integrators should follow the live behavior in `services/tokenization/main.py`, `services/tokenization/schemas.py`, and `services/tokenization/tapd_client.py`.

## 5. How to Use the Endpoints

### Prerequisites

- Obtain an access token from the auth service.
- Send it as `Authorization: Bearer <access-token>` on all authenticated requests.
- Use a `seller` or `admin` account for asset submission, evaluation, and tokenization.
- Before calling `POST /assets/{asset_id}/tokenize`, ensure the Taproot Asset already exists in `tapd` and that you know its 32-byte asset ID.
- Expect direct-service examples below to use `http://localhost:8002` for clarity.
- Frontend applications typically go through the gateway prefix instead.

### Example: submit an asset

```bash
curl -X POST "http://localhost:8002/assets" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Downtown Office Building",
    "description": "3-story commercial office building in the central business district.",
    "category": "real_estate",
    "valuation_sat": 100000000,
    "documents_url": "https://storage.example.com/docs/abc123"
  }'
```

Example response:

```json
{
  "asset": {
    "id": "f6eb2bb8-babc-4254-af56-e0e4d361b305",
    "owner_id": "3d3a8472-d8f1-49d2-94d2-2ef4c545d2ca",
    "name": "Downtown Office Building",
    "description": "3-story commercial office building in the central business district.",
    "category": "real_estate",
    "valuation_sat": 100000000,
    "documents_url": "https://storage.example.com/docs/abc123",
    "status": "pending",
    "created_at": "2026-04-15T12:00:00Z",
    "updated_at": "2026-04-15T12:00:00Z"
  }
}
```

### Example: list assets from a frontend through the gateway

```js
const response = await fetch('/v1/tokenization/assets?status=approved&limit=20', {
  headers: {
    Authorization: `Bearer ${accessToken}`,
  },
});

const payload = await response.json();
if (!response.ok) {
  throw payload.error ?? payload.detail;
}

console.log(payload.assets, payload.next_cursor);
```

### Example: fetch a single asset detail record

```bash
curl -X GET "http://localhost:8002/assets/${ASSET_ID}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Example response after approval but before tokenization:

```json
{
  "asset": {
    "id": "f6eb2bb8-babc-4254-af56-e0e4d361b305",
    "owner_id": "3d3a8472-d8f1-49d2-94d2-2ef4c545d2ca",
    "name": "Downtown Office Building",
    "description": "3-story commercial office building in the central business district.",
    "category": "real_estate",
    "valuation_sat": 100000000,
    "documents_url": "https://storage.example.com/docs/abc123",
    "ai_score": 78.5,
    "ai_analysis": {
      "model_version": "heuristic-v1",
      "risk_level": "moderate",
      "market_timing": "favorable",
      "projected_roi_annual": 8.8,
      "summary": "The real estate submission scored 78.50/100 with moderate risk..."
    },
    "projected_roi": 8.8,
    "status": "approved",
    "created_at": "2026-04-15T12:00:00Z",
    "updated_at": "2026-04-15T12:04:10Z"
  }
}
```

### Example: request evaluation

```bash
curl -X POST "http://localhost:8002/assets/${ASSET_ID}/evaluate" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

Example response:

```json
{
  "message": "Evaluation started",
  "estimated_completion": "2026-04-15T12:05:00Z"
}
```

### Example: poll until evaluation completes

The service does not expose a job-status endpoint. Poll the asset record until `status` changes from `evaluating` to `approved` or `rejected`.

```bash
curl -X GET "http://localhost:8002/assets/${ASSET_ID}" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

### Example: tokenize an approved asset

```bash
curl -X POST "http://localhost:8002/assets/${ASSET_ID}/tokenize" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "taproot_asset_id": "abababababababababababababababababababababababababababababababab",
    "total_supply": 1000,
    "unit_price_sat": 100000
  }'
```

Example response:

```json
{
  "asset": {
    "id": "f6eb2bb8-babc-4254-af56-e0e4d361b305",
    "owner_id": "3d3a8472-d8f1-49d2-94d2-2ef4c545d2ca",
    "name": "Downtown Office Building",
    "description": "3-story commercial office building in the central business district.",
    "category": "real_estate",
    "valuation_sat": 100000000,
    "documents_url": "https://storage.example.com/docs/abc123",
    "ai_score": 78.5,
    "ai_analysis": {
      "model_version": "heuristic-v1",
      "risk_level": "moderate"
    },
    "projected_roi": 8.8,
    "status": "tokenized",
    "created_at": "2026-04-15T12:00:00Z",
    "updated_at": "2026-04-15T12:10:00Z",
    "token": {
      "id": "f42cdfe5-5f7c-4679-b877-c98dfdf5c425",
      "taproot_asset_id": "abababababababababababababababababababababababababababababababab",
      "total_supply": 1000,
      "circulating_supply": 1000,
      "unit_price_sat": 100000,
      "issuance_metadata": {
        "asset_id": "abababababababababababababababababababababababababababababababab",
        "asset_name": "Downtown Office Building",
        "anchor_block_height": 144
      },
      "minted_at": "2026-04-15T12:10:00Z"
    }
  }
}
```

### Example: inspect tapd connectivity

```bash
curl -X GET "http://localhost:8002/tapd/info"
```

This endpoint is primarily for diagnostics. Frontend applications should normally not call it directly.

### Common workflows

#### Seller asset onboarding workflow

1. Authenticate through auth and obtain an access token for a `seller` or `admin` account.
2. Call `POST /assets` with asset metadata and a document URL.
3. Persist the returned `asset.id` in frontend state.
4. Call `POST /assets/{asset_id}/evaluate` when the seller is ready for automated screening.
5. Poll `GET /assets/{asset_id}` until `status` becomes `approved` or `rejected`.
6. If the asset is approved and the corresponding Taproot Asset already exists in `tapd`, call `POST /assets/{asset_id}/tokenize`.
7. After tokenization, use the returned token metadata or downstream marketplace/wallet screens to continue the user journey.

#### Investor catalog workflow

1. Authenticate the user through auth.
2. Call `GET /assets` with optional `status` and `category` filters.
3. Use `next_cursor` to paginate through older results.
4. Open `GET /assets/{asset_id}` for details, including AI analysis and token metadata once available.

#### Evaluation-notification workflow

1. Seller starts evaluation with `POST /assets/{asset_id}/evaluate`.
2. Tokenization sets the asset status to `evaluating` and launches an in-process background task.
3. On completion, tokenization writes `ai_score`, `ai_analysis`, `projected_roi`, and final status back to `assets`.
4. Tokenization publishes `ai.evaluation.complete` to the internal bus and Redis mirror.
5. Marketplace realtime notifications and Nostr publishing can consume that event downstream.

## 6. Frontend Integration Recommendations

### Auth, session state, and token refresh

- Treat tokenization as a JWT-protected service. Obtain and refresh access tokens through the auth service.
- Send `Authorization: Bearer <access-token>` on every authenticated request.
- Retry idempotent reads such as `GET /assets` and `GET /assets/{asset_id}` after a token refresh when appropriate.
- Do not automatically replay `POST /assets`, `POST /assets/{asset_id}/evaluate`, or `POST /assets/{asset_id}/tokenize` after token refresh or transient failures without explicit user confirmation.
- Frontend authorization rules should match backend behavior: seller/admin for writes, any authenticated user for reads.

### Suggested UI flows

- Seller submission form: collect asset metadata plus an HTTP(S) diligence document URL, then call `POST /assets`.
- Seller asset detail screen: call `GET /assets/{asset_id}` and show submission status, evaluation output, and tokenization readiness.
- Seller evaluation action: display a primary action that triggers `POST /assets/{asset_id}/evaluate`, then show an `evaluating` pending state.
- Seller tokenization action: only enable once the asset status is `approved`, and require the seller to supply the existing Taproot Asset ID, total supply, and unit price.
- Investor browse screen: use `GET /assets` with filters and cursor pagination.
- Token detail screen: use `GET /assets/{asset_id}` and show token metadata only when `token` is present.

### Validation, loading states, retries, and error handling

- Validate category choices client-side using the backend enum values.
- Validate `documents_url` as HTTP(S); the backend will reject invalid or non-HTTP URLs.
- Trim name and description inputs before submit so the user sees the same blank-field behavior as the backend.
- Validate `taproot_asset_id` as a 64-character hex string before calling `POST /assets/{asset_id}/tokenize`.
- Enforce positive integers for `valuation_sat`, `total_supply`, and `unit_price_sat` in the UI.
- Show clear loading states during evaluation requests and tokenization requests; both are state transitions, not pure reads.
- Treat `409 asset_state_conflict` responses as meaningful business-state feedback rather than generic failures.
- Parse both the platform `error.code/message` structure and the simpler `error/detail` shape used by the tapd debug endpoints.
- Surface `429 rate_limit_exceeded` errors as retry-later guidance rather than hard failures.

### Caching guidance

| Resource | Cache guidance | Why |
| --- | --- | --- |
| `GET /assets` | Short-lived cache per filter set, or explicit user refresh | Assets change as sellers submit, evaluate, and tokenize them |
| `GET /assets/{asset_id}` | Fetch fresh during evaluation and tokenization flows | Status and analysis fields can change quickly |
| `POST /assets` | Never cache | Creates a new asset record |
| `POST /assets/{asset_id}/evaluate` | Never cache | Triggers an evaluation state transition |
| `POST /assets/{asset_id}/tokenize` | Never cache | Triggers token registration and initial balance seeding |
| `GET /tapd/info` and `GET /tapd/assets` | Avoid frontend caching altogether | These are operational/debug endpoints, not stable UI resources |

### Real-time and event-driven considerations

- The tokenization service does not expose WebSocket or SSE endpoints.
- If the platform wants near-real-time evaluation completion, poll `GET /assets/{asset_id}` or integrate with downstream realtime surfaces that already consume `ai.evaluation.complete`, such as marketplace notifications.
- Nostr publication is downstream of Redis stream mirroring; frontend teams should not assume Nostr delivery as a replacement for direct API state reads.

### Security recommendations for frontend consumption

- Gate write actions by account role in the UI, but continue to rely on backend authorization as the source of truth.
- Treat asset descriptions and document URLs as untrusted user input when rendering them.
- Avoid exposing the tapd diagnostic endpoints in normal end-user interfaces.
- Do not imply that `approved` means legal, regulatory, or human approval; the current evaluator is heuristic and automated.
- Do not present `POST /assets/{asset_id}/tokenize` as a mint operation. The seller must already have a valid Taproot Asset ID from outside this API.

### Frontend anti-patterns to avoid

- Do not assume `GET /assets` is scoped to the current seller; it is a platform-wide authenticated catalog.
- Do not assume the `estimated_completion` timestamp returned by evaluation is authoritative or exact.
- Do not auto-retry tokenization after network failures without explicit confirmation; state may already have changed.
- Do not hide `409` or `422` responses behind generic error messages; they often explain exactly what state or field is wrong.
- Do not treat `token` as always present in `GET /assets/{asset_id}` responses.

## 7. Internal Logic and Important Modules

| File | Role | Notes |
| --- | --- | --- |
| `services/tokenization/main.py` | FastAPI app, auth orchestration, route handlers, background task dispatch, event publishing, tapd metadata shaping | Most request orchestration lives here |
| `services/tokenization/db.py` | Async DB helpers | Handles asset creation, lifecycle transitions, asset lookup, listing, and token creation |
| `services/tokenization/schemas.py` | Pydantic request and response models | Defines asset create, list, detail, evaluation response, and tokenization request/response shapes |
| `services/tokenization/evaluation.py` | Heuristic evaluation engine | Computes score, projected ROI, strengths/concerns, risk level, and final approved/rejected status |
| `services/tokenization/tapd_client.py` | tapd gRPC adapter | Wraps TLS + macaroon setup and the `GetInfo`, `ListAssets`, `FetchAsset`, and `FetchAssetMeta` calls |
| `services/tokenization/tapd_grpc/` | Generated gRPC stubs | Vendored Taproot Assets protobuf bindings |
| `services/tokenization/README.md` | Short service summary | Useful as a high-level overview, but less precise than the live code |
| `services/tokenization/events.py` | Service-local event-bus helper copy | Present in the service tree, but the live app imports the shared `common.events` implementation instead |

### Where the business logic lives

- Route-level orchestration and lifecycle checks live in `main.py`.
- Persistence and state transitions live in `db.py`.
- Evaluation rules live in `evaluation.py`.
- Taproot Asset lookup and transport concerns live in `tapd_client.py`.

### Core domain logic vs adapters/integrations

- Core tokenization domain logic:
  - asset submission and status transitions,
  - evaluation scheduling and result persistence,
  - token registration and initial balance seeding.
- Domain helpers:
  - `evaluate_asset_submission()` computes heuristic AI-like outputs,
  - `_build_taproot_issuance_metadata()` converts protobuf responses into JSON-safe metadata.
- Adapters/integrations:
  - JWT decode via `services/auth/jwt_utils.decode_token`,
  - tapd RPC transport via `tapd_client.py`,
  - Redis stream mirroring through `common.events.RedisStreamMirror`,
  - metrics, audit, rate limiting, and alerts via `services/common`.

### Notable implementation details

- `_make_async_url()` rewrites plain `postgresql://` and `postgres://` URLs to `postgresql+asyncpg://` before creating the async engine.
- `_build_asset_page()` sorts rows in memory and validates cursors against the filtered result set rather than paginating directly in SQL.
- `_run_asset_evaluation()` is the background execution path and is tracked in a process-local `_background_tasks` set so shutdown can cancel outstanding work.
- `create_asset_token()` performs the asset status flip, token insert, and initial balance insert in one database transaction.
- `tapd_client.py` does not implement a mint RPC wrapper; it only fetches and inspects existing assets.

### Evaluation-model details

The current evaluator is deterministic and rule-based. It is useful to know this if you are maintaining or explaining the service behavior.

- Category baselines: `real_estate = 72`, `commodity = 65`, `invoice = 69`, `art = 58`, `other = 55`.
- Documentation modifier: `+8` when a document URL exists, `-12` when absent.
- Description depth: up to `+12` based on word count.
- Title quality: up to `+4` based on title length.
- Positive keywords: `audited`, `insured`, `leased`, `occupied`, `verified`, `recurring revenue`.
- Negative keywords: `default`, `dispute`, `lawsuit`, `vacant`, `volatile`, `delinquent`.
- Valuation modifier ranges from `+6` for smaller submissions to `-8` for very large ones.
- Approval rule: `ai_score >= 70` yields `approved`; lower scores yield `rejected`.
- Projected ROI is derived heuristically and clamped into a `1.5-24.0` range.

## 8. Operational Notes

### Port and routing

- Service port: `8002`.
- Gateway prefix: `/v1/tokenization/`.
- Dedicated gateway shortcuts also exist for health, readiness, and metrics.

### Required and inferred environment variables

The tokenization service loads the shared `Settings` model, so it requires more configuration than it actively uses.

| Category | Variables |
| --- | --- |
| Service identity | `SERVICE_NAME`, `SERVICE_PORT`, `ENV_PROFILE`, `LOG_LEVEL` |
| Shared service URLs | `WALLET_SERVICE_URL`, `TOKENIZATION_SERVICE_URL`, `MARKETPLACE_SERVICE_URL`, `EDUCATION_SERVICE_URL`, `NOSTR_SERVICE_URL` |
| Database | `DATABASE_URL`, `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, optionally `POSTGRES_PASSWORD` or `POSTGRES_PASSWORD_FILE` |
| Redis | `REDIS_URL` |
| Auth | `JWT_SECRET` or `JWT_SECRET_FILE`, `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS`, `TOTP_ISSUER` |
| Taproot Assets | `TAPD_GRPC_HOST`, `TAPD_GRPC_PORT`, `TAPD_MACAROON_PATH`, `TAPD_TLS_CERT_PATH` |
| Bitcoin and LND readiness | `BITCOIN_RPC_HOST`, `BITCOIN_RPC_PORT`, `BITCOIN_RPC_USER`, optionally `BITCOIN_RPC_PASSWORD` or file-backed variant; `LND_GRPC_HOST`, `LND_GRPC_PORT`, `LND_MACAROON_PATH`, `LND_TLS_CERT_PATH` |
| Platform config inherited from shared settings | `BITCOIN_NETWORK`, `NOSTR_RELAYS`, optional `NOSTR_PRIVATE_KEY` or file-backed variant |
| Alerting | `ALERT_WEBHOOK_URL` or `ALERT_WEBHOOK_URL_FILE` |
| Optional future AI integration | `OPENAI_API_KEY` or `OPENAI_API_KEY_FILE` |
| Rate limiting | `RATE_LIMIT_WINDOW_SECONDS`, `RATE_LIMIT_WRITE_REQUESTS`, `RATE_LIMIT_SENSITIVE_REQUESTS` |
| Shared custody settings validated in non-local profiles | `CUSTODY_BACKEND`, `WALLET_ENCRYPTION_KEY` or file-backed variant, or the HSM-related custody settings |

**Important implementation note:** although tokenization handlers do not use custody features directly, the shared `Settings` model still validates custody-related secrets for `staging`, `beta`, and `production` profiles.

### Active vs configured external dependencies

| Dependency | Used actively by handlers | Used by readiness only | Notes |
| --- | --- | --- | --- |
| PostgreSQL | Yes | Yes | Primary persistence layer |
| tapd | Yes | Yes | Active gRPC dependency for token verification and diagnostics |
| Redis | Yes | Yes | Used for event-stream mirroring and readiness |
| Bitcoin Core | No | Yes | No tokenization route calls Bitcoin RPC directly |
| LND | No | Yes | No tokenization route calls LND directly |

### Observability considerations

- `GET /health` exposes liveness.
- `GET /ready` checks PostgreSQL, Redis, Bitcoin Core, LND, and tapd over TCP.
- `GET /metrics` supports Prometheus text and `?format=json`.
- Request metrics are auto-recorded via middleware, including total requests, duration histograms, in-progress gauges, and 5xx counters.
- Business events recorded include:
  - `asset_submit`
  - `asset_evaluation_request`
  - `asset_evaluation_complete`
  - `asset_tokenize`
- Audit events are recorded for:
  - `tokenization.asset.submit`
  - `tokenization.asset.evaluate`
  - `tokenization.asset.tokenize`
- Internal event publication includes:
  - `asset.created`
  - `ai.evaluation.complete`
  - `token.minted`
- Background evaluation failures trigger a CRITICAL alert through the shared alert dispatcher.

### Security considerations

- JWT verification is local to the service; there is no callback to auth for token introspection.
- The service fetches the user row after decoding the token so deleted users are rejected.
- Write operations are rate-limited by shared middleware.
- Because `/assets` and `/assets/` are configured as sensitive prefixes, all asset POST routes inherit the stricter sensitive-request limit by default.
- Sensitive log values are redacted by the shared `SensitiveDataFilter`.
- The service currently exposes unauthenticated tapd debug endpoints, which may be acceptable for internal environments but are risky for public deployment.
- Evaluation work is in-process rather than durable. If the service is terminated mid-task, there is no external job system to resume the work.
- Some important data guarantees live only at the API layer today, such as positive `valuation_sat`, `total_supply`, and `unit_price_sat`.

## 9. Example End-to-End Flow

### Flow 1: Seller submits, evaluates, and tokenizes an asset

1. The auth service issues an access token to a seller.
2. The frontend calls `POST /assets` with the asset metadata and diligence document URL.
3. Tokenization validates the JWT locally, checks the user's DB row, inserts the `assets` row with `status = pending`, records an audit event, and publishes `asset.created`.
4. The seller triggers `POST /assets/{asset_id}/evaluate`.
5. Tokenization flips the asset into `evaluating`, records an audit event, and starts an in-process background task.
6. The background task reads the asset, runs the heuristic evaluator, updates `ai_score`, `ai_analysis`, `projected_roi`, and final `status`, and publishes `ai.evaluation.complete`.
7. Once the asset is approved and the seller already has a Taproot Asset ID from `tapd`, the frontend calls `POST /assets/{asset_id}/tokenize`.
8. Tokenization verifies the Taproot Asset in `tapd`, builds issuance metadata, updates the asset to `tokenized`, inserts the `tokens` row, inserts the seller's `token_balances` row, records an audit event, and publishes `token.minted`.
9. Downstream services such as marketplace and wallet can now use the shared `tokens` and `token_balances` data.

This flow shows how tokenization depends on auth for identity, tapd for on-chain asset verification, PostgreSQL for shared persistence, and Redis-backed events for downstream propagation.

### Flow 2: Evaluation completion reaches other service surfaces

1. The seller requests evaluation through tokenization.
2. Tokenization completes the heuristic evaluation and publishes `ai.evaluation.complete`.
3. The event is mirrored to a Redis stream.
4. Marketplace can surface the event through its realtime notification flow for the asset owner.
5. Nostr can also consume the same stream topic and publish a signed event to configured relays.

This flow shows that tokenization is event-producing rather than event-consuming in the current platform.

## 10. Open Questions / Assumptions

- **Assumption:** `assets` and `tokens` are tokenization-owned tables, even though they live in a shared schema module.
- **Assumption:** the platform intends tokenization to do more than verification in the future, because the service README still describes minting Taproot Assets even though the current code does not perform mint RPCs.
- **Open question:** should `GET /assets` expose the full authenticated catalog, or should it support owner-scoped and public-only views separately?
- **Open question:** should `admin` users be able to evaluate or tokenize assets they do not own, or is owner-only behavior the intended long-term policy?
- **Open question:** should evaluation move to a durable worker or queue so work survives process restarts and scales independently?
- **Open question:** should `tokens.asset_id` become unique at the database level to enforce one token per asset?
- **Open question:** should `circulating_supply` start at full issuance, or should some portion remain non-circulating until later distribution/listing steps?
- **Open question:** should `token.minted` also be mirrored to Redis streams and consumed by another service today?
- **Open question:** should `/tapd/info` and `/tapd/assets` remain unauthenticated in production-facing deployments?
- **Open question:** is `OPENAI_API_KEY` meant to back a future external evaluation model, or is `heuristic-v1` the intended steady-state design?
- **Assumption:** requiring `documents_url` at the API layer is a deliberate product choice for diligence quality, even though the DB column is nullable.

## Integration Summary

For frontend teams, the tokenization service is the authenticated API surface for seller asset submission, asset catalog browsing, automated evaluation, and token-registration workflows. Integrate against the live routes in `services/tokenization/main.py`, treat `POST /assets/{asset_id}/tokenize` as verification plus registration rather than minting, and poll asset detail state or use downstream realtime surfaces when you need evaluation updates.

For backend teams, tokenization is an orchestration layer over shared persistence, shared observability/security modules, and direct `tapd` lookup. If you extend it, keep the current implementation gaps explicit: heuristic evaluation is not external AI, background work is not durable, one-token-per-asset is only operationally enforced, and tokenization records an existing Taproot Asset instead of creating one.