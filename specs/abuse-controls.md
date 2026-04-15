# Abuse Controls

> Defines rate limits, throttling policies, and abuse-mitigation strategies
> for authentication, trading, and administrative endpoints across the
> OpenProof platform.

---

## 1. Overview

Abuse controls are enforced at two layers:

1. **Gateway (nginx)** – connection-level rate limiting, request-size caps,
   and IP-based throttling
2. **Application (FastAPI middleware)** – per-client, per-path rate limiting
   via `common.security.RateLimitMiddleware`

All rules are configured through `Settings` (env vars) and can be tuned per
environment profile (local / staging / production).

---

## 2. Authentication Endpoints (`services/auth`)

### 2.1 Login & Registration

| Endpoint | Method | Limit | Window | Scope | Rationale |
|---|---|---|---|---|---|
| `/auth/login` | POST | 10 req | 60 s | per-IP | Prevent credential stuffing |
| `/auth/register` | POST | 5 req | 60 s | per-IP | Prevent mass account creation |
| `/auth/nostr` | POST | 10 req | 60 s | per-IP | Prevent Nostr auth abuse |

### 2.2 Token Operations

| Endpoint | Method | Limit | Window | Scope | Rationale |
|---|---|---|---|---|---|
| `/auth/refresh` | POST | 10 req | 60 s | per-IP | Prevent token-rotation storms |
| `/auth/logout` | POST | 10 req | 60 s | per-IP | Prevent session-revocation abuse |

### 2.3 Two-Factor Authentication

| Endpoint | Method | Limit | Window | Scope | Rationale |
|---|---|---|---|---|---|
| `/auth/2fa/enable` | POST | 3 req | 60 s | per-IP | Prevent secret enumeration |
| `/auth/2fa/verify` | POST | 5 req | 60 s | per-IP | Prevent TOTP brute-force |

### 2.4 Additional Controls

- **Timing-safe password comparison**: `bcrypt.checkpw` always runs a full
  hash cycle, even for non-existent accounts (`_DUMMY_HASH`), to prevent
  user-enumeration via response timing.
- **JWT expiry**: Access tokens expire per `jwt_access_token_expire_minutes`;
  refresh tokens per `jwt_refresh_token_expire_days`.
- **Refresh-token rotation**: Each refresh rotates the JTI; the previous
  token becomes immediately invalid.

---

## 3. Trading Endpoints (`services/marketplace`)

### 3.1 Order Management

| Endpoint | Method | Limit | Window | Scope | Rationale |
|---|---|---|---|---|---|
| `/orders` | POST | 60 req | 60 s | per-IP | Prevent order-flooding |
| `/orders/{id}/cancel` | POST | 60 req | 60 s | per-IP | Prevent cancel-storms |

### 3.2 Escrow Operations

| Endpoint | Method | Limit | Window | Scope | Rationale |
|---|---|---|---|---|---|
| `/escrows/{id}/sign` | POST | 10 req | 60 s | per-IP+path | Prevent sig replay |
| `/trades/{id}/dispute` | POST | 5 req | 60 s | per-IP+path | Prevent dispute flooding |

### 3.3 Additional Controls

- **Balance pre-checks**: Buy orders verify available sats minus reserved
  commitments; sell orders verify token balance minus reserved quantities.
- **2FA enforcement**: Escrow signature submission requires a valid TOTP code.
- **Hex-signature validation**: `_validate_hex_signature` rejects empty or
  malformed partial signatures before processing.

---

## 4. Wallet Endpoints (`services/wallet`)

### 4.1 Fund Operations

| Endpoint | Method | Limit | Window | Scope | Rationale |
|---|---|---|---|---|---|
| `/wallet/onchain/withdraw` | POST | 10 req | 60 s | per-IP+path | Prevent withdrawal storms |
| `/lightning/payments` | POST | 10 req | 60 s | per-IP+path | Prevent payment spam |
| `/wallet/onchain/address` | POST | 10 req | 60 s | per-IP | Prevent address generation abuse |

### 4.2 Additional Controls

- **Mandatory 2FA**: On-chain withdrawals require `X-2FA-Code` header with
  valid TOTP code; requests without it are rejected with `400`.
- **Insufficient-funds guard**: Withdrawal amounts are validated against
  wallet balance before transaction creation.
- **Audit logging**: All fund-moving operations are recorded via
  `record_audit_event` with wallet ID, amount, and address tail.

---

## 5. Administrative Endpoints (`services/admin`)

### 5.1 Privileged Operations

| Endpoint | Method | Limit | Window | Scope | Rationale |
|---|---|---|---|---|---|
| `/users/{id}` | PATCH | 10 req | 60 s | per-IP+path | Prevent role-change flooding |
| `/courses` | POST | 10 req | 60 s | per-IP | Prevent content spam |
| `/treasury/disburse` | POST | 5 req | 60 s | per-IP+path | Protect treasury |
| `/escrows/{id}/resolve` | POST | 5 req | 60 s | per-IP+path | Protect dispute resolution |

### 5.2 Additional Controls

- **Admin-only access**: All administrative endpoints require
  `role == "admin"` in the JWT claims.
- **2FA on sensitive ops**: Treasury disbursement and dispute resolution
  require a valid `X-2FA-Code` header.
- **Audit trail**: Every admin action is recorded via `record_audit_event`
  with actor ID, role, target, and metadata.

---

## 6. Asset Tokenization Endpoints (`services/tokenization`)

| Endpoint | Method | Limit | Window | Scope | Rationale |
|---|---|---|---|---|---|
| `/assets` | POST | 60 req | 60 s | per-IP | Prevent asset submission spam |
| `/assets/{id}/evaluate` | POST | 10 req | 60 s | per-IP+path | Prevent evaluation flooding |
| `/assets/{id}/tokenize` | POST | 10 req | 60 s | per-IP+path | Prevent token-mint abuse |

### Additional Controls

- **Seller/admin-only access**: Asset submission and evaluation require
  `seller` or `admin` role.
- **State-machine enforcement**: Assets must be in the correct status before
  evaluation or tokenization can proceed.
- **Duplicate-evaluation guard**: Concurrent evaluation requests are rejected
  with `409 Conflict`.

---

## 7. Global Controls (All Services)

### 7.1 Request Context

- Every request receives an `X-Request-ID` header (auto-generated UUID if not
  provided by the client).
- `X-Correlation-ID` is propagated when present for distributed tracing.
- Client IP is extracted from `X-Forwarded-For` (first hop) or
  `request.client.host`.

### 7.2 Write-Method Throttle

All `POST`, `PUT`, `PATCH`, `DELETE` requests across every service are subject
to a global write-rate limit:

| Parameter | Default | Env Var |
|---|---|---|
| Window | 60 s | `RATE_LIMIT_WINDOW_SECONDS` |
| Write limit | 60 req/window | `RATE_LIMIT_WRITE_REQUESTS` |
| Sensitive limit | 10 req/window | `RATE_LIMIT_SENSITIVE_REQUESTS` |

### 7.3 Sensitive-Data Redaction

All log output passes through `SensitiveDataFilter` which redacts:

- Bearer tokens
- JWT strings (`eyJ…`)
- Hex strings ≥ 64 characters (seeds, private keys)
- Key-value patterns matching `secret=`, `password=`, `api_key=`, etc.

### 7.4 Alerting Integration

Critical failures (e.g., escrow funding timeout, audit-log write failure,
seed encryption error) trigger alerts via the `AlertDispatcher`:

- **LogAlertSink**: Always active; emits `CRITICAL`-level structured logs
- **WebhookAlertSink**: Configurable; POSTs to PagerDuty/Slack/Opsgenie
- **EventBusAlertSink**: Publishes `alert.fired` to Redis streams

---

## 8. Tuning Guidelines

| Environment | Write Limit | Sensitive Limit | Notes |
|---|---|---|---|
| `local` | 600 | 100 | Relaxed for development |
| `staging` | 120 | 20 | Moderate for testing |
| `production` | 60 | 10 | Strict for production |

Operators can override per-service via environment variables without code
changes.
