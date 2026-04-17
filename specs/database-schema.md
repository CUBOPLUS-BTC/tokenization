# Database Schema Specification

> Migration notice: this schema spec predates the Liquid migration in Alembic `0013`. The live schema now uses `tokens.liquid_asset_id`, a Liquid wallet derivation default, and escrow settlement metadata for PSET flows.

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
       ├─────────────<│ refresh_token_   │            │
       │              │ sessions         │            │
       │              └──────────────────┘            │
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
| `referrer_id`     | `UUID`         | FK → users.id, nullable        | Immutable user that referred this account |
| `referral_code`   | `VARCHAR(12)`  | UNIQUE, NOT NULL               | Shareable code used during signup    |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `updated_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `deleted_at`      | `TIMESTAMPTZ`  | nullable                       | Soft delete                          |

**Indexes**: `uq_users_email` (UNIQUE via UniqueConstraint), `uq_users_referral_code` (UNIQUE via UniqueConstraint), `ix_users_role`, `ix_users_referrer_id`

**Referral rule**: a user can have at most one `referrer_id`, self-referrals are rejected, and the relation is captured only at account creation.

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

production
developer (local)
staging (pre produccion)

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
| `order_type`      | `VARCHAR(20)`  | NOT NULL, DEFAULT 'limit'      | `limit` or `stop_limit`              |
| `trigger_price_sat` | `BIGINT`     | nullable, CHECK > 0            | Trigger threshold for `stop_limit` orders |
| `triggered_at`    | `TIMESTAMPTZ`  | nullable                       | When the stop trigger activated      |
| `filled_quantity` | `BIGINT`       | DEFAULT 0                      | Units already filled                 |
| `status`          | `VARCHAR(20)`  | NOT NULL, DEFAULT 'open'       | `open`, `partially_filled`, `filled`, `cancelled` |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `updated_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

**Indexes**: `ix_orders_token_id`, `ix_orders_user_id`, `ix_orders_order_type`

**Trigger rule**: `stop_limit` orders remain non-matchable until the current reference price crosses the trigger. Buy stops activate when the reference price is greater than or equal to the trigger. Sell stops activate when the reference price is less than or equal to the trigger.

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
| `fee_sat`         | `BIGINT`       | NOT NULL                       | Platform fee routed on release       |
| `status`          | `VARCHAR(20)`  | NOT NULL, DEFAULT 'pending'    | `pending`, `escrowed`, `settled`, `disputed`, `cancelled` |
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
| `multisig_address`  | `VARCHAR(100)` | NOT NULL                       | Liquid confidential escrow address |
| `buyer_pubkey`      | `VARCHAR(66)`  | NOT NULL                       | Buyer's signing public key         |
| `seller_pubkey`     | `VARCHAR(66)`  | NOT NULL                       | Seller's signing public key        |
| `platform_pubkey`   | `VARCHAR(66)`  | NOT NULL                       | Platform arbiter public key        |
| `locked_amount_sat` | `BIGINT`       | NOT NULL                       | Buyer funding amount: seller payout + fee + fee reserve |
| `funding_txid`      | `VARCHAR(64)`  | nullable                       | On-chain funding transaction       |
| `release_txid`      | `VARCHAR(64)`  | nullable                       | On-chain release transaction       |
| `refund_txid`       | `VARCHAR(64)`  | nullable                       | On-chain refund transaction        |
| `collected_signatures` | `JSONB`     | nullable                       | Signature metadata grouped by settlement path |
| `settlement_metadata`  | `JSONB`     | nullable                       | Liquid settlement inputs, PSETs, payout addresses, and fee reserve metadata |
| `status`            | `VARCHAR(20)`  | NOT NULL, DEFAULT 'created'    | `created`, `funded`, `inspection_pending`, `released`, `refunded`, `disputed`, `expired` |
| `expires_at`        | `TIMESTAMPTZ`  | NOT NULL                       | Expiration for unfunded escrow rollback |

Behavior notes:
- Seller inventory is reserved when the escrow is created and restored if the escrow expires unfunded or is refunded after dispute.
- Funding detection is handled by an internal marketplace watcher, not by escrow reads.
- Release and refund terminal states are only persisted after the matching Liquid transaction is broadcast.

---

### 3.11 `referral_rewards`

Auditable credits generated when a referred user completes onboarding.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `referrer_id`     | `UUID`         | FK → users.id, NOT NULL        | User that receives the reward        |
| `referred_user_id`| `UUID`         | FK → users.id, NOT NULL        | User that completed onboarding       |
| `reward_type`     | `VARCHAR(20)`  | DEFAULT `signup_bonus`         | Reward rule identifier               |
| `amount_sat`      | `BIGINT`       | NOT NULL                       | Reward amount in satoshis            |
| `status`          | `VARCHAR(20)`  | DEFAULT `credited`             | `credited` or `reversed`             |
| `eligibility_event` | `VARCHAR(30)`| DEFAULT `kyc_verified`         | Event that unlocked the reward       |
| `metadata`        | `JSONB`        | nullable                       | Rule traceability payload            |
| `credited_at`     | `TIMESTAMPTZ`  | DEFAULT NOW()                  | When the reward was credited         |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

**Eligibility rule**: signup bonus is credited once, only after the referred account reaches KYC status `verified`.

---

### 3.12 `yield_accruals`

Daily yield ledger derived from token holdings and asset projected ROI.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             |                                      |
| `user_id`         | `UUID`         | FK → users.id, NOT NULL        | Beneficiary user                     |
| `token_id`        | `UUID`         | FK → tokens.id, NOT NULL       | Token generating the yield           |
| `annual_rate_pct` | `DECIMAL(5,2)` | NOT NULL                       | Annualized projected rate used       |
| `quantity_held`   | `BIGINT`       | NOT NULL                       | Token balance used for the accrual   |
| `reference_price_sat` | `BIGINT`   | NOT NULL                       | Latest market price or issue price fallback |
| `amount_sat`      | `BIGINT`       | NOT NULL                       | Yield amount accrued                 |
| `accrued_from`    | `TIMESTAMPTZ`  | NOT NULL                       | Beginning of the accrual window      |
| `accrued_to`      | `TIMESTAMPTZ`  | NOT NULL                       | End of the accrual window            |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

**Yield rule**: the platform accrues full-day yield only, using `floor(balance × reference_price × projected_roi × days / 36500)`.
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

### 3.14 `refresh_token_sessions`

Persists refresh-token JTIs so rotation and revocation can invalidate reused sessions.

| Column            | Type           | Constraints                    | Description                          |
| :---------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`              | `UUID`         | PK                             | Session record identifier            |
| `user_id`         | `UUID`         | FK → users.id, NOT NULL        | Owning user                          |
| `token_jti`       | `UUID`         | UNIQUE, NOT NULL               | Current refresh token JTI            |
| `replaced_by_jti` | `UUID`         | nullable                       | Next refresh token issued on rotation|
| `expires_at`      | `TIMESTAMPTZ`  | NOT NULL                       | Refresh-token expiry                 |
| `revoked_at`      | `TIMESTAMPTZ`  | nullable                       | When the session was revoked         |
| `created_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `updated_at`      | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

**Indexes**: `ix_refresh_token_sessions_user_id`, `ix_refresh_token_sessions_expires_at`, `uq_refresh_token_sessions_token_jti` (UNIQUE via UniqueConstraint)

---

### 3.15 `kyc_verifications`

Per-user identity verification state used to enforce KYC rules for high-value trades.

| Column             | Type           | Constraints                    | Description                          |
| :----------------- | :------------- | :----------------------------- | :----------------------------------- |
| `id`               | `UUID`         | PK                             |                                      |
| `user_id`          | `UUID`         | FK → users.id, UNIQUE, NOT NULL| One record per user                  |
| `status`           | `VARCHAR(20)`  | NOT NULL, DEFAULT 'pending'    | `pending`, `verified`, `rejected`, `expired` |
| `reviewed_by`      | `UUID`         | FK → users.id, nullable        | Admin who reviewed the submission    |
| `reviewed_at`      | `TIMESTAMPTZ`  | nullable                       | When the review was completed        |
| `rejection_reason` | `TEXT`         | nullable                       | Reason for rejection (if applicable) |
| `notes`            | `TEXT`         | nullable                       | Additional notes from user or admin  |
| `document_url`     | `TEXT`         | nullable                       | Link to uploaded KYC documents       |
| `metadata`         | `JSONB`        | nullable                       | Additional verification metadata     |
| `created_at`       | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |
| `updated_at`       | `TIMESTAMPTZ`  | DEFAULT NOW()                  |                                      |

**Indexes**: `ix_kyc_verifications_status`, `ix_kyc_verifications_user_id`, `uq_kyc_verifications_user_id` (UNIQUE via UniqueConstraint)

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
