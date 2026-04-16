# AGENT.md

Operational manual for AI coding agents and human engineers working on the RWA Tokenization Platform.

---

## 1. Project Overview

- **Project name**: RWA Tokenization Platform on Bitcoin
- **Copyright**: © 2026 CUBOPLUS (MIT License)
- **Purpose**: A platform for tokenizing real-world assets (RWA) on Bitcoin using Taproot Assets, enabling fractional ownership, peer-to-peer trading with multisig escrow, and self-custodial wallet management.
- **Domain goal**: Democratize access to real-world asset investment through Bitcoin-native tokenization with an auditable educational treasury funded by platform fees.

### Primary User Flows

1. **Registration & onboarding** — Email/password or Nostr pubkey signup, optional KYC, optional referral code, wallet creation with HD key derivation.
2. **Asset submission & tokenization** — Sellers submit real-world assets → AI evaluation (scoring, ROI projection) → admin approval → Taproot Assets minting on-chain.
3. **Marketplace trading** — Buyers/sellers place limit or stop-limit orders → trade matching → 2-of-3 multisig escrow (buyer, seller, platform) → settlement with fee extraction to treasury.
4. **Wallet operations** — On-chain Bitcoin deposits/withdrawals, Lightning Network payments, fiat on-ramp provider handoff, yield accrual on token holdings.
5. **Education** — Course catalog funded by treasury, user enrollments with progress tracking.
6. **Nostr integration** — Platform events (asset created, trade matched, AI evaluation complete) are published to Nostr relays as signed events.
7. **Admin operations** — User role management, dispute resolution, treasury disbursement, yield/referral analytics.

---

## 2. Tech Stack

### Languages

| Layer | Language |
|-------|----------|
| Backend | Python 3.11+ |
| Frontend (spec) | TypeScript 5 + React 18 |
| Infrastructure | YAML, Nginx conf, Shell scripts |
| Generated code | gRPC protobuf stubs (Python) |

### Frameworks & Libraries

| Category | Technology |
|----------|------------|
| Web framework | FastAPI 0.115+ (ASGI via Uvicorn) |
| ORM / DB | SQLAlchemy 2.0 (async), Alembic 1.13+ |
| DB driver | pg8000 (migrations), asyncpg (runtime) |
| Validation | Pydantic v2 (BaseSettings, BaseModel) |
| Auth | python-jose (JWT), bcrypt, pyotp (TOTP) |
| Cryptography | `cryptography` (AES-256-GCM), HMAC-SHA256 |
| Blockchain | grpcio (LND, tapd), Bitcoin Core HTTP RPC |
| AI | OpenAI API (asset evaluation) |
| Cache / Pub-Sub | Redis 7 (async client, streams) |
| HTTP client | httpx |
| Frontend (spec) | Tailwind CSS 4, Shadcn, Zustand, React Query, Vite 5, Recharts |

### Infrastructure & Databases

| Component | Version / Detail |
|-----------|------------------|
| Database | PostgreSQL 15+ |
| Cache / Streams | Redis 7 |
| Bitcoin node | Bitcoin Core (regtest / testnet / signet / mainnet) |
| Lightning | LND (gRPC :10009) |
| Taproot Assets | tapd (gRPC :10029) |
| Reverse proxy | Nginx 1.25 (Alpine) |
| Containers | Docker + Docker Compose |
| CI/CD | GitHub Actions |
| Observability | Prometheus, Grafana, Alertmanager, Blackbox Exporter, cAdvisor |

### Testing & Quality Tools

| Tool | Purpose |
|------|---------|
| pytest + asyncio | Unit and integration tests |
| unittest.mock / AsyncMock | Mocking async DB and gRPC calls |
| markdownlint-cli2 | Markdown linting (CI) |
| `scripts/scan_secrets.py` | Custom secret scanning (CI) |
| .editorconfig | Code style (indentation, line endings) |

---

## 3. Repository Structure

```
tokenization/
├── AGENT.md                  # This file
├── README.md                 # Project overview, setup, contribution guide
├── WORKFLOW_CHEATSHEET.md    # Git sync & branching quick reference
├── LICENSE                   # MIT
├── alembic.ini               # Alembic configuration
├── alembic/                  # Database migrations
│   ├── env.py                # Migration environment (loads metadata, env vars)
│   └── versions/             # 11 sequential migration files
├── services/                 # All platform microservices
│   ├── __init__.py
│   ├── common/               # Shared infrastructure (no port)
│   │   ├── __init__.py       # Public API exports
│   │   ├── config.py         # Pydantic Settings, env profiles, secret resolution
│   │   ├── db/metadata.py    # SQLAlchemy table definitions (19 tables)
│   │   ├── custody.py        # AES-256-GCM wallet encryption, HSM, platform signing
│   │   ├── security.py       # Rate limiting, request context, sensitive data filtering
│   │   ├── audit.py          # Audit log recording
│   │   ├── events.py         # InternalEventBus + RedisStreamMirror
│   │   ├── logging.py        # Structured JSON logging with redaction
│   │   ├── readiness.py      # Health/readiness checks for dependencies
│   │   ├── metrics.py        # Prometheus-compatible metrics collector
│   │   ├── alerting.py       # Alert dispatcher (log, webhook, event-bus sinks)
│   │   ├── onramp.py         # Fiat on-ramp provider definitions and session creation
│   │   └── incentives.py     # Referral rewards + yield accrual calculations
│   ├── auth/                 # Auth service (:8000)
│   │   ├── main.py, schemas.py, db.py
│   │   ├── jwt_utils.py      # Token issuance & validation
│   │   ├── nostr_utils.py    # NIP-01 event validation, Schnorr sig verify
│   │   └── kyc_db.py         # KYC verification queries
│   ├── wallet/               # Wallet service (:8001)
│   │   ├── main.py, schemas.py, db.py, auth.py
│   │   ├── key_manager.py    # HD wallet derivation (BIP-86), seed encryption
│   │   ├── lnd_client.py     # LND gRPC client wrapper
│   │   ├── lnd_grpc/         # Generated protobuf stubs
│   │   └── schemas_wallet.py, schemas_lnd.py, log_filter.py
│   ├── tokenization/         # Tokenization service (:8002)
│   │   ├── main.py, schemas.py, db.py
│   │   ├── evaluation.py     # AI-powered asset evaluation
│   │   ├── tapd_client.py    # Taproot Assets daemon gRPC client
│   │   ├── tapd_grpc/        # Generated protobuf stubs
│   │   └── events.py         # Domain event definitions
│   ├── marketplace/          # Marketplace service (:8003)
│   │   ├── main.py, schemas.py, db.py
│   │   ├── escrow.py         # 2-of-3 multisig escrow management
│   │   └── bitcoin_rpc.py    # Bitcoin Core RPC client
│   ├── education/            # Education service (:8004)
│   │   ├── main.py, schemas.py, db.py
│   ├── nostr/                # Nostr integration (:8005)
│   │   ├── main.py           # Relay connector, event publisher
│   │   ├── relay_client.py   # WebSocket relay connections
│   │   └── events.py         # Nostr event construction
│   ├── admin/                # Admin service (:8006)
│   │   ├── main.py, schemas.py, db.py
│   ├── gateway/              # Nginx reverse proxy (:8000)
│   │   ├── Dockerfile        # nginx:1.25-alpine
│   │   └── default.conf      # Proxy rules, rate limiting, TLS
│   └── frontend/             # React app (spec only, not yet built)
├── tests/                    # Cross-service integration & E2E tests
├── specs/                    # Architecture, API, DB, frontend, security specs
├── infra/                    # Docker Compose, Bitcoin config, observability
├── deploy/                   # Deployment runbooks (public-beta, mainnet)
├── scripts/                  # Dev utilities, secret scanner, migration helpers
├── strategy/                 # Vision, mission, action plan
└── logs/                     # Runtime log output (gitignored)
```

### Where to Find Things

| Task | Location |
|------|----------|
| Add/modify a service endpoint | `services/<service>/main.py` |
| Change database schema | `services/common/db/metadata.py` + new migration in `alembic/versions/` |
| Modify shared config/settings | `services/common/config.py` |
| Add a new database table | `services/common/db/metadata.py` (table def) → Alembic migration |
| Change authentication logic | `services/auth/main.py`, `services/auth/jwt_utils.py` |
| Change wallet encryption | `services/common/custody.py` |
| Change escrow/multisig logic | `services/marketplace/escrow.py` |
| Add rate limit rules | `services/common/security.py` |
| Modify API schemas | `services/<service>/schemas.py` |
| Add/run tests | `tests/test_<service>.py` |
| Modify Docker setup | `infra/docker-compose.*.yml` |
| Update API contracts spec | `specs/api-contracts.md` |
| Update database spec | `specs/database-schema.md` |

---

## 4. Architecture

### Style

Microservices architecture with a shared library (`services/common/`), an API gateway (Nginx), and async event propagation via an in-process event bus mirrored to Redis streams.

### System Diagram

```mermaid
graph TB
    subgraph Client["Client Layer"]
        Web["Web / PWA"]
        CLI["CLI / Mobile"]
    end

    subgraph Gateway["API Gateway :8000"]
        Nginx["Nginx — TLS, CORS, Rate Limiting, JWT Enforcement"]
    end

    subgraph Services["Microservices"]
        Auth["Auth :8000"]
        Wallet["Wallet :8001"]
        Token["Tokenization :8002"]
        Market["Marketplace :8003"]
        Edu["Education :8004"]
        Nostr["Nostr :8005"]
        Admin["Admin :8006"]
    end

    subgraph Infra["Infrastructure"]
        PG["PostgreSQL :5432"]
        Redis["Redis :6379"]
    end

    subgraph Blockchain["Blockchain Layer"]
        BTC["Bitcoin Core RPC :18443"]
        LND["LND gRPC :10009"]
        TAPD["tapd gRPC :10029"]
    end

    Web --> Nginx
    CLI --> Nginx
    Nginx --> Auth & Wallet & Token & Market & Edu & Nostr & Admin
    Auth & Wallet & Token & Market & Edu & Nostr & Admin --> PG
    Auth & Wallet & Token & Market & Nostr --> Redis
    Wallet --> LND --> BTC
    Token --> TAPD --> BTC
    Market --> BTC
```

### Request Lifecycle

1. Client sends HTTPS request to Gateway (:8000).
2. Nginx terminates TLS, enforces CORS, applies rate limiting, and proxies to the target service.
3. Service extracts JWT from `Authorization: Bearer <token>`, decodes and validates it, and resolves the principal (user ID, roles, wallet ID).
4. For sensitive operations (withdraw, escrow sign, treasury disburse), 2FA (TOTP) is verified via the `X-TOTP-Code` header.
5. Service performs business logic with async SQLAlchemy connections (PostgreSQL) and/or gRPC calls (LND, tapd, Bitcoin Core).
6. Business events are published to `InternalEventBus` and mirrored to Redis streams for cross-service consumption.
7. Audit events are recorded to the `audit_logs` table.
8. Response is returned in the standard JSON contract.

### Event Bus Topics

| Topic | Producer | Consumers |
|-------|----------|-----------|
| `trade.matched` | Marketplace | Nostr |
| `token.minted` | Tokenization | Nostr |
| `asset.created` | Tokenization | Nostr |
| `ai.evaluation.complete` | Tokenization | Nostr |
| `escrow.funded` | Wallet | — |
| `escrow.released` | Wallet | — |
| `fee.collected` | Marketplace | Education (treasury) |

### Key Abstractions

| Abstraction | Location | Purpose |
|-------------|----------|---------|
| `Settings` | `common/config.py` | Centralized config with profile-aware env loading |
| `InternalEventBus` | `common/events.py` | In-process pub/sub for domain events |
| `RedisStreamMirror` | `common/events.py` | Mirrors events to Redis streams |
| `MetricsCollector` | `common/metrics.py` | Thread-safe Prometheus-compatible metrics |
| `AlertDispatcher` | `common/alerting.py` | Multi-sink alerting (log, webhook, event bus) |
| `WalletCustodyBackend` | `common/custody.py` | Abstract custody (software AES-256-GCM / HSM) |
| `PlatformSigner` | `common/custody.py` | Abstract platform signing for escrow counter-signatures |
| `SensitiveDataFilter` | `common/security.py` | Log redaction for secrets, tokens, keys |
| `RateLimitMiddleware` | `common/security.py` | In-memory sliding-window rate limiting |

### Configuration Strategy

- Pydantic `BaseSettings` loads from environment variables.
- Profile-based `.env.{profile}` files: `local`, `staging`, `beta`, `production`.
- Secret resolution: env var `VAR` or file-backed `VAR_FILE` (file takes precedence).
- Non-local profiles enforce JWT secret presence (startup validation).

### Error Handling Strategy

- All API errors use the contract: `{"error": {"code": "<slug>", "message": "<human>"}}`
- HTTP status codes follow REST conventions (400, 401, 403, 404, 409, 422, 500).
- Audit log write failures are silent but trigger CRITICAL alerts (never block the request).
- Database operations use explicit `await conn.commit()` (no autocommit).

---

## 5. Core Domains and Responsibilities

### Auth (`services/auth/`)

| Aspect | Detail |
|--------|--------|
| **Owns** | User registration, login, JWT issuance/rotation/revocation, 2FA (TOTP), Nostr login, KYC status tracking, onboarding summary |
| **Tables** | `users`, `nostr_identities`, `refresh_token_sessions`, `kyc_verifications` |
| **Depends on** | `common/config`, `common/custody` (wallet creation at registration), `common/incentives` (referral codes) |
| **Key files** | `main.py` (endpoints), `jwt_utils.py` (token lifecycle), `nostr_utils.py` (NIP-01 validation), `kyc_db.py` (KYC queries) |
| **Do not place here** | Business logic for wallet operations, trading, or asset management |

### Wallet (`services/wallet/`)

| Aspect | Detail |
|--------|--------|
| **Owns** | Bitcoin custody (HD keys, seed encryption), Lightning payments, on-chain transactions, balance aggregation, fiat on-ramp sessions, yield accrual execution |
| **Tables** | `wallets`, `transactions`, `token_balances`, `yield_accruals` |
| **Depends on** | LND (gRPC), Bitcoin Core (RPC), `common/custody`, `common/onramp`, `common/incentives` |
| **Key files** | `main.py`, `key_manager.py` (BIP-86 derivation, AES-256-GCM), `lnd_client.py` (gRPC stub), `db.py` |
| **Do not place here** | Order matching, escrow creation, asset evaluation |

### Tokenization (`services/tokenization/`)

| Aspect | Detail |
|--------|--------|
| **Owns** | Asset submission, AI-powered evaluation (scoring, ROI projection), Taproot Assets minting, fractionalization |
| **Tables** | `assets`, `tokens` |
| **Depends on** | tapd (gRPC), OpenAI API, `common/events` |
| **Key files** | `main.py`, `evaluation.py` (AI scoring), `tapd_client.py` (gRPC), `events.py` (domain events) |
| **Do not place here** | Trading logic, escrow management, wallet operations |

### Marketplace (`services/marketplace/`)

| Aspect | Detail |
|--------|--------|
| **Owns** | Order book management, trade matching, 2-of-3 multisig escrow lifecycle, fee extraction, dispute handling, WebSocket price updates |
| **Tables** | `orders`, `trades`, `escrows`, `disputes`, `treasury` (fee deposits) |
| **Depends on** | Bitcoin Core (RPC for multisig address scanning), `common/events`, Redis (real-time streams) |
| **Key files** | `main.py`, `escrow.py` (multisig), `bitcoin_rpc.py` (RPC client), `db.py` |
| **Do not place here** | Wallet balance tracking, asset evaluation, user management |

### Education (`services/education/`)

| Aspect | Detail |
|--------|--------|
| **Owns** | Course catalog, enrollments, progress tracking |
| **Tables** | `courses`, `enrollments` |
| **Depends on** | `common/config` |
| **Key files** | `main.py`, `db.py`, `schemas.py` |

### Nostr (`services/nostr/`)

| Aspect | Detail |
|--------|--------|
| **Owns** | Relay connections, event publishing, consuming internal event bus topics and broadcasting to Nostr |
| **Tables** | (reads `nostr_identities`) |
| **Depends on** | Redis streams (consumer), Nostr relays (WebSocket) |
| **Key files** | `main.py`, `relay_client.py`, `events.py` |
| **Subscribes to** | `asset.created`, `ai.evaluation.complete`, `trade.matched` |

### Admin (`services/admin/`)

| Aspect | Detail |
|--------|--------|
| **Owns** | User role management, course creation, treasury ledger queries and disbursement, dispute resolution, yield/referral analytics |
| **Tables** | Reads/writes across `users`, `courses`, `treasury`, `disputes`, `escrows`, `yield_accruals`, `referral_rewards` |
| **Depends on** | `common/incentives`, `common/audit` |
| **Key files** | `main.py` (~500 lines, all admin endpoints), `db.py`, `schemas.py` |
| **Guards** | All endpoints require `admin` role; sensitive ops require 2FA |

### Common (`services/common/`)

| Aspect | Detail |
|--------|--------|
| **Owns** | Shared infrastructure: config, DB metadata, custody, security, logging, metrics, alerting, events, audit, on-ramp, incentives, readiness |
| **Consumed by** | Every other service |
| **Critical constraint** | Changes here affect all services — test broadly |

---

## 6. Coding Standards and Conventions

### Naming Conventions

| Element | Convention | Example |
|---------|-----------|---------|
| Directories | lowercase, singular | `wallet`, `marketplace` |
| Python files | snake_case | `key_manager.py`, `bitcoin_rpc.py` |
| Doc files | kebab-case | `api-contracts.md`, `database-schema.md` |
| Functions | snake_case | `get_user_by_id()`, `create_onramp_session()` |
| Classes | PascalCase | `MetricsCollector`, `AlertDispatcher` |
| Constants | UPPER_SNAKE_CASE | `REFERRAL_SIGNUP_BONUS_SAT`, `_TOTP_DIGITS` |
| Module-private | leading underscore | `_runtime_engine()`, `_get_current_principal()` |
| DB tables | snake_case, plural | `users`, `token_balances`, `yield_accruals` |
| DB constraints | `ck_<table>_<column>`, `uq_<table>_<columns>`, `fk_<table>_<column>_<ref>` |
| Branches | `feat/…`, `fix/…`, `docs/…`, `chore/…` |
| Commits | `type(scope): short description` |

### File Organization (per service)

```
services/<service>/
├── main.py       # FastAPI app, lifespan, all endpoint handlers
├── schemas.py    # Pydantic request/response models
├── db.py         # Database query functions (async, using SQLAlchemy)
└── <domain>.py   # Domain-specific logic (e.g., escrow.py, evaluation.py)
```

### App Initialization Pattern

Every service follows this pattern:

```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    _runtime_engine()  # Create/cache async SQLAlchemy engine
    yield
    await _runtime_engine().dispose()

app = FastAPI(title="Service Name", lifespan=_lifespan)
```

### Authentication Pattern

```python
async def _get_current_principal(credentials: HTTPAuthorizationCredentials = Depends(_bearer)):
    # Decode JWT, extract user_id, validate against DB
    ...

async def _require_admin(principal = Depends(_get_current_principal)):
    # Check role == "admin"
    ...

async def _check_2fa(request: Request, principal):
    # Verify X-TOTP-Code header
    ...
```

### Database Access Pattern

```python
async with _runtime_engine().connect() as conn:
    row = await get_entity_by_id(conn, entity_id)
    if row is None:
        raise HTTPException(404, detail={"error": {"code": "not_found", ...}})
    # Mutations require explicit commit:
    await conn.commit()
```

### Row-to-Schema Conversion

The codebase uses a universal `_row_value(row, key, default)` helper that handles both dict-like and SQLAlchemy Row objects:

```python
def _row_value(row, key, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    mapping = getattr(row, "_mapping", None)
    if mapping is not None and key in mapping:
        return mapping[key]
    return getattr(row, key, default)
```

### Error Response Contract

All services return errors in this format:

```json
{"error": {"code": "string_slug", "message": "Human-readable description"}}
```

### Logging Convention

- Structured JSON logging via `configure_structured_logging()`.
- Every log record includes: timestamp (ISO-8601), level, service name, request_id, correlation_id.
- Sensitive data (tokens, keys, seeds, passwords) is redacted by `SensitiveDataFilter`.

### Metrics Convention

- All services expose `GET /metrics` (JSON or Prometheus text format).
- Business events recorded via `record_business_event(name, outcome, labels)`.
- HTTP middleware auto-instruments: `http_requests_total`, `http_requests_in_progress`, `http_request_duration_seconds`, `http_errors_total`.

### Import Convention

- Standard library → third-party → local service → `services.common` modules.
- `services/common/__init__.py` re-exports all public APIs.
- Services import from `services.common` (e.g., `from services.common.config import get_settings`).

### Testing Convention

- Test files: `tests/test_<service>.py` or `tests/test_<feature>.py`.
- Fixtures use `@pytest.fixture` with named tuples (`FakeUser`, `FakeWallet`, etc.) to simulate DB rows.
- Async DB connections are mocked with `AsyncMock` — no real PostgreSQL required for unit tests.
- Settings are patched at module import time via `unittest.mock.patch.dict(os.environ, ...)`.
- Test pattern: Arrange → Act → Assert.

---

## 7. Development Workflow

### Prerequisites

- Python 3.11+
- Docker + Docker Compose
- PostgreSQL 15+ (or use Docker)
- Redis 7 (or use Docker)

### Install Dependencies

```bash
# Migration dependencies
pip install -r scripts/requirements-migrations.txt

# Test dependencies
pip install -r tests/requirements.txt

# Service dependencies (each service may have its own requirements)
pip install fastapi uvicorn sqlalchemy asyncpg pydantic-settings python-jose bcrypt pyotp cryptography grpcio httpx redis
```

### Run Locally with Docker Compose

```bash
# Copy environment template
cp infra/.env.local.example infra/.env.local

# Start all services + infrastructure
docker compose -f infra/docker-compose.local.yml up -d

# Verify
curl http://localhost:8000/health          # Gateway
curl http://localhost:8000/v1/wallet/health # Wallet via gateway
```

### Shutdown

```bash
docker compose -f infra/docker-compose.local.yml down
```

### Run Tests

```bash
# Unit/integration tests (no real DB required)
pytest tests/ -q

# With verbose output
pytest tests/ -v

# Specific service tests
pytest tests/test_wallet.py -v
pytest tests/test_auth.py -v
```

### Run Migration Smoke Tests (requires PostgreSQL)

```bash
# Set DATABASE_URL or POSTGRES_* vars
export DATABASE_URL=postgresql+pg8000://user:pass@localhost:5432/testdb

# Run migration cycle
alembic downgrade base
alembic upgrade head
```

### Run Alembic Migrations

```bash
# Apply all migrations
alembic upgrade head

# Create a new migration
alembic revision -m "description_of_change"

# Downgrade one step
alembic downgrade -1
```

### Run Secret Scanner

```bash
python scripts/scan_secrets.py
```

### Run Markdown Linting

```bash
npx markdownlint-cli2 "**/*.md"
```

### Run Observability Stack

```bash
docker compose -f infra/docker-compose.observability.yml up -d
# Grafana: http://localhost:3000 (admin/admin)
# Prometheus: http://localhost:9090
```

### Run Public Beta Stack

```bash
cp infra/.env.beta.example infra/.env.beta
docker compose -f infra/docker-compose.public-beta.yml up -d
```

---

## 8. Environment and Configuration

### Environment Profiles

| Profile | Network | Use Case |
|---------|---------|----------|
| `local` | Bitcoin regtest | Local development |
| `staging` | Bitcoin testnet | Staging/QA |
| `beta` | Bitcoin signet | Public beta |
| `production` | Bitcoin mainnet | Production |

Set via `ENV_PROFILE` environment variable. Defaults to `local`.

### Required Environment Variables

| Variable | Description | Required In |
|----------|-------------|-------------|
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Database connection | All profiles |
| `DATABASE_URL` | Alternative to individual POSTGRES_* vars | Optional |
| `REDIS_URL` | Redis connection string | All profiles |
| `JWT_SECRET` | JWT signing secret | Non-local profiles (enforced) |
| `WALLET_ENCRYPTION_KEY` | AES-256-GCM key for wallet seeds | All profiles |
| `BITCOIN_RPC_HOST`, `BITCOIN_RPC_PORT`, `BITCOIN_RPC_USER`, `BITCOIN_RPC_PASSWORD` | Bitcoin Core connection | All profiles |
| `BITCOIN_NETWORK` | `regtest` / `testnet` / `signet` / `mainnet` | All profiles |
| `LND_GRPC_HOST`, `LND_GRPC_PORT`, `LND_MACAROON_PATH`, `LND_TLS_CERT_PATH` | LND connection + auth | All profiles |
| `TAPD_GRPC_HOST`, `TAPD_GRPC_PORT`, `TAPD_MACAROON_PATH`, `TAPD_TLS_CERT_PATH` | Taproot Assets daemon | All profiles |
| `OPENAI_API_KEY` | Asset evaluation AI | All profiles |
| `NOSTR_RELAYS`, `NOSTR_PRIVATE_KEY` | Nostr relay list + signing key | When Nostr service is active |
| `CUSTODY_BACKEND` | `software` or `hsm` | Production should use `hsm` |
| `ALERT_WEBHOOK_URL` | Webhook for critical alerts | Staging, production |
| `KYC_TRADE_THRESHOLD_SAT` | KYC enforcement threshold (default 10M) | Beta, production |

### Secret Handling

- **Convention**: For any variable `VAR`, the system checks `VAR_FILE` first (path to a file containing the secret), then `VAR` (inline value).
- **Docker secrets**: Mount secret files and set `*_FILE` environment variables.
- **Never commit**: `.env` files, plaintext secrets, keys, tokens, or credentials.
- **Template files**: `infra/.env.local.example`, `infra/.env.staging.example`, `infra/.env.beta.example`, `infra/.env.production.example`.

### Profile-Specific Rate Limits

| Profile | Write (req/window) | Sensitive (req/window) |
|---------|--------------------|-----------------------|
| local | 600 / 100 | — |
| staging | 120 / 20 | — |
| production | 60 / 10 | — |

---

## 9. Testing Strategy

### Test Structure

All tests live in `tests/` at the repository root. Each file targets a service or cross-cutting concern:

| Test File | Scope |
|-----------|-------|
| `test_auth.py` | Auth service (registration, login, JWT, 2FA, Nostr) |
| `test_wallet.py` | Wallet service (balance, transactions) |
| `test_wallet_summary.py` | Wallet balance aggregation |
| `test_wallet_pricing.py` | Pricing calculations |
| `test_marketplace.py` | Order placement, trade matching |
| `test_tokenization_assets.py` | Asset submission, evaluation |
| `test_education.py` | Course catalog, enrollments |
| `test_admin.py` | Admin endpoints |
| `test_nostr_main.py` | Nostr service |
| `test_nostr_events.py` | Nostr event construction |
| `test_nostr_relay_client.py` | Relay client |
| `test_key_manager.py` | HD key derivation, encryption |
| `test_lightning.py` | Lightning Network operations |
| `test_token_balance_sync.py` | Token balance synchronization |
| `test_security_controls.py` | Rate limiting, data filtering |
| `test_observability.py` | Metrics, health checks |
| `test_migrations_schema.py` | Alembic migration idempotency (requires PostgreSQL) |
| `test_e2e_trading_flow.py` | End-to-end asset → tokenize → trade → escrow |
| `test_public_beta_operations.py` | Public beta workflow validation |
| `test_kyc_and_mainnet_rollout.py` | KYC enforcement, mainnet readiness |

### Mocking Strategy

- **No real database** needed for most tests — async DB connections are mocked.
- **Named tuples** (`FakeUser`, `FakeWallet`, `FakeTransaction`) simulate SQLAlchemy Row objects.
- **Settings patching**: Environment variables are patched before service module import to prevent real connections.
- **gRPC mocks**: LND and tapd clients are mocked at the client level.

### CI Pipeline (`.github/workflows/ci.yml`)

1. **Validate**: Secret scanning (`scripts/scan_secrets.py`) + Markdown linting.
2. **Backend tests**: `pytest tests/ -q` against all services.
3. **Migration smoke test**: PostgreSQL service → `alembic downgrade base` → `upgrade head` → `downgrade base` → `upgrade head` (idempotency verification).

### Testing Gaps

- Frontend tests (no frontend code exists yet, only spec).
- Load/performance testing not visible.
- End-to-end tests with real blockchain (regtest) not automated in CI.
- gRPC integration tests against real LND/tapd not visible.

---

## 10. Build, Release, and Deployment

### Docker Images

- **Base image**: `python:3.11-slim` (services), `nginx:1.25-alpine` (gateway).
- Services are built from `services/<service>/Dockerfile` or run via Docker Compose command directives.
- Source code is volume-mounted in local development.

### CI Pipeline

1. **Secret scanning**: Custom regex patterns via `scripts/scan_secrets.py`.
2. **Markdown linting**: `markdownlint-cli2`.
3. **Backend tests**: `pytest`.
4. **Migration smoke test**: Full Alembic cycle on ephemeral PostgreSQL.

### Deployment Profiles

| Profile | Compose File | Bitcoin Network | Notes |
|---------|-------------|-----------------|-------|
| Local | `docker-compose.local.yml` | regtest | Dev defaults, volume mounts |
| Public Beta | `docker-compose.public-beta.yml` | signet | Isolated secrets, testnet data |
| Production | (manual / CI-driven) | mainnet | HSM custody, `*_FILE` secrets, KYC enforcement |

### Release Flow (Mainnet — from `deploy/mainnet/README.md`)

1. **Preflight checks**: CI green, migration smoke test passes, beta validation complete.
2. **Deployment**: Apply migrations → roll out services → verify health checks.
3. **Post-deploy**: Dashboard checks, end-to-end trade flow, treasury balance verification.
4. **Rollback authority**: Release Lead or Infrastructure Lead.

### Startup Order

PostgreSQL → Redis → Bitcoin Core → LND/tapd → Services → Gateway

Each dependency has health checks (pg_isready, Redis PING, getblockchaininfo).

---

## 11. Observability and Operations

### Logging

- **Format**: Structured JSON (one line per record).
- **Fields**: timestamp, level, logger, service, request_id, correlation_id, message, exception details.
- **Redaction**: `SensitiveDataFilter` strips tokens, keys, seeds, Bearer tokens, hex strings, and key-value assignments from logs.
- **Configuration**: `LOG_LEVEL` environment variable (default varies by profile).

### Metrics

- **Collector**: In-process `MetricsCollector` (thread-safe, no external agent).
- **Endpoint**: `GET /metrics` on each service (JSON or Prometheus text format).
- **Auto-instrumented**: HTTP requests (total, in-progress, duration, errors), service readiness, dependency health.
- **Custom**: Business events via `record_business_event()` (e.g., `nostr_publish`, `trade_matched`).

### Alerting

- **Dispatcher**: `AlertDispatcher` with pluggable sinks.
- **Sinks**: LogAlertSink (structured log), WebhookAlertSink (Slack/PagerDuty/Opsgenie), EventBusAlertSink (Redis streams).
- **Severities**: INFO, WARNING, CRITICAL.
- **Auto-configured**: From `Settings` (webhook URL, event bus reference).

### Health Checks

- `GET /health` on each service — basic liveness.
- `get_readiness_payload()` in `common/readiness.py` — checks TCP connectivity to PostgreSQL, Redis, Bitcoin Core, LND, tapd.
- Returns `{ "status": "ready|not_ready", "dependencies": { ... } }`.

### Observability Stack (Docker Compose)

| Component | Port | Purpose |
|-----------|------|---------|
| Prometheus | 9090 | Metrics scraping |
| Grafana | 3000 | Dashboards (admin/admin) |
| Alertmanager | 9093 | Alert routing + webhook delivery |
| Blackbox Exporter | 9115 | HTTP/gRPC endpoint probing |
| cAdvisor | 8081 | Container resource metrics |

### Common Debugging Entry Points

1. **Service health**: `GET /health` and `GET /metrics` on each service port.
2. **Request tracing**: Search logs by `request_id` (propagated via `X-Request-ID` header).
3. **Database state**: Query PostgreSQL directly (tables defined in `services/common/db/metadata.py`).
4. **Event flow**: Check Redis streams for event propagation.
5. **Blockchain state**: Bitcoin Core RPC (`getblockchaininfo`), LND (`getinfo`), tapd queries.
6. **Audit trail**: Query `audit_logs` table by `actor_id`, `action`, `request_id`.

---

## 12. Security and Safety Constraints

### Authentication & Authorization

- **JWT**: 15-minute access tokens, 7-day refresh tokens with rotation.
- **Password hashing**: bcrypt (default rounds).
- **2FA**: TOTP (6-digit, 30-second period, ±1 step window) required for sensitive operations.
- **RBAC roles**: `user`, `seller`, `admin`, `auditor`.
- **Nostr login**: NIP-01 event validation with Schnorr signature verification.
- **Session tracking**: `refresh_token_sessions` table with JTI for revocation.

### Known Security Findings (from security audit)

| ID | Severity | Issue | Status |
|----|----------|-------|--------|
| F-001 | P0 Critical | Default JWT secret fallback in local mode | Mitigated (config validator enforces in non-local) |
| F-002 | P1 High | Wallet encryption key stored in env var | Open (recommend `_FILE` in prod) |
| F-003 | P2 Medium | Rate limiter in-memory, not shared across replicas | Open (gateway-level Nginx limiting mitigates) |
| F-004 | P1 High | Nostr event validation scope (missing nonce, freshness, kind checks) | Open |
| F-005 | P2 Medium | TOTP brute-force window (no account lockout) | Open |
| F-006 | P2 Medium | Audit log write failures are silent | Mitigated (CRITICAL alert triggered) |
| F-007 | P1 High | Platform counter-signature uses HMAC not ECDSA | Open (should use secp256k1 Schnorr) |

### Security-Sensitive Modules

- `services/common/custody.py` — Wallet seed encryption, key derivation. **Extreme caution.**
- `services/wallet/key_manager.py` — HD wallet derivation (BIP-86). **Extreme caution.**
- `services/marketplace/escrow.py` — Multisig escrow creation and signing. **Extreme caution.**
- `services/auth/jwt_utils.py` — JWT issuance, validation, secret management.
- `services/common/security.py` — Rate limiting, sensitive data filtering.
- `services/auth/nostr_utils.py` — Schnorr signature verification.

### Dangerous Operations

- **Wallet encryption key changes** — Will make all existing encrypted seeds unrecoverable.
- **JWT secret rotation** — Invalidates all existing tokens (requires coordinated rollout).
- **Migration downgrades on production** — Data loss risk.
- **Treasury disbursement** — Irreversible fund movement.
- **Escrow signing** — Irreversible on-chain transaction settlement.

### Input Validation

- Pydantic models validate all request bodies.
- UUID parameters validated with `_as_uuid()` normalization.
- ENUM checks enforced at both schema and database constraint level.
- Rate limiting per IP + path for all write operations.

---

## 13. Change Guidance for AI Agents

### Diagnosing Bugs

1. Read the relevant `services/<service>/main.py` for the endpoint in question.
2. Check `services/<service>/db.py` for query logic.
3. Review `services/common/db/metadata.py` for table definitions and constraints.
4. Search `tests/test_<service>.py` for existing test coverage of the behavior.
5. Check `specs/api-contracts.md` for the expected API contract.

### Adding a New Endpoint

1. Define Pydantic request/response schemas in `services/<service>/schemas.py`.
2. Add the handler in `services/<service>/main.py` following existing patterns (auth, error format, audit).
3. Add DB query functions in `services/<service>/db.py` if needed.
4. Add tests in `tests/test_<service>.py`.
5. Update `specs/api-contracts.md` if the endpoint is part of the public API.
6. Record audit events for any state-changing operations.
7. Add rate limiting rules if the endpoint is sensitive.

### Changing API Contracts

1. Update `specs/api-contracts.md` first.
2. Modify Pydantic schemas.
3. Update endpoint handler.
4. Update all affected tests.
5. Verify error responses follow the `{"error": {"code": ..., "message": ...}}` contract.

### Changing Database Schema

1. Modify table definitions in `services/common/db/metadata.py`.
2. Update `specs/database-schema.md`.
3. Create a new Alembic migration:
   ```bash
   alembic revision -m "description"
   ```
4. Follow the naming convention: `YYYYMMDD_HHMM_<rev>_<slug>.py`.
5. Test the migration cycle: `downgrade base` → `upgrade head` → `downgrade base` → `upgrade head`.
6. Update any affected service `db.py` files with new queries.
7. Update affected test fixtures.

### Updating Dependencies

1. Update the relevant `requirements*.txt` file.
2. Test locally with Docker Compose.
3. Ensure no breaking API changes in updated libraries.
4. Run full test suite.

### Invariants to Preserve

- **Error response format**: Always `{"error": {"code": "...", "message": "..."}}`.
- **UUID v4 for all user-facing primary keys**.
- **Explicit `await conn.commit()`** after mutations (no autocommit).
- **Audit logging** for all state-changing operations.
- **Metrics recording** via `record_business_event()`.
- **Structured JSON logging** through `configure_structured_logging()`.
- **`_row_value()` helper** for SQLAlchemy Row access (not direct attribute access).
- **TOTP verification** for sensitive operations (withdrawals, escrow signing, treasury disbursement).
- **Settings via `get_settings()`** — never hardcode config values.
- **CHECK constraints** on all enum columns at the database level.
- **Soft deletes** on `users` and `assets` (set `deleted_at`, never hard delete).

### Anti-Patterns to Avoid

- **Do not** add autocommit to database sessions.
- **Do not** bypass rate limiting middleware.
- **Do not** log sensitive data (keys, seeds, tokens, passwords) — rely on `SensitiveDataFilter`.
- **Do not** hardcode secrets, URLs, or port numbers.
- **Do not** create tables outside `services/common/db/metadata.py`.
- **Do not** use raw SQL strings — use SQLAlchemy constructs.
- **Do not** skip audit event recording for financial operations.
- **Do not** make `custody.py` or `key_manager.py` changes without thorough security review.
- **Do not** merge migrations without running the full smoke test cycle.
- **Do not** add new services without updating Docker Compose files and the gateway config.

### When to Avoid Broad Refactors

- The `common/` module is imported by all services — any refactor risks breaking everything.
- `db/metadata.py` changes require corresponding Alembic migrations.
- Do not restructure the error response format without updating all services and tests.
- Do not rename event bus topics without updating all producers and consumers.

---

## 14. Known Risks, Debt, and Fragile Areas

### Security Risks (Open)

- **F-002**: Wallet encryption key in env var (not file-backed) — risk of exposure in process listings.
- **F-004**: Nostr event validation does not check nonce binding, freshness, or event kind.
- **F-005**: No TOTP account lockout — brute-force possible within rate limit window.
- **F-007**: Platform counter-signature uses HMAC-SHA256, not real ECDSA/Schnorr — not a valid Bitcoin signature.

### Architectural Risks

- **In-memory rate limiter** (`common/security.py`): Not shared across replicas. Multi-instance deployments rely solely on Nginx gateway-level limiting.
- **Shared PostgreSQL**: All services use a single database. No per-service isolation — cross-service queries are possible but not enforced.
- **Event bus coupling**: `InternalEventBus` is in-process; only the `RedisStreamMirror` provides cross-service communication. If Redis is down, cross-service events are lost.
- **No message queue**: No durable message queue (e.g., RabbitMQ, Kafka) — Redis streams are the only async communication path.

### Missing or Incomplete

- **Frontend**: Specified but not yet implemented (only spec in `specs/frontend-spec.md`).
- **Load testing**: No performance or load tests visible.
- **gRPC integration tests**: No tests against real LND/tapd.
- **KYC provider integration**: `kyc_db.py` has DB queries but no external provider integration.
- **Fiat on-ramp integration**: Provider definitions exist but actual webhook/callback handling is not visible.
- **HSM integration**: Abstract interface exists (`HsmCompatibleWalletCustody`) but no concrete HSM provider.
- **Nostr relay authentication**: No relay authentication or access control visible.

### Code Quality

- **Single TODO in entire codebase** (in generated gRPC code, not custom code).
- **No FIXME or HACK comments** — codebase is clean.
- **Consistent patterns** across all services — low pattern divergence.

### Fragile Areas

- `services/common/db/metadata.py` — Central schema definition; changes cascade to migrations and all services.
- `services/common/config.py` — Central settings; adding/renaming variables affects all services.
- `services/marketplace/escrow.py` — Multisig escrow is the core financial safety mechanism.
- `services/common/custody.py` — Wallet encryption; any bug can cause fund loss.

---

## 15. Common Tasks Playbook

### Adding a New Endpoint

1. Define schemas in `services/<service>/schemas.py`.
2. Add DB functions in `services/<service>/db.py` (if state changes needed).
3. Add the handler in `services/<service>/main.py`:
   - Use `Depends(_get_current_principal)` for auth.
   - Use `Depends(_require_admin)` if admin-only.
   - Call `_check_2fa()` for sensitive operations.
   - Record audit events via `record_audit_event()`.
   - Record metrics via `record_business_event()`.
   - Return errors in `{"error": {"code": ..., "message": ...}}` format.
4. Add tests in `tests/test_<service>.py`.
5. Update `specs/api-contracts.md`.

### Adding a New Service

1. Create `services/<service>/` with `main.py`, `schemas.py`, `db.py`.
2. Follow the lifespan pattern for app initialization.
3. Configure `get_settings(service_name="<service>", default_port=<port>)`.
4. Install security middleware: `install_http_security(app, settings)`.
5. Mount metrics: `mount_metrics_endpoint(app, settings)`.
6. Configure logging: `configure_structured_logging(...)`.
7. Configure alerting: `configure_alerting(settings)`.
8. Add to `infra/docker-compose.local.yml`.
9. Add proxy rules to `services/gateway/default.conf`.
10. Add tests in `tests/test_<service>.py`.
11. Update `specs/architecture.md` and `README.md`.

### Modifying Database Schema

1. Edit table definition in `services/common/db/metadata.py`.
2. Create migration: `alembic revision -m "add_column_to_table"`.
3. Edit the generated migration file with `upgrade()` and `downgrade()` functions.
4. Test: `alembic downgrade base && alembic upgrade head && alembic downgrade base && alembic upgrade head`.
5. Update `specs/database-schema.md`.
6. Update affected service DB functions and schemas.
7. Update test fixtures.

### Fixing a Failing Test

1. Read the test file to understand the fixture setup and assertions.
2. Check if the test uses `FakeUser`/`FakeWallet` named tuples — ensure field names match current schema.
3. Check if settings patches match current `Settings` fields.
4. Run the specific test: `pytest tests/test_<file>.py::<test_name> -v`.
5. If migration-related, ensure `DATABASE_URL` is set and PostgreSQL is accessible.

### Debugging Production-Like Issues Locally

1. Start full stack: `docker compose -f infra/docker-compose.local.yml up -d`.
2. Check service health: `curl http://localhost:<port>/health`.
3. Check readiness: `curl http://localhost:<port>/readiness` (if exposed).
4. Check metrics: `curl http://localhost:<port>/metrics`.
5. Tail logs: `docker compose -f infra/docker-compose.local.yml logs -f <service>`.
6. Query audit logs: `SELECT * FROM audit_logs WHERE action = '...' ORDER BY created_at DESC LIMIT 10;`.
7. Trace requests by `request_id` across service logs.

### Integrating a New Third-Party Service

1. Add configuration variables to `services/common/config.py` (with `_FILE` variant).
2. Add `.env.*.example` templates with the new variables.
3. Create a client module in the relevant service (e.g., `services/wallet/new_client.py`).
4. Add readiness check in `services/common/readiness.py` if the dependency is critical.
5. Add tests with mocked client responses.
6. Document in `specs/architecture.md`.

---

## 16. Glossary

| Term | Definition |
|------|-----------|
| **RWA** | Real-World Asset — physical or financial asset tokenized on-chain |
| **tapd** | Taproot Assets Daemon — daemon for issuing and managing Taproot Assets on Bitcoin |
| **LND** | Lightning Network Daemon — Lightning node implementation |
| **BIP-86** | Bitcoin Improvement Proposal for Taproot single-key derivation paths |
| **BIP-84** | Bitcoin Improvement Proposal for native SegWit (bech32) derivation paths |
| **P2WSH** | Pay-to-Witness-Script-Hash — Bitcoin script type used for multisig |
| **2-of-3 multisig** | Escrow requiring 2 of 3 signatures (buyer, seller, platform) |
| **Escrow** | On-chain multisig address holding trade funds until settlement |
| **TOTP** | Time-based One-Time Password (RFC 6238) — used for 2FA |
| **JTI** | JWT ID — unique identifier for each token, used for revocation tracking |
| **NIP-01** | Nostr Implementation Possibility — basic event format and signature scheme |
| **Schnorr signature** | Bitcoin Taproot signature scheme (secp256k1) |
| **HSM** | Hardware Security Module — for production key management |
| **AES-256-GCM** | Authenticated encryption for wallet seed storage |
| **On-ramp** | Fiat-to-Bitcoin conversion service (Bank Bridge, Card Bridge) |
| **Treasury** | Platform fund ledger fed by marketplace trading fees |
| **Yield accrual** | Daily calculation of returns on token holdings |
| **Referral code** | Unique code for tracking user referrals and bonus distribution |
| **regtest** | Bitcoin regression test network (local, instant block mining) |
| **signet** | Bitcoin signed test network (used for public beta) |
| **testnet** | Bitcoin public test network (used for staging) |
| **mainnet** | Bitcoin production network |
| **ENV_PROFILE** | Environment variable selecting the deployment profile |
| **`_FILE` convention** | Secret resolution pattern: `VAR_FILE` (path) takes precedence over `VAR` (inline) |

---

## 17. Unknowns / Inferred Assumptions

### Verified from Repository

- All 6 services + admin + gateway + common module structure and code.
- 19 database tables defined in `services/common/db/metadata.py`.
- 11 Alembic migrations with full up/down support.
- API contracts documented in `specs/api-contracts.md`.
- 7 security findings tracked in `specs/security-findings-registry.md`.
- CI pipeline defined in `.github/workflows/ci.yml`.
- Docker Compose configurations for local, observability, and public beta.
- Deployment runbooks for public beta and mainnet in `deploy/`.
- Rate limiting rules defined in `specs/abuse-controls.md` and `common/security.py`.
- Structured logging, metrics, alerting, and audit trail fully implemented.
- Test suite with 20 test files covering all services and cross-cutting concerns.

### Inferred (High Confidence)

- **Auth service runs on port 8000** behind the gateway (endpoints are `/auth/*` routed by Nginx).
- **Frontend is not yet implemented** — only a detailed spec exists in `specs/frontend-spec.md`.
- **No Makefile or pyproject.toml** exists — dependency management uses plain `requirements.txt` files.
- **No pre-commit hooks** — secret scanning and linting are CI-only.
- **Services share a single PostgreSQL database** — no per-service database isolation despite microservice architecture.
- **The gateway proxies all traffic** — services are not directly exposed in production.

### Unknown (Insufficient Evidence)

- **Exact per-service `requirements.txt` files** — only `scripts/requirements-migrations.txt` and `tests/requirements.txt` were found; individual service dependency manifests were not located.
- **Exact Dockerfile for each service** — only `services/gateway/Dockerfile` was confirmed; other services may use compose `command` directives instead.
- **Fiat on-ramp webhook handling** — providers are defined but callback/webhook processing is not visible.
- **KYC external provider integration** — DB queries exist but no external API calls.
- **HSM vendor/implementation** — abstract interface exists but no concrete provider.
- **Production deployment tooling** — mainnet runbook describes process but no CI/CD pipeline for production was found.
- **WebSocket implementation details** — marketplace `main.py` imports WebSocket support but full implementation was not deeply inspected.
- **Exact nginx gateway routing rules** — `default.conf` was not fully read.
- **`.env.*.example` template contents** — referenced but not read.

---

## Recommended First Reading Order

For an agent onboarding to this repository, read these files in this order:

1. [README.md](README.md) — Project overview and setup instructions
2. [specs/architecture.md](specs/architecture.md) — System architecture and service decomposition
3. [specs/api-contracts.md](specs/api-contracts.md) — Complete API reference
4. [specs/database-schema.md](specs/database-schema.md) — Database design
5. [services/common/config.py](services/common/config.py) — Central configuration and settings
6. [services/common/db/metadata.py](services/common/db/metadata.py) — All table definitions
7. [services/common/custody.py](services/common/custody.py) — Wallet encryption (security-critical)
8. [services/common/security.py](services/common/security.py) — Rate limiting and data filtering
9. [services/common/events.py](services/common/events.py) — Event bus architecture
10. [services/auth/main.py](services/auth/main.py) — Auth patterns (reference implementation)
11. [services/marketplace/escrow.py](services/marketplace/escrow.py) — Multisig escrow (financial-critical)
12. [specs/security-findings-registry.md](specs/security-findings-registry.md) — Known vulnerabilities
13. [specs/abuse-controls.md](specs/abuse-controls.md) — Rate limiting and abuse prevention
14. [alembic/env.py](alembic/env.py) — Migration environment configuration
15. [tests/test_auth.py](tests/test_auth.py) — Test patterns and fixture conventions
16. [deploy/mainnet/README.md](deploy/mainnet/README.md) — Production deployment and rollback
