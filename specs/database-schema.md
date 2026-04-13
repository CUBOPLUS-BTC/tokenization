# Database Schema Specification

## 1. Overview

- **RDBMS**: PostgreSQL 15+
- **ORM**: SQLAlchemy 2.0 with Alembic migrations
- **Naming Convention**: `snake_case` for tables and columns; plural table names
- **Timestamps**: All tables include `created_at` and `updated_at` (UTC, timezone-aware)
- **Soft Deletes**: Critical entities use `deleted_at` instead of hard deletes
- **UUIDs**: Primary keys use `UUID v4` for all user-facing entities

## 2. Entity Relationship Diagram

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   users      │────<│  wallets         │────<│  transactions    │
└──────────────┘     └──────────────────┘     └──────────────────┘
       │                                              │
       │              ┌──────────────────┐            │
       ├─────────────<│  assets          │            │
       │              └──────────────────┘            │
       │                     │                        │
       │              ┌──────────────────┐            │
       │              │  tokens          │────────────┘
       │              └──────────────────┘
       │                     │
       │              ┌──────────────────┐
       ├─────────────<│  orders          │
       │              └──────────────────┘
       │                     │
       │              ┌──────────────────┐
       │              │  trades          │
       │              └──────────────────┘
       │                     │
       │              ┌──────────────────┐
       │              │  escrows         │
       │              └──────────────────┘
       │
       │              ┌──────────────────┐
       ├─────────────<│  enrollments     │
       │              └──────────────────┘
       │                     │
       │              ┌──────────────────┐
       │              │  courses         │
       │              └──────────────────┘
       │
       │              ┌──────────────────┐
       └─────────────<│  nostr_identities│
                      └──────────────────┘
```

## 3. Table Definitions

### 3.1 `users`

Core user accounts.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK, DEFAULT uuid_generate_v4() | Unique user identifier               |
| `email`           | `VARCHAR(255)` | UNIQUE, nullable               | Email (nullable for Nostr-only users)|
| `password_hash`   | `VARCHAR(255)` | nullable                       | bcrypt hash                          |
| `display_name`    | `VARCHAR(100)` | NOT NULL                       | Public display name                  |
| `role`            | `VARCHAR(20)`  | NOT NULL, DEFAULT 'user'       | One of: user, seller, admin, auditor |
| `totp_secret`     | `VARCHAR(255)` | nullable                       | Encrypted TOTP secret for 2FA       |
| `is_verified`     | `BOOLEAN`      | DEFAULT false                  | Email/identity verification status   |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `updated_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `deleted_at`      | `TIMESTAMPTZ`  | nullable                       | Soft delete                          |

**Indexes**: `uq_users_email` (UNIQUE via UniqueConstraint), `ix_users_role`

---

### 3.2 `nostr_identities`

Links Nostr public keys to platform users.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `user_id`         | `UUID`         | FK → users.id, NOT NULL        | Owning user                          |
| `pubkey`          | `VARCHAR(64)`  | UNIQUE, NOT NULL               | Nostr hex public key (32 bytes)      |
| `relay_urls`      | `TEXT[]`       | nullable                       | Preferred relays for this identity   |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

**Indexes**: `uq_nostr_identities_pubkey` (UNIQUE via UniqueConstraint)

---

### 3.3 `wallets`

One wallet per user. Tracks aggregate balances.

| Column              | Type           | Constraints                    | Description                        |
| :------------------ | :------------- | :----------------------------- | :--------------------------------- |
| `id`                | `UUID`         | PK                             |                                    |
| `user_id`           | `UUID`         | FK → users.id, UNIQUE, NOT NULL| One wallet per user                |
| `onchain_balance_sat` | `BIGINT`     | DEFAULT 0                      | Confirmed on-chain balance (sats)  |
| `lightning_balance_sat`| `BIGINT`    | DEFAULT 0                      | Lightning channel balance (sats)   |
| `encrypted_seed`    | `BYTEA`        | NOT NULL                       | AES-256-GCM encrypted HD seed     |
| `derivation_path`   | `VARCHAR(50)`  | NOT NULL, DEFAULT "m/86'/0'/0'"| BIP-86 Taproot derivation path     |
| `created_at`        | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                    |
| `updated_at`        | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                    |

**Indexes**: `uq_wallets_user_id` (UNIQUE via UniqueConstraint)

---

### 3.4 `transactions`

Immutable ledger of all financial movements.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `wallet_id`       | `UUID`         | FK → wallets.id, NOT NULL      | Associated wallet                    |
| `type`            | `VARCHAR(30)`  | NOT NULL                       | `deposit`, `withdrawal`, `ln_send`, `ln_receive`, `escrow_lock`, `escrow_release`, `fee` |
| `amount_sat`      | `BIGINT`       | NOT NULL                       | Amount in satoshis                   |
| `direction`       | `VARCHAR(4)`   | NOT NULL                       | `in` or `out`                        |
| `status`          | `VARCHAR(20)`  | NOT NULL, DEFAULT 'pending'    | `pending`, `confirmed`, `failed`     |
| `txid`            | `VARCHAR(64)`  | nullable                       | Bitcoin transaction ID               |
| `ln_payment_hash` | `VARCHAR(64)`  | nullable                       | Lightning payment hash               |
| `description`     | `TEXT`         | nullable                       | Human-readable memo                  |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `confirmed_at`    | `TIMESTAMPTZ`  | nullable                       | When confirmed on-chain              |

**Indexes**: `ix_transactions_wallet_id`, `ix_transactions_type`, `ix_transactions_status`, `ix_transactions_created_at`

---

### 3.5 `assets`

Real-world assets submitted for tokenization.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `owner_id`        | `UUID`         | FK → users.id, NOT NULL        | User who submitted the asset         |
| `name`            | `VARCHAR(200)` | NOT NULL                       | Asset name                           |
| `description`     | `TEXT`         | NOT NULL                       | Detailed description                 |
| `category`        | `VARCHAR(50)`  | NOT NULL                       | `real_estate`, `commodity`, `invoice`, `art`, `other` |
| `valuation_sat`   | `BIGINT`       | NOT NULL                       | Estimated value in satoshis          |
| `documents_url`   | `TEXT`         | nullable                       | Link to supporting documents (S3/IPFS) |
| `ai_score`        | `DECIMAL(5,2)` | nullable                       | AI evaluation score (0-100)          |
| `ai_analysis`     | `JSONB`        | nullable                       | Full AI evaluation report            |
| `projected_roi`   | `DECIMAL(5,2)` | nullable                       | AI-projected annual return (%)       |
| `status`          | `VARCHAR(20)`  | NOT NULL, DEFAULT 'pending'    | `pending`, `evaluating`, `approved`, `rejected`, `tokenized` |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `updated_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

**Indexes**: `ix_assets_owner_id`, `ix_assets_status`, `ix_assets_category`

---

### 3.6 `tokens`

On-chain tokens representing fractional asset ownership.

| Column              | Type           | Constraints                    | Description                        |
| :------------------ | :------------- | :----------------------------- | :--------------------------------- |
| `id`                | `UUID`         | PK                             |                                    |
| `asset_id`          | `UUID`         | FK → assets.id, NOT NULL       | Parent asset                       |
| `taproot_asset_id`  | `VARCHAR(64)`  | UNIQUE, NOT NULL               | On-chain Taproot Asset ID          |
| `total_supply`      | `BIGINT`       | NOT NULL                       | Total fractional units minted      |
| `circulating_supply`| `BIGINT`       | NOT NULL, DEFAULT 0            | Units currently in circulation     |
| `unit_price_sat`    | `BIGINT`       | NOT NULL                       | Price per fractional unit (sats)   |
| `metadata`          | `JSONB`        | nullable                       | On-chain metadata                  |
| `minted_at`         | `TIMESTAMPTZ`  | DEFAULT NOW()                  | When tokens were minted            |
| `created_at`        | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                    |

**Indexes**: `ix_tokens_asset_id`, `uq_tokens_taproot_asset_id` (UNIQUE via UniqueConstraint)

---

### 3.7 `token_balances`

Tracks per-user ownership of each token.

| Column            | Type           | Constraints                         | Description                    |
| :---------------- | :------------- | :---------------------------------- | :----------------------------- |
| `id`              | `UUID`         | PK                                  |                                |
| `user_id`         | `UUID`         | FK → users.id, NOT NULL             |                                |
| `token_id`        | `UUID`         | FK → tokens.id, NOT NULL            |                                |
| `balance`         | `BIGINT`       | NOT NULL, DEFAULT 0, CHECK >= 0     | Number of fractional units held|
| `updated_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                       |                                |

**Constraints**: UNIQUE(`user_id`, `token_id`)
**Indexes**: `ix_token_balances_user_id`, `ix_token_balances_token_id`

---

### 3.8 `orders`

Buy and sell orders on the marketplace.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `user_id`         | `UUID`         | FK → users.id, NOT NULL        | Order creator                        |
| `token_id`        | `UUID`         | FK → tokens.id, NOT NULL       | Token being traded                   |
| `side`            | `VARCHAR(4)`   | NOT NULL                       | `buy` or `sell`                      |
| `quantity`        | `BIGINT`       | NOT NULL, CHECK > 0            | Number of fractional units           |
| `price_sat`       | `BIGINT`       | NOT NULL, CHECK > 0            | Price per unit in satoshis           |
| `filled_quantity` | `BIGINT`       | DEFAULT 0                      | Units already filled                 |
| `status`          | `VARCHAR(20)`  | NOT NULL, DEFAULT 'open'       | `open`, `partially_filled`, `filled`, `cancelled` |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `updated_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

**Indexes**: `ix_orders_token_id`, `ix_orders_user_id`

---

### 3.9 `trades`

Executed trades between two parties.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `buy_order_id`    | `UUID`         | FK → orders.id, NOT NULL       |                                      |
| `sell_order_id`   | `UUID`         | FK → orders.id, NOT NULL       |                                      |
| `token_id`        | `UUID`         | FK → tokens.id, NOT NULL       |                                      |
| `quantity`        | `BIGINT`       | NOT NULL                       | Units exchanged                      |
| `price_sat`       | `BIGINT`       | NOT NULL                       | Execution price per unit             |
| `total_sat`       | `BIGINT`       | NOT NULL                       | Total sats exchanged (qty × price)   |
| `fee_sat`         | `BIGINT`       | NOT NULL                       | Platform fee deducted                |
| `status`          | `VARCHAR(20)`  | NOT NULL, DEFAULT 'pending'    | `pending`, `escrowed`, `settled`, `disputed` |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `settled_at`      | `TIMESTAMPTZ`  | nullable                       |                                      |

**Indexes**: `ix_trades_token_id`, `ix_trades_status`

---

### 3.10 `escrows`

Multisig 2-of-3 escrow for each trade.

| Column              | Type           | Constraints                    | Description                        |
| :------------------ | :------------- | :----------------------------- | :--------------------------------- |
| `id`                | `UUID`         | PK                             |                                    |
| `trade_id`          | `UUID`         | FK → trades.id, UNIQUE, NOT NULL |                                   |
| `multisig_address`  | `VARCHAR(100)` | NOT NULL                       | Bitcoin multisig address           |
| `buyer_pubkey`      | `VARCHAR(66)`  | NOT NULL                       | Buyer's signing public key         |
| `seller_pubkey`     | `VARCHAR(66)`  | NOT NULL                       | Seller's signing public key        |
| `platform_pubkey`   | `VARCHAR(66)`  | NOT NULL                       | Platform arbiter public key        |
| `locked_amount_sat` | `BIGINT`       | NOT NULL                       | Sats locked in escrow              |
| `funding_txid`      | `VARCHAR(64)`  | nullable                       | On-chain funding transaction       |
| `release_txid`      | `VARCHAR(64)`  | nullable                       | On-chain release transaction       |
| `status`            | `VARCHAR(20)`  | NOT NULL, DEFAULT 'created'    | `created`, `funded`, `released`, `refunded`, `disputed` |
| `expires_at`        | `TIMESTAMPTZ`  | NOT NULL                       | Escrow expiration (auto-refund)    |
| `created_at`        | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                    |
| `updated_at`        | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                    |

**Indexes**: `uq_escrows_trade_id` (UNIQUE via UniqueConstraint), `ix_escrows_status`

---

### 3.11 `treasury`

Platform education fund ledger.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `source_trade_id` | `UUID`         | FK → trades.id, nullable       | Trade that generated this entry      |
| `type`            | `VARCHAR(20)`  | NOT NULL                       | `fee_income`, `disbursement`, `adjustment` |
| `amount_sat`      | `BIGINT`       | NOT NULL                       | Amount in satoshis                   |
| `balance_after_sat`| `BIGINT`      | NOT NULL                       | Running balance after this entry     |
| `description`     | `TEXT`         | nullable                       | Purpose or context                   |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

**Indexes**: `ix_treasury_type`, `ix_treasury_created_at`

---

### 3.12 `courses`

Educational content funded by the treasury.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `title`           | `VARCHAR(200)` | NOT NULL                       |                                      |
| `description`     | `TEXT`         | NOT NULL                       |                                      |
| `content_url`     | `TEXT`         | NOT NULL                       | Link to content (video, document)    |
| `category`        | `VARCHAR(50)`  | NOT NULL                       | `bitcoin`, `finance`, `programming`, `entrepreneurship` |
| `difficulty`      | `VARCHAR(20)`  | NOT NULL                       | `beginner`, `intermediate`, `advanced` |
| `is_published`    | `BOOLEAN`      | DEFAULT false                  |                                      |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `updated_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

---

### 3.13 `enrollments`

User enrollments in educational courses.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `user_id`         | `UUID`         | FK → users.id, NOT NULL        |                                      |
| `course_id`       | `UUID`         | FK → courses.id, NOT NULL      |                                      |
| `progress`        | `DECIMAL(5,2)` | DEFAULT 0, CHECK 0..100        | Completion percentage                |
| `enrolled_at`     | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `completed_at`    | `TIMESTAMPTZ`  | nullable                       |                                      |

**Constraints**: UNIQUE(`user_id`, `course_id`)

---

## 4. Migration Strategy

- Use **Alembic** for versioned schema migrations.
- Each migration file is atomic and reversible (`upgrade()` / `downgrade()`).
- Naming: `YYYYMMDD_HHMM_REVID_short_description.py` (matches `alembic.ini` `file_template`)
- All migrations tested against a clean database in CI before deployment.

## 5. Data Retention & Privacy

| Data Type          | Retention Policy                              |
| :----------------- | :-------------------------------------------- |
| Transaction logs   | Indefinite (immutable audit trail)            |
| User PII           | Soft-deleted on account closure; hard-deleted after 90 days |
| AI analysis reports| Retained while asset is active                |
| Session tokens     | Auto-expire (access: 15min, refresh: 7 days)  |
| Treasury ledger    | Indefinite (publicly auditable)               |
