# Nostr Service Integration Guide

This document describes the Nostr service as it is currently implemented in `services/nostr`, with explicit notes where the broader platform materials describe a larger Nostr feature set than the code currently provides.

## 1. Service Overview

The Nostr service is currently an outbound integration bridge between internal platform events and external Nostr relays. It consumes selected Redis stream topics, maps each internal payload into a signed Nostr event, and publishes the result to configured relays over WebSocket connections.

### Purpose of the service

- Bridge selected internal platform events into the Nostr ecosystem.
- Normalize internal event payloads into a consistent Nostr event shape.
- Sign outbound events with a platform-controlled Nostr private key.
- Expose operational endpoints for liveness, readiness, and metrics.

### Main responsibilities

- Subscribe to Redis stream topics `asset.created`, `ai.evaluation.complete`, and `trade.matched`.
- Parse mirrored event payloads produced by Tokenization and Marketplace.
- Map platform payloads into signed Nostr kind `1` events with structured tags and JSON content.
- Publish those events to each configured relay in `NOSTR_RELAYS`.
- Report relay configuration count, dependency readiness, and Prometheus-compatible metrics.

### Business/domain role within the platform

Within the broader tokenization platform, this service is the outbound social and notification adapter for a subset of platform activity. It turns internal asset and trading milestones into publicly consumable Nostr events so external clients, bots, or relay subscribers can observe selected platform activity without directly querying internal APIs or databases.

### Why this service exists separately from the others

- Nostr relay connectivity uses a different transport model than the platform's HTTP APIs: outbound WebSocket publishing instead of request-response CRUD.
- Nostr key management and event signing are operationally sensitive concerns that should be isolated from core business services.
- Relay delivery failures should not directly break asset creation, evaluation, or trade matching flows.
- Separating protocol translation keeps Tokenization and Marketplace focused on their own domains while Nostr handles external publication concerns.

### Current implementation status

| Area | Currently implemented | Intended or adjacent platform role |
| --- | --- | --- |
| Relay connectivity | Probes configured relays and publishes outbound events over WebSockets | Long-lived Nostr connectivity layer |
| Event publishing | Supports `asset.created`, `ai.evaluation.complete`, and `trade.matched` only | Broader notification and announcement publishing |
| Event format | Emits signed Nostr kind `1` events with JSON content and structured tags | Platform-wide event publication strategy |
| Identity bridge | Not implemented in this service | Specs and service README describe mapping Nostr public keys to users |
| Nostr auth | Not implemented in this service | Nostr login exists today in `services/auth`, not here |
| DM / bot handler | Not implemented in this service | Architecture materials mention NIP-04 DM handling |
| HTTP API | Operational endpoints only: `/health`, `/ready`, `/metrics` | Potential future admin or integration API surface |
| Durable delivery | Not implemented | No consumer groups, persisted offsets, replay, or dead-letter path |

## 2. Service Relationships

The Nostr service has a narrow runtime footprint. Its main relationships are event-driven publication from Tokenization and Marketplace, operational routing through Gateway, and shared infrastructure dependencies exposed through `services/common`.

### Relationships with other platform services

| Service | Purpose of interaction | Interaction type | Current implementation |
| --- | --- | --- | --- |
| `services/tokenization` | Source of asset lifecycle events for external publication | Event-driven communication | Tokenization mirrors `asset.created` and `ai.evaluation.complete` into Redis streams; Nostr consumes those streams and publishes signed relay events |
| `services/marketplace` | Source of trade lifecycle events for external publication | Event-driven communication | Marketplace mirrors `trade.matched` into Redis streams; Nostr consumes and republishes that event |
| `services/auth` | Adjacent Nostr identity and login behavior | Shared database access by adjacent service and authentication dependency outside this service | Auth owns `POST /auth/nostr`, validates signed Nostr auth events, creates users, and writes `nostr_identities`; the Nostr service itself does not call Auth or validate inbound Nostr auth |
| `services/gateway` | Public entrypoint routing for this service's operational endpoints | Direct API exposure through reverse proxy | Gateway routes `/v1/nostr/*` to port `8005` and exposes `/health/nostr`, `/ready/nostr`, and `/metrics/nostr` |
| `services/wallet` | No direct runtime interaction today | None currently | Wallet does not call the Nostr service, and the Nostr service does not consume wallet-originated topics in the current code |
| `services/education` | No direct runtime interaction today | None currently | Education does not publish or consume Nostr-service topics in the current code |
| `services/admin` | No direct runtime interaction today | None currently | Admin is not currently integrated with Nostr service endpoints or relay operations |

### Dependencies on `services/common`

| Shared module | Purpose | Interaction type | Current implementation |
| --- | --- | --- | --- |
| `common.config` | Shared settings model and secret resolution | Infrastructure/shared module dependency | Supplies relay list, Nostr private key, Redis URL, service identity, and the broader platform config required by the shared settings model |
| `common.readiness` | Dependency readiness checks | Infrastructure/shared module dependency | `GET /ready` checks PostgreSQL, Redis, Bitcoin Core, LND, and tapd over TCP |
| `common.metrics` | Request instrumentation and business-event metrics | Infrastructure/shared module dependency | Mounts `GET /metrics`, instruments HTTP requests, and records `business_events_total{event="nostr_publish"...}` |
| `common.alerting` | Alert sink configuration | Infrastructure/shared module dependency | The service configures shared alert sinks at startup, but does not currently fire any custom alerts of its own |
| `common.events` | Contract used indirectly by producer services | Event contract dependency | Tokenization and Marketplace use `InternalEventBus` and `RedisStreamMirror` to write the Redis stream payloads that Nostr consumes |
| `common.db.metadata` | Shared schema definition for Nostr identity data | Shared schema dependency outside this service's runtime path | Defines `nostr_identities`, which is used by Auth and Marketplace, but not queried by the Nostr service today |

### External and infrastructure relationships

| Dependency | Purpose | Interaction type | Current implementation |
| --- | --- | --- | --- |
| Redis | Transport for mirrored internal events | Event infrastructure dependency | Nostr reads Redis streams with `XREAD` and consumes `payload` fields as JSON |
| Nostr relays | External publication targets | Direct external integration over WebSocket | Each configured relay receives a probe message and outbound `EVENT` messages |
| PostgreSQL | Platform dependency exposed through readiness checks | Infrastructure dependency | Checked by `/ready`, but not used for active query or write paths in this service |
| Bitcoin Core | Platform dependency exposed through readiness checks | Infrastructure dependency | Checked by `/ready`; no Bitcoin RPC calls are made by the service |
| LND | Platform dependency exposed through readiness checks | Infrastructure dependency | Checked by `/ready`; no Lightning RPC calls are made by the service |
| tapd | Platform dependency exposed through readiness checks | Infrastructure dependency | Checked by `/ready`; no Taproot Assets RPC calls are made by the service |
| `websockets` package | Relay transport client | Runtime library dependency | Required for relay publishing and probing |
| `btclib` package | Schnorr signing and x-only pubkey derivation | Runtime library dependency | Used to derive the pubkey from the configured private key and sign outbound events |

### Supported inbound event topics

| Topic | Producer | Why Nostr consumes it | Current behavior |
| --- | --- | --- | --- |
| `asset.created` | Tokenization | Announce newly submitted assets | Mapped into a signed Nostr event and published to all configured relays |
| `ai.evaluation.complete` | Tokenization | Announce evaluation completion and scoring metadata | Mapped into a signed Nostr event and published to all configured relays |
| `trade.matched` | Marketplace | Announce trade matches and escrow context | Mapped into a signed Nostr event and published to all configured relays |

### Notes on absent direct service-to-service calls

- The Nostr service does not call sibling microservices over HTTP.
- It does not currently verify JWTs or depend on authenticated callers.
- It does not currently subscribe to inbound Nostr content from relays; it is outbound-only.
- The broader Nostr identity model exists elsewhere in the codebase, primarily in Auth and shared schema definitions.

## 3. Database Documentation

### Current implementation reality

The Nostr service does not currently open a database engine, import a `db.py` module, or execute SQL queries. From a runtime perspective, it has no active table access. PostgreSQL appears only as a readiness dependency through the shared readiness check.

That said, there is Nostr-related platform data in the shared schema that other services use. For maintainers, that data model is relevant because it represents the platform's current Nostr identity bridge even though it is not managed by `services/nostr` itself.

### Nostr-related shared table in the platform schema

| Table | Ownership | Purpose | Important fields and constraints | Relationships |
| --- | --- | --- | --- | --- |
| `nostr_identities` | Auth-owned shared table | Links a platform user account to a Nostr public key and optional preferred relays | `id` UUID PK; `user_id` UUID FK to `users.id`; `pubkey` `VARCHAR(64)` unique and non-null; `relay_urls` `TEXT[]` nullable; `created_at` defaults to `NOW()`; unique constraint `uq_nostr_identities_pubkey`; FK `fk_nostr_identities_user_id_users` | Many-to-one to `users`; read by Marketplace and written by Auth |
| `users` | Auth-owned shared table | Parent table referenced by `nostr_identities` | `id` UUID PK; nullable `email` supports Nostr-created users; other auth/account fields live here | Referenced by `nostr_identities.user_id` |

### How the shared Nostr table is used today

- `services/auth/db.py` reads `nostr_identities` by `pubkey` during `POST /auth/nostr`.
- `services/auth/db.py` inserts `nostr_identities` when a first-time Nostr login creates a user.
- `services/marketplace/db.py` reads the earliest `nostr_identities` row for a user and compresses the x-only pubkey for escrow-related key resolution.
- `services/nostr` does not currently read or write `nostr_identities`.

### Relevant Alembic migration

| Migration | Why it matters |
| --- | --- |
| `20260413_1330_0002_remaining_schema_tables.py` | Creates `nostr_identities` with the `user_id` foreign key, unique `pubkey`, optional `relay_urls`, and `created_at` timestamp |

### Relevant schema verification tests

| Test | What it verifies |
| --- | --- |
| `tests/test_migrations_schema.py::test_nostr_identities_schema_matches_spec` | Confirms `user_id` and `pubkey` are non-null, `created_at` has a default, and the unique and foreign-key constraints exist |

### Ownership and assumptions

- The Nostr service currently owns no database tables in practice.
- `nostr_identities` should be treated as shared Nostr-domain data, but its active ownership today is closer to Auth than to the Nostr service.
- **Assumption:** if the service later implements the spec's identity bridge directly, `nostr_identities` would likely become a first-class runtime dependency here as well.

## 4. API Endpoints

### Path conventions

- The paths below are the service's internal FastAPI paths.
- Through the gateway, these routes are usually reachable as `/v1/nostr/<path>`.
- The gateway also exposes convenience operational paths: `/health/nostr`, `/ready/nostr`, and `/metrics/nostr`.
- There are currently no business-domain endpoints such as publish, subscribe, identity lookup, or relay management APIs.

### Important scope note

`POST /auth/nostr` is not part of this service. It belongs to `services/auth` and should be documented and integrated as an Auth endpoint, not a Nostr-service endpoint.

### Endpoint summary

| Method | Internal path | Gateway path | Purpose | Auth required |
| --- | --- | --- | --- | --- |
| `GET` | `/health` | `/v1/nostr/health` or `/health/nostr` | Liveness probe and relay-count summary | No |
| `GET` | `/ready` | `/v1/nostr/ready` or `/ready/nostr` | Dependency readiness report | No |
| `GET` | `/metrics` | `/v1/nostr/metrics` or `/metrics/nostr` | Prometheus or JSON metrics snapshot | No |

### 4.1 `GET /health`

| Field | Value |
| --- | --- |
| Purpose | Simple liveness check plus configured relay count |
| Authentication / authorization | None |
| Query parameters | None |
| Request body | None |
| Success status | `200 OK` |

**Response schema**

```json
{
  "status": "ok",
  "service": "nostr",
  "env_profile": "local",
  "configured_relays": 1
}
```

**Possible error responses**

- None are custom-defined.
- If FastAPI or process-level failures occur, the service would return a generic `500`, but the handler itself has no explicit error branch.

### 4.2 `GET /ready`

| Field | Value |
| --- | --- |
| Purpose | Readiness probe that reports dependency health |
| Authentication / authorization | None |
| Query parameters | None |
| Request body | None |
| Success status | `200 OK` when all dependencies are reachable |
| Failure status | `503 Service Unavailable` when one or more dependencies are down |

**Response schema**

```json
{
  "status": "ready",
  "service": "nostr",
  "env_profile": "local",
  "dependencies": {
    "postgres": {
      "ok": true,
      "target": "localhost:5432",
      "error": null
    },
    "redis": {
      "ok": true,
      "target": "localhost:6379",
      "error": null
    },
    "bitcoin": {
      "ok": true,
      "target": "localhost:18443",
      "error": null
    },
    "lnd": {
      "ok": true,
      "target": "localhost:10009",
      "error": null
    },
    "tapd": {
      "ok": true,
      "target": "localhost:10029",
      "error": null
    }
  }
}
```

**Important implementation note**

- Relay reachability is not part of this readiness check.
- A service can return `ready` even when every configured Nostr relay is unreachable.

**Possible error responses**

- `503` when any dependency check fails. The response body still uses the readiness schema above, but `status` becomes `not_ready` and individual dependency entries include error strings.

### 4.3 `GET /metrics`

| Field | Value |
| --- | --- |
| Purpose | Expose request metrics, business-event counters, readiness-derived gauges, and service metadata |
| Authentication / authorization | None |
| Query parameters | Optional `format=json` |
| Request body | None |
| Success status | `200 OK` |

**Default response format**

- `text/plain; version=0.0.4; charset=utf-8`
- Prometheus exposition format

**Prometheus response example**

```text
# TYPE service_info gauge
service_info{bitcoin_network="regtest",env_profile="local",service="nostr"} 1.000000
# TYPE business_events_total counter
business_events_total{bitcoin_network="regtest",env_profile="local",event="nostr_publish",outcome="success",service="nostr"} 12.000000
```

**JSON response schema when `?format=json` is used**

```json
{
  "counters": {
    "business_events_total": {
      "event=nostr_publish,outcome=success": 12.0
    }
  },
  "gauges": {
    "service_info": {
      "bitcoin_network=regtest,env_profile=local,service=nostr": 1.0
    }
  },
  "histograms": {},
  "uptime_seconds": 123.45,
  "collected_at": "2026-04-15T12:00:00+00:00",
  "service": "nostr",
  "env_profile": "local",
  "bitcoin_network": "regtest",
  "readiness": {
    "status": "ready",
    "service": "nostr",
    "env_profile": "local",
    "dependencies": {}
  }
}
```

**Possible error responses**

- No custom error contract is defined.
- Readiness failures do not cause `/metrics` to return `503`; readiness status is embedded in the metrics payload instead.

### Missing API surface compared with platform intent

The following capabilities are described in service README or architecture materials, but are not currently exposed as Nostr-service HTTP endpoints:

- Nostr identity lookup or linking API
- Relay management API
- Direct publish API
- Nostr DM or bot interaction API
- Auth challenge or signature verification API

## 5. How to Use the Endpoints

### Prerequisites

- Direct service URL in local compose: `http://localhost:8005`
- Gateway URL in local compose: `http://localhost:8000`
- No bearer token is required for the currently implemented Nostr service endpoints.
- For actual Nostr login, use the Auth service endpoint `POST /v1/auth/nostr` instead of calling the Nostr service.

### Common workflow 1: confirm the service is alive

```bash
curl http://localhost:8005/health
```

Or through the gateway:

```bash
curl http://localhost:8000/health/nostr
```

Expected response:

```json
{
  "status": "ok",
  "service": "nostr",
  "env_profile": "local",
  "configured_relays": 1
}
```

Use this when you only need to know that the process is up and the relay list was parsed.

### Common workflow 2: verify readiness before relying on relay publishing

```bash
curl http://localhost:8005/ready
```

Or through the gateway:

```bash
curl http://localhost:8000/ready/nostr
```

If everything is reachable, you get `200` with `"status": "ready"`.

If Redis or another dependency is unavailable, expect `503` with a body like:

```json
{
  "status": "not_ready",
  "service": "nostr",
  "env_profile": "local",
  "dependencies": {
    "redis": {
      "ok": false,
      "target": "localhost:6379",
      "error": "[Errno 111] Connection refused"
    }
  }
}
```

### Common workflow 3: inspect metrics in JSON during development

```bash
curl "http://localhost:8005/metrics?format=json"
```

Or through the gateway:

```bash
curl "http://localhost:8000/metrics/nostr?format=json"
```

This is useful when debugging:

- total HTTP traffic to the service
- `nostr_publish` success and failure counts
- current readiness-derived dependency gauges
- service uptime

### JavaScript `fetch` example for an admin or operations UI

```js
async function loadNostrReadiness() {
  const response = await fetch("/ready/nostr");
  const data = await response.json();

  return {
    ok: response.ok,
    status: data.status,
    dependencies: data.dependencies,
  };
}
```

### Operational workflow: how events actually reach relays

There is no HTTP endpoint to push business events into Nostr. The current workflow is internal and event-driven:

1. Tokenization or Marketplace emits an internal event through `common.events.InternalEventBus`.
2. `common.events.RedisStreamMirror` writes that payload to a Redis stream such as `asset.created`.
3. The Nostr service blocks on `XREAD` for supported topics.
4. The service parses the JSON `payload` field, maps it into a Nostr event, signs it, and publishes it to each configured relay.

### Example of the emitted Nostr event shape

The service sends a signed Nostr event similar to the following:

```json
{
  "id": "6d8c5a1d4f9c...",
  "pubkey": "f1a2b3c4d5e6...",
  "created_at": 1776254400,
  "kind": 1,
  "tags": [
    ["topic", "trade.matched"],
    ["event", "trade_matched"],
    ["source", "nostr"],
    ["entity", "trade_id", "trade-123"],
    ["entity", "token_id", "token-9"],
    ["entity", "buyer_id", "buyer-1"],
    ["entity", "seller_id", "seller-2"]
  ],
  "content": "{\"event_type\":\"trade_matched\",\"occurred_at\":null,\"payload\":{...},\"source_service\":\"nostr\",\"topic\":\"trade.matched\"}",
  "sig": "1a2b3c4d5e6f..."
}
```

Important details:

- `kind` is always `1` in the current implementation.
- `content` is a JSON string, not a nested JSON object.
- Every payload field ending in `_id` becomes an `entity` tag.
- The full original payload is embedded inside `content.payload`.

### Frontend-relevant prerequisite for Nostr auth

If a frontend wants "Sign in with Nostr," the correct current flow is:

1. Collect a Nostr-signed auth event client-side.
2. Send it to `POST /v1/auth/nostr`.
3. Receive normal platform access and refresh tokens from Auth.
4. Use those tokens against Wallet, Tokenization, Marketplace, and other protected APIs.

The Nostr service is not part of that login round-trip today.

## 6. Frontend Integration Recommendations

### What frontend applications should do

- Treat the Nostr service as an operational backend component, not as a primary business API.
- Use `services/auth` for Nostr login and session establishment.
- Use Tokenization, Marketplace, Wallet, and Admin APIs as the source of truth for application state.
- Treat relay-published Nostr content as an external notification channel or public broadcast channel.

### Recommended UI flows

| UI need | Recommended integration |
| --- | --- |
| "Sign in with Nostr" | Call `POST /v1/auth/nostr`, not the Nostr service |
| Public activity feed based on platform broadcasts | Subscribe to relevant Nostr relays or consume a backend-aggregated feed derived from relays |
| Admin health dashboard | Poll `/health/nostr` and `/ready/nostr` with low frequency |
| Admin diagnostics | Read `/metrics/nostr?format=json` from a privileged internal UI only |

### Validation, loading states, retries, and error handling

- For health and readiness widgets, show explicit states: `loading`, `healthy`, `degraded`, `down`.
- Retries are reasonable for `/health` and `/ready`, but keep them conservative to avoid noisy polling.
- For metrics, avoid aggressive browser polling; metrics endpoints are better suited for Prometheus or internal dashboards.
- When readiness returns `503`, surface the dependency-level errors directly to operators rather than collapsing them into a generic failure.

### Auth, session state, and token refresh

- The Nostr service does not currently require bearer auth for any exposed endpoint.
- Frontend session handling for Nostr-based login still follows the Auth service's normal access-token and refresh-token flow.
- Token refresh remains `POST /auth/refresh` in Auth, not anything in Nostr.

### Caching guidance

- Do not cache `/ready`; always fetch it fresh.
- `/health` may be short-lived cached in an ops UI if needed, but fresh fetches are safer.
- Do not cache `/metrics` responses in frontend code; treat them as live diagnostics.
- Do not treat Nostr relay content as canonical cached application state. Canonical state should come from the platform APIs or database-backed service responses.

### Real-time and event-driven considerations

- Relay publication is asynchronous and best-effort. A successful asset or trade action does not guarantee immediate relay visibility.
- UI logic must not assume that a Nostr event has been published just because the originating API call succeeded.
- If the frontend displays relay-derived events, reconcile them against backend state when exact correctness matters.

### Security recommendations for frontend consumption

- Never expose `NOSTR_PRIVATE_KEY` or any file-backed secret values to the frontend.
- Do not expose `/metrics` publicly on untrusted surfaces; it includes service metadata and dependency status.
- Treat all relay-derived content as untrusted display content even if the platform publishes it, especially if future relay subscriptions are added.

### Anti-patterns to avoid

- Do not call the Nostr service for authentication.
- Do not use Nostr relay events as the only confirmation that a trade or asset action succeeded.
- Do not poll `/metrics` from end-user pages.
- Do not assume the service supports inbound relay subscriptions, DMs, or relay management APIs today.

## 7. Internal Logic and Important Modules

### Main files in `services/nostr`

| File | Role |
| --- | --- |
| `main.py` | FastAPI app bootstrap, lifecycle management, Redis stream consumption loop, health/readiness endpoints, and metrics registration |
| `events.py` | Core mapping from internal platform payloads to Nostr event structure plus event signing logic |
| `relay_client.py` | Relay transport adapter that probes and publishes over WebSockets |
| `README.md` | Short service-level intent statement that currently describes a broader role than the implemented code |
| `requirements.txt` | Minimal runtime dependencies: FastAPI, Uvicorn, shared settings support, Redis client, WebSocket client, and `btclib` |

### `main.py`

`main.py` contains almost all orchestration logic:

- Loads shared settings with service name `nostr` and default port `8005`.
- Configures alert sinks via `configure_alerting(settings)`.
- Defines the hard-coded inbound topic allowlist: `asset.created`, `ai.evaluation.complete`, and `trade.matched`.
- Starts a lifespan task that probes relays once and then launches the Redis consumer loop.
- Exposes `/health` and `/ready`.
- Mounts `/metrics` through `common.metrics.mount_metrics_endpoint`.
- Derives a deterministic fallback private key from service name plus JWT secret when no Nostr key is configured.

### `events.py`

This file contains the core domain logic that is actually specific to the service:

- `map_internal_event_to_nostr(...)` turns an internal topic plus payload into a Nostr-compatible unsigned event.
- `_entity_tags(...)` extracts every `*_id` field from the payload and emits `entity` tags.
- `_derive_xonly_pubkey(...)` derives the x-only pubkey from the configured private key.
- `sign_nostr_event(...)` computes the event commitment, hashes it, and Schnorr-signs the result.
- `map_and_sign_internal_event(...)` composes mapping plus signing in one call.

The resulting outbound event has:

- `kind = 1`
- `created_at = now`
- `tags` including `topic`, `event`, `source`, and one `entity` tag per `*_id`
- `content` as JSON containing `event_type`, `topic`, `source_service`, `occurred_at`, and the original payload

### `relay_client.py`

This file is the outbound adapter layer:

- `probe_relays()` sends a minimal `REQ` frame to each relay and records success or failure.
- `publish()` serializes `EVENT` frames and attempts delivery to every configured relay.
- `_send_over_websocket()` uses the `websockets` package with short open and close timeouts.

Important behavior:

- Relay publish failures are logged but do not abort publishing to other relays.
- Relay probe failures are logged and reported as `False` in the probe result.

### Business logic vs adapters

| Concern | Location | Type |
| --- | --- | --- |
| Topic allowlist and worker lifecycle | `main.py` | Service orchestration |
| Stream payload parsing and retry-tolerant publish loop | `main.py` | Service orchestration |
| Internal-event to Nostr-event mapping | `events.py` | Core domain logic |
| Schnorr signing and pubkey derivation | `events.py` | Core domain logic with crypto library dependency |
| Relay transport | `relay_client.py` | External adapter |
| Readiness and metrics | `common.readiness`, `common.metrics` | Shared infrastructure |

### Notable absences compared with other services

- There is no `schemas.py` module because the service does not currently expose request/response business contracts beyond simple operational endpoints.
- There is no `db.py` module because the service does not currently perform runtime database operations.
- There is no explicit auth dependency, no `HTTPBearer`, and no `install_http_security(...)` call in the current implementation.
- There is no structured-logging bootstrap call like `configure_structured_logging(...)` in this service, unlike several other services in the repo.

### Important implementation details

- `map_internal_event_to_nostr()` stores the original payload verbatim inside `content.payload`.
- `occurred_at` is selected from `created_at`, `completed_at`, or `minted_at` only.
- `trade.matched` payloads emitted by Marketplace currently use `settled_at`, so `occurred_at` will likely be `null` for those published Nostr events.
- `_pump_events_to_relays()` starts reading streams from `$`, which means it consumes only new events after the worker starts.

## 8. Operational Notes

### Port and runtime entrypoint

- Service port: `8005`
- Local compose container: `tokenization-nostr`
- Compose command: `uvicorn main:app --host 0.0.0.0 --port 8005`

### Environment variables actively used by this service

| Variable | Required | Purpose |
| --- | --- | --- |
| `NOSTR_RELAYS` | Yes | Comma-separated list of relay URLs to probe and publish to |
| `NOSTR_PRIVATE_KEY` | No | Hex private key used to derive pubkey and sign outbound events |
| `NOSTR_PRIVATE_KEY_FILE` | No | File-backed alternative for the Nostr private key |
| `REDIS_URL` | Yes | Redis endpoint used for `XREAD` stream consumption |
| `LOG_LEVEL` | Yes through shared settings | Controls validated log level value |
| `BITCOIN_NETWORK` | Yes through shared metrics labels | Included in metrics labels |
| `JWT_SECRET` | No in local, required in higher envs by shared settings | Also used by the fallback private-key derivation path when no Nostr key is configured |

### Environment variables required because of the shared settings model

The service inherits the full shared `Settings` model from `services/common/config.py`. Even though many of these settings are not used directly by Nostr runtime logic, they still need to be present for configuration loading and readiness checks:

- Shared service URLs: `WALLET_SERVICE_URL`, `TOKENIZATION_SERVICE_URL`, `MARKETPLACE_SERVICE_URL`, `EDUCATION_SERVICE_URL`, `NOSTR_SERVICE_URL`
- PostgreSQL settings: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `DATABASE_URL`, plus password or password file when needed
- Bitcoin settings: `BITCOIN_RPC_HOST`, `BITCOIN_RPC_PORT`, `BITCOIN_RPC_USER`, `BITCOIN_NETWORK`, and password or password file when needed
- Lightning settings: `LND_GRPC_HOST`, `LND_GRPC_PORT`, `LND_MACAROON_PATH`, `LND_TLS_CERT_PATH`
- Taproot Assets settings: `TAPD_GRPC_HOST`, `TAPD_GRPC_PORT`, `TAPD_MACAROON_PATH`, `TAPD_TLS_CERT_PATH`
- Auth timing settings: `JWT_ACCESS_TOKEN_EXPIRE_MINUTES`, `JWT_REFRESH_TOKEN_EXPIRE_DAYS`, `TOTP_ISSUER`

### External dependencies

| Dependency | Required for startup | Required for readiness | Required for active runtime logic |
| --- | --- | --- | --- |
| Redis | Yes | Yes | Yes |
| Nostr relays | No hard startup failure, but required for useful publishing | No, relay checks are logged not surfaced in readiness | Yes |
| PostgreSQL | Yes through shared config expectations | Yes | No active queries |
| Bitcoin Core | Yes through shared config expectations | Yes | No |
| LND | Yes through shared config expectations | Yes | No |
| tapd | Yes through shared config expectations | Yes | No |

### Observability considerations

- `/health` reports service identity, environment profile, and the number of configured relays.
- `/ready` checks TCP reachability to PostgreSQL, Redis, Bitcoin Core, LND, and tapd.
- `/metrics` exposes request counts, duration histograms, readiness-derived dependency gauges, `service_info`, and `business_events_total`.
- Successful outbound publishes increment `business_events_total{event="nostr_publish",outcome="success"}`.
- Publish failures increment `business_events_total{event="nostr_publish",outcome="failure"}` and log stack traces.
- Relay probe failures are logged during startup, but the service still starts.
- Malformed Redis payloads are skipped and logged without crashing the loop.

### Alerting considerations

- `configure_alerting(settings)` is called at startup, so log and optional webhook/event-bus sinks are available.
- The service does not currently invoke `alert_dispatcher.fire(...)` for relay or stream failures.
- Operational teams should therefore expect logs and metrics for most failures, but not dedicated Nostr-specific alert events from this service.

### Reliability considerations

- The stream reader initializes topic offsets to `$`, so events already present in Redis before the service starts are skipped.
- There is no persisted offset tracking across restarts.
- There is no Redis consumer group usage, retry queue, or dead-letter queue.
- Relay publish failures are logged but do not abort the overall workflow.
- Because `publish()` suppresses per-relay errors internally, the surrounding worker only records a failure metric when an exception escapes the connector entirely.

### Security considerations

- The Nostr private key is secret material and should be supplied via `NOSTR_PRIVATE_KEY_FILE` or another secret-management path in non-local environments.
- If `NOSTR_PRIVATE_KEY` is not set, the service derives a deterministic fallback key from `service_name` and `JWT_SECRET`. This keeps local and test publishing functional but should not be treated as a safe production strategy.
- The service does not expose write endpoints, but `/metrics` and `/ready` reveal operational information and should usually be restricted to trusted environments.
- Incoming Redis stream payloads are trusted internal inputs; there is no schema validation layer beyond JSON parsing and simple field handling.
- Relay delivery is best-effort. A failed publish is logged but does not roll back the originating asset or trade operation.
- Unlike several other services in this repository, the Nostr service does not currently call the shared structured-logging setup or HTTP security middleware. Do not assume request-ID middleware, rate limiting, or shared log redaction are active here unless they are enforced elsewhere.

## 9. Example End-to-End Flow

### Flow 1: Asset submission becomes a Nostr event

1. A client submits an asset through the Tokenization service.
2. Tokenization persists the asset row and publishes `asset.created` through `InternalEventBus`.
3. Tokenization's `RedisStreamMirror` writes that event into the Redis stream `asset.created`.
4. The Nostr service's background worker reads the new stream record via `XREAD`.
5. `services/nostr/events.py` maps the payload into a Nostr event with:
   - `kind = 1`
   - tags for `topic`, `event`, `source`, and any `*_id` fields such as `asset_id` and `owner_id`
   - JSON `content` containing the original payload and event metadata
6. The service signs the event with the configured Nostr private key.
7. `NostrRelayConnector.publish(...)` sends the serialized `EVENT` message to every configured relay.
8. External Nostr clients can now consume the asset-created announcement from those relays.

### Flow 2: Marketplace trade match becomes a Nostr event

1. Marketplace matches a buy and sell order.
2. Marketplace builds a `trade.matched` payload including `trade_id`, `token_id`, buyer and seller IDs, quantity, pricing, fee, and escrow metadata.
3. Marketplace publishes the event internally and mirrors it to Redis.
4. The Nostr service consumes that stream record.
5. The service signs and publishes a Nostr event containing the trade metadata.
6. If one relay fails, the service logs the failure and continues attempting publication to the remaining relays.
7. The originating trade flow remains successful even if downstream Nostr publication partially fails.

### Related but separate flow: Nostr login

The broader platform also supports Nostr-based login, but that flow does not go through this service:

1. A client creates a signed Nostr auth event.
2. The client calls Auth `POST /auth/nostr`.
3. Auth validates the event signature and freshness.
4. Auth looks up or creates a `users` row plus `nostr_identities` row.
5. Auth returns standard platform access and refresh tokens.

This matters because engineers often assume the Nostr service owns all Nostr functionality. In the current codebase, it does not.

## 10. Open Questions / Assumptions

- The service README and architecture spec describe an identity bridge and DM bot handler, but no such implementation exists in `services/nostr` today.
- It is unclear whether future Nostr identity ownership should move into this service or remain in Auth plus shared tables.
- It is unclear whether additional event topics such as `token.minted`, `escrow.funded`, `escrow.released`, or treasury events are intended to be published later.
- Relay probe results are logged at startup, but there is no persistent relay-status API or in-memory status endpoint. Whether one is intended is not yet explicit.
- **Assumption:** using kind `1` for all outbound platform events is an intentional simplification for now, not a finalized long-term event taxonomy.
- **Assumption:** the deterministic private-key fallback is meant for local and test environments only, even though the code does not hard-block it in all deployments.
- **Assumption:** `nostr_identities` is operationally Auth-owned today because Auth writes it and Marketplace reads it, while the Nostr service does not touch it.
- There is no explicit schema validation for consumed Redis payloads beyond JSON parsing, so malformed-but-valid JSON may still produce incomplete Nostr events if producers drift.

## Integration Summary

For frontend teams, the main rule is simple: do not treat the Nostr service as the place to authenticate or fetch canonical product state. Use Auth for Nostr login, use the core domain services for source-of-truth data, and treat relay content as a downstream notification layer.

For backend teams, the Nostr service is currently an outbound relay bridge. If you want a platform event to appear on Nostr, emit a stable internal event, mirror it to Redis, and update the Nostr service's topic allowlist and mapping logic as needed. Keep business workflows independent from relay delivery, and provide a stable `NOSTR_PRIVATE_KEY` in non-development environments.