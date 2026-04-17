# Architecture Specification

> Migration notice: this architecture spec still contains Taproot / tapd-era diagrams and dependency descriptions. The live runtime now uses Elements / Liquid for issuance, wallet on-chain flows, and marketplace settlement.

## 1. High-Level System Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        CLIENT LAYER                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  Web App      │  │  Mobile PWA  │  │  Nostr Bot / CLI Client  │  │
│  │  (React/TS)   │  │  (React/TS)  │  │  (Python)                │  │
│  └──────┬───────┘  └──────┬───────┘  └────────────┬─────────────┘  │
└─────────┼──────────────────┼──────────────────────┼─────────────────┘
          │                  │                      │
          ▼                  ▼                      ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      API GATEWAY (Nginx / Traefik)                  │
│         Rate Limiting · JWT Auth · CORS · TLS Termination           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│  WALLET SERVICE  │ │ TOKENIZATION SVC │ │ MARKETPLACE SVC  │
│  (FastAPI)       │ │ (FastAPI)        │ │ (FastAPI)        │
│                  │ │                  │ │                  │
│ • BTC custody    │ │ • Asset ingest   │ │ • Order book     │
│ • LN payments    │ │ • AI evaluation  │ │ • Multisig escrow│
│ • Balance mgmt   │ │ • Token issuance │ │ • Trade matching │
│ • Tx history     │ │ • Taproot Assets │ │ • Fee extraction │
└────────┬─────────┘ └────────┬─────────┘ └────────┬─────────┘
         │                    │                    │
         ▼                    ▼                    ▼
┌─────────────────────────────────────────────────────────────────────┐
│                      SHARED INFRASTRUCTURE                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  PostgreSQL   │  │  Redis       │  │  Message Queue           │  │
│  │  (Primary DB) │  │  (Cache/Pub) │  │  (Redis Streams / Bull)  │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────────────┐
│                     BLOCKCHAIN LAYER                                │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────────┐  │
│  │  Bitcoin Core │  │  LND Node    │  │  Taproot Assets Daemon   │  │
│  │  (Full Node)  │  │  (Lightning) │  │  (tapd)                  │  │
│  └──────────────┘  └──────────────┘  └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
                               │
┌─────────────────────────────────────────────────────────────────────┐
│                     SOCIAL & EDUCATION LAYER                        │
│  ┌──────────────┐  ┌──────────────────────────────────────────┐    │
│  │  Nostr Relay  │  │  Education Service (FastAPI)             │    │
│  │  Connection   │  │  • Treasury management                   │    │
│  └──────────────┘  │  • Course catalog & enrollment            │    │
│                     │  • Fund allocation & auditing             │    │
│                     └──────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

## 2. Service Decomposition

### 2.1 Wallet Service

**Responsibility**: Bitcoin custody, Lightning Network payments, balance management.

| Component              | Description                                                        |
| :--------------------- | :----------------------------------------------------------------- |
| Key Manager            | HD wallet derivation (BIP-84/86), encrypted seed storage           |
| Lightning Client       | gRPC connection to LND for invoice creation, payment, and routing  |
| Balance Tracker        | Aggregates on-chain + Lightning + token balances per user          |
| Transaction Logger     | Persists every inbound/outbound movement with blockchain proof     |

**External Dependencies**: Bitcoin Core (RPC), LND (gRPC), Taproot Assets daemon.

**Security Boundaries**:
- Private keys never leave the Key Manager module.
- All seed material encrypted with AES-256-GCM at rest.
- Wallet operations require JWT + optional 2FA.

### 2.2 Tokenization Service

**Responsibility**: Asset onboarding, AI-driven evaluation, token issuance on Taproot Assets.

| Component              | Description                                                        |
| :--------------------- | :----------------------------------------------------------------- |
| Asset Ingestor         | Accepts asset metadata, documents, and valuation inputs            |
| AI Evaluator           | Calls LLM/ML model to assess risk, projected ROI, market timing   |
| Token Issuer           | Interfaces with `tapd` to mint Taproot Assets on-chain             |
| Fractionalization Engine | Splits a single asset token into N fractional units              |

**Flow**:
```
User submits asset → Ingestor validates → AI Evaluator scores →
  If approved → Token Issuer mints on Taproot Assets →
    Fractionalization Engine creates tradable units
```

### 2.3 Marketplace Service

**Responsibility**: Order matching, Multisig escrow, fee extraction.

| Component              | Description                                                        |
| :--------------------- | :----------------------------------------------------------------- |
| Order Book             | Maintains buy/sell orders for each tokenized asset                 |
| Trade Matcher          | Pairs compatible orders (price + quantity)                         |
| Multisig Escrow        | Creates 2-of-3 multisig (buyer, seller, platform) for each trade  |
| Fee Extractor          | Deducts platform commission and routes it to the Education Treasury|
| Settlement Engine      | Finalizes token transfer upon escrow release                       |

**Multisig Flow**:
```
1. Buyer places order → sats locked in 2-of-3 multisig address
2. Seller confirms availability → tokens held in escrow
3. Both parties sign → settlement executes atomically
4. Dispute? → Platform acts as arbiter (3rd key)
```

### 2.4 Education Service

**Responsibility**: Treasury management, educational resource delivery.

| Component              | Description                                                        |
| :--------------------- | :----------------------------------------------------------------- |
| Treasury Manager       | Receives fee allocations, tracks balances, authorizes disbursements|
| Course Manager         | CRUD for educational content and learning paths                    |
| Enrollment Tracker     | Manages user enrollments and progress                              |
| Audit Logger           | Publicly verifiable log of all treasury movements                  |

### 2.5 Nostr Integration Layer

**Responsibility**: Social communication, bot-driven notifications, decentralized identity.

| Component              | Description                                                      |
| :--------------------- | :--------------------------------------------------------------- |
| Relay Connector        | Maintains WebSocket connections to Nostr relays                  |
| Event Publisher        | Publishes marketplace events, alerts, and announcements          |
| Bot Handler            | Responds to DMs (NIP-04) for account/trade queries               |
| Identity Bridge        | Maps Nostr public keys to platform user accounts                  |

## 3. Inter-Service Communication

| Pattern               | Usage                                                             |
| :-------------------- | :---------------------------------------------------------------- |
| Synchronous (HTTP)    | Client → API Gateway → Services (request/response)               |
| Async Events (Redis Streams) | Service-to-service: trade matched, token minted, fee collected |
| gRPC                  | Services → LND and Taproot Assets daemon                         |
| WebSocket             | Real-time price updates and trade notifications to clients        |

### Event Bus Topics

| Topic                       | Producer            | Consumer(s)                     |
| :-------------------------- | :------------------ | :------------------------------ |
| `trade.matched`             | Marketplace         | Wallet, Education               |
| `token.minted`              | Tokenization        | Marketplace, Wallet             |
| `escrow.funded`             | Wallet              | Marketplace                     |
| `escrow.released`           | Marketplace         | Wallet, Tokenization            |
| `fee.collected`             | Marketplace         | Education                       |
| `ai.evaluation.complete`    | Tokenization        | Notification (Nostr)            |

## 4. Authentication & Authorization

### 4.1 Auth Flow

```
1. User registers with email/password OR Nostr keypair
2. Server issues JWT (access token: 15min, refresh token: 7d)
3. JWT contains: user_id, roles[], wallet_id
4. All API calls require Bearer token in Authorization header
5. Sensitive operations (withdraw, trade) require 2FA (TOTP)
```

### 4.2 Role-Based Access Control (RBAC)

| Role        | Permissions                                                    |
| :---------- | :------------------------------------------------------------- |
| `user`      | View balances, create orders, view courses                     |
| `seller`    | All `user` + submit assets for tokenization                    |
| `admin`     | All `seller` + manage users, resolve disputes, manage treasury |
| `auditor`   | Read-only access to treasury logs and platform metrics          |

## 5. Deployment Architecture

```
┌─────────────────────────────────────────────┐
│              Docker Compose / K8s            │
│                                              │
│  ┌─────────┐ ┌─────────┐ ┌─────────┐       │
│  │ wallet  │ │ token   │ │ market  │       │
│  │ :8001   │ │ :8002   │ │ :8003   │       │
│  └─────────┘ └─────────┘ └─────────┘       │
│  ┌─────────┐ ┌─────────┐                    │
│  │ educate │ │ nostr   │                    │
│  │ :8004   │ │ :8005   │                    │
│  └─────────┘ └─────────┘                    │
│                                              │
│  ┌────────────┐  ┌───────┐  ┌────────────┐ │
│  │ PostgreSQL │  │ Redis │  │ Nginx GW   │ │
│  │ :5432      │  │ :6379 │  │ :443       │ │
│  └────────────┘  └───────┘  └────────────┘ │
│                                              │
│  ┌────────────┐  ┌───────┐  ┌────────────┐ │
│  │ bitcoind   │  │ LND   │  │ tapd       │ │
│  │ :8332      │  │ :10009│  │ :10029     │ │
│  └────────────┘  └───────┘  └────────────┘ │
└─────────────────────────────────────────────┘
```

### Environment Strategy

| Environment | Purpose                    | Blockchain Network |
| :---------- | :------------------------- | :----------------- |
| `dev`       | Local development          | Regtest            |
| `staging`   | Integration testing        | Testnet / Signet   |
| `production`| Live deployment            | Mainnet            |

## 6. Security Architecture

### 6.1 Data Protection

- **At Rest**: AES-256-GCM for wallet seeds and private keys. PostgreSQL column-level encryption for PII.
- **In Transit**: TLS 1.3 enforced on all external endpoints. mTLS between internal services.
- **Secrets Management**: Environment variables injected via Docker secrets or Vault. Never committed to git.

### 6.2 Threat Model (Key Risks)

| Threat                          | Mitigation                                              |
| :------------------------------ | :------------------------------------------------------ |
| Key theft (server compromise)   | HSM or encrypted key store; Multisig limits single-key damage |
| API abuse / DDoS                | Rate limiting (100 req/min per user), WAF, IP throttling|
| SQL Injection                   | Parameterized queries via SQLAlchemy ORM                |
| Token replay                    | Short-lived JWTs, refresh token rotation, jti blacklist |
| Insider threat (admin abuse)    | Multisig 2-of-3 requires multiple signers               |
| Smart contract bugs             | Taproot Assets are UTXO-based (not Turing-complete), reducing attack surface |

### 6.3 Audit & Compliance

- All financial operations logged with immutable audit trail.
- Treasury movements published to Nostr for public verification.
- Quarterly security review of key management and access controls.
