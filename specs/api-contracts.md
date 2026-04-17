# API Contracts Specification

> Migration notice: portions of this spec still describe Taproot-era contracts. The current runtime uses Liquid / Elements semantics, including `liquid_asset_id`, confidential Liquid addresses, and PSET-based settlement flows.

## 1. General Conventions

| Property         | Value                                                   |
| :--------------- | :------------------------------------------------------ |
| Base URL         | `https://api.platform.example/v1`                       |
| Protocol         | HTTPS (TLS 1.3)                                         |
| Format           | JSON (`application/json`)                                |
| Authentication   | Bearer JWT in `Authorization` header                     |
| Pagination       | Cursor-based: `?cursor=<uuid>&limit=<int>` (default 20, max 100) |
| Rate Limiting    | 100 requests/min per user; 10 requests/min for auth endpoints |
| Error Format     | `{ "error": { "code": "string", "message": "string" } }` |

### Standard HTTP Status Codes

| Code  | Usage                                    |
| :---- | :--------------------------------------- |
| `200` | Success                                  |
| `201` | Resource created                          |
| `400` | Validation error / Bad request            |
| `401` | Missing or invalid authentication         |
| `403` | Insufficient permissions                  |
| `404` | Resource not found                        |
| `409` | Conflict (duplicate, state violation)     |
| `422` | Unprocessable entity                      |
| `429` | Rate limit exceeded                       |
| `500` | Internal server error                     |

---

## 2. Authentication Endpoints

### 2.1 Register

```
POST /auth/register
```

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "SecureP@ss123",
  "display_name": "Alice",
  "referrer_code": "AB12CD34EF"
}
```

**Response (201):**
```json
{
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "display_name": "Alice",
    "role": "user",
    "referral_code": "ZX90LM12NP",
    "created_at": "2026-04-07T12:00:00Z"
  },
  "tokens": {
    "access_token": "eyJ...",
    "refresh_token": "eyJ...",
    "expires_in": 900
  }
}
```

### 2.2 Login

```
POST /auth/login
```

**Request Body:**
```json
{
  "email": "user@example.com",
  "password": "SecureP@ss123"
}
```

**Response (200):** Same token structure as register.

### 2.3 Login with Nostr

```
POST /auth/nostr
```

**Request Body:**
```json
{
  "pubkey": "hex_pubkey_64chars",
  "signed_event": {
    "id": "event_id",
    "kind": 22242,
    "created_at": 1712505600,
    "content": "Sign-in challenge: <nonce>",
    "sig": "hex_signature"
  }
}
```

**Response (200):** Same token structure. Creates user on first login.

### 2.4 Refresh Token

```
POST /auth/refresh
```

**Request Body:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response (200):** Same token structure as register. The refresh token is rotated on every successful call.

### 2.5 Enable 2FA

```
POST /auth/2fa/enable
Authorization: Bearer <token>
```

**Response (200):**
```json
{
  "totp_uri": "otpauth://totp/Platform:user@example.com?secret=BASE32SECRET&issuer=Platform",
  "backup_codes": ["123456", "789012", "..."]
}
```

### 2.6 Verify 2FA

```
POST /auth/2fa/verify
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "totp_code": "123456"
}
```

### 2.7 Logout

```
POST /auth/logout
```

**Request Body:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response (200):**
```json
{
  "message": "Session revoked."
}
```

### 2.8 Onboarding Summary

```
GET /auth/onboarding/summary
Authorization: Bearer <token>
```

Returns the authenticated user's KYC status, configured custody posture, and the fiat on-ramp providers that can be launched from the client. The UI should show the response disclaimers before redirecting to any provider-hosted flow.

**Response (200):**
```json
{
  "user": {
    "id": "uuid",
    "email": "user@example.com",
    "display_name": "Alice",
    "role": "user",
    "created_at": "2026-04-07T12:00:00Z"
  },
  "kyc_status": "verified",
  "custody": {
    "configured_backend": "hsm",
    "signer_backend": "hsm",
    "state": "ready",
    "key_reference": "hsm:wallet-root",
    "signer_key_reference": "hsm:wallet-root",
    "seed_exportable": false,
    "server_compromise_impact": "Wallet seeds remain wrapped under the configured HSM-compatible key reference...",
    "disclaimers": [
      "HSM mode depends on externally managed key rotation and access policies."
    ]
  },
  "fiat_onramp_providers": [
    {
      "provider_id": "bank-bridge",
      "display_name": "Bank Bridge",
      "state": "ready",
      "supported_fiat_currencies": ["USD", "EUR", "GBP"],
      "supported_countries": ["US", "GB", "DE"],
      "payment_methods": ["bank_transfer"],
      "requires_kyc": true,
      "disclaimer": "Bank Bridge completes cardholder checks...",
      "external_handoff_url": "https://bank-bridge.partner.example/checkout"
    }
  ],
  "compliance_notices": [
    "Fiat purchases complete on a provider-hosted checkout outside platform custody."
  ]
}
```

### 2.9 Referral Summary

```
GET /auth/referrals/summary
Authorization: Bearer <token>
```

Returns the authenticated user's immutable referral code, referred accounts, and signup rewards credited after successful onboarding.

**Response (200):**
```json
{
  "referral_code": "ZX90LM12NP",
  "referrals_count": 2,
  "total_reward_sat": 100000,
  "referred_users": [
    {
      "id": "uuid",
      "email": "friend@example.com",
      "display_name": "Friend",
      "created_at": "2026-04-10T12:00:00Z"
    }
  ],
  "rewards": [
    {
      "id": "uuid",
      "referred_user_id": "uuid",
      "referred_display_name": "Friend",
      "reward_type": "signup_bonus",
      "amount_sat": 50000,
      "status": "credited",
      "eligibility_event": "kyc_verified",
      "credited_at": "2026-04-12T12:00:00Z",
      "created_at": "2026-04-12T12:00:00Z"
    }
  ]
}
```

---

## 3. Wallet Endpoints

### 3.1 Get Wallet

```
GET /wallet
Authorization: Bearer <token>
```

**Response (200):**
```json
{
  "wallet": {
    "id": "uuid",
    "onchain_balance_sat": 500000,
    "lightning_balance_sat": 150000,
    "token_balances": [
      {
        "token_id": "uuid",
        "asset_name": "Downtown Office Building",
        "symbol": "DOB",
        "balance": 50,
        "unit_price_sat": 10000,
        "accrued_yield_sat": 1200
      }
    ],
    "total_yield_earned_sat": 1200,
    "total_value_sat": 1151200
  }
}
```

### 3.2 Yield Summary

```
GET /wallet/yield/summary
Authorization: Bearer <token>
```

Returns accrued yield totals and the underlying accrual records. The service settles any pending full-day accruals before responding.

**Response (200):**
```json
{
  "yield_summary": {
    "total_yield_earned_sat": 1200,
    "by_token": [
      {
        "token_id": "uuid",
        "asset_name": "Downtown Office Building",
        "total_yield_sat": 1200
      }
    ],
    "accruals": [
      {
        "id": "uuid",
        "token_id": "uuid",
        "asset_name": "Downtown Office Building",
        "amount_sat": 1200,
        "quantity_held": 50,
        "reference_price_sat": 10000,
        "annual_rate_pct": 8.5,
        "accrued_from": "2026-04-10T12:00:00Z",
        "accrued_to": "2026-04-11T12:00:00Z",
        "created_at": "2026-04-11T12:00:00Z"
      }
    ]
  }
}
```

### 3.2 Get Transaction History

```
GET /wallet/transactions?cursor=<uuid>&limit=20&type=<type>
Authorization: Bearer <token>
```

**Response (200):**
```json
{
  "transactions": [
    {
      "id": "uuid",
      "type": "ln_receive",
      "amount_sat": 25000,
      "direction": "in",
      "status": "confirmed",
      "description": "Lightning deposit",
      "created_at": "2026-04-07T14:30:00Z"
    }
  ],
  "next_cursor": "uuid_or_null"
}
```

### 3.3 Get Custody Status

```
GET /wallet/custody
Authorization: Bearer <token>
```

Returns the current wallet record's custody backend, configured signer backend, derivation path, and security notes required by the onboarding and settings flows.

**Response (200):**
```json
{
  "configured_backend": "software",
  "wallet_backend": "software",
  "signer_backend": "software",
  "state": "ready",
  "key_reference": "sw:8b2a6f5d1d6b11b2",
  "signer_key_reference": "sw-signer:7200bcf18edc36cd",
  "derivation_path": "m/86'/1'/0'",
  "seed_exportable": true,
  "withdraw_requires_2fa": true,
  "server_compromise_impact": "Seeds are wrapped with an application-managed AES key...",
  "disclaimers": [
    "Software custody is intended for local, staging, or transitional deployments."
  ]
}
```

### 3.4 List Fiat On-Ramp Providers

```
GET /wallet/fiat/onramp/providers
Authorization: Bearer <token>
```

Returns provider discovery data and compliance notices that the frontend must render before initiating an external checkout flow.

**Response (200):**
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
      "disclaimer": "Bank Bridge completes cardholder checks and may ask the user to complete provider-hosted KYC...",
      "external_handoff_url": "https://bank-bridge.partner.example/checkout"
    }
  ],
  "compliance_notices": [
    "Fiat purchases complete on a provider-hosted checkout outside platform custody.",
    "Provider fees, exchange rates, KYC, and settlement timelines are determined by the selected partner."
  ]
}
```

### 3.5 Create Fiat On-Ramp Session

```
POST /wallet/fiat/onramp/session
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "provider_id": "bank-bridge",
  "fiat_currency": "USD",
  "fiat_amount": "150.00",
  "country_code": "US",
  "return_url": "https://app.platform.example/wallet/fiat/complete",
  "cancel_url": "https://app.platform.example/wallet/fiat/cancel"
}
```

Creates an external handoff session and pre-generates the on-chain deposit address that will receive the BTC purchase. If the provider requires KYC and the user is not verified, the API returns a provider-specific conflict instead of redirecting.

**Response (201):**
```json
{
  "session_id": "uuid",
  "provider_id": "bank-bridge",
  "state": "pending_redirect",
  "handoff_url": "https://bank-bridge.partner.example/checkout?...",
  "deposit_address": "bcrt1p...",
  "destination_wallet_id": "uuid",
  "expires_at": "2026-04-15T12:00:00Z",
  "disclaimer": "Bank Bridge completes cardholder checks...",
  "compliance_action": "review_terms"
}
```

**Provider-specific failure example (409):**
```json
{
  "error": {
    "code": "provider_kyc_required",
    "message": "Bank Bridge requires a verified KYC profile before launching checkout."
  }
}
```

### 3.6 Create Lightning Invoice

```
POST /wallet/lightning/invoice
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "amount_sat": 50000,
  "description": "Fund wallet"
}
```

**Response (201):**
```json
{
  "payment_request": "lnbc500u1p...",
  "payment_hash": "hex_hash",
  "expires_at": "2026-04-07T15:30:00Z"
}
```

### 3.7 Pay Lightning Invoice

```
POST /wallet/lightning/pay
Authorization: Bearer <token>
X-2FA-Code: 123456 (required)
```

**Request Body:**
```json
{
  "payment_request": "lnbc500u1p..."
}
```

**Response (200):**
```json
{
  "payment_hash": "hex_hash",
  "amount_sat": 50000,
  "fee_sat": 12,
  "status": "confirmed"
}
```

### 3.5 Get Deposit Address (On-chain)

```
POST /wallet/onchain/address
Authorization: Bearer <token>
```

**Response (201):**
```json
{
  "address": "bc1p...",
  "type": "taproot"
}
```

### 3.6 Withdraw On-chain

```
POST /wallet/onchain/withdraw
Authorization: Bearer <token>
X-2FA-Code: 123456 (required)
```

**Request Body:**
```json
{
  "address": "bc1q...",
  "amount_sat": 100000,
  "fee_rate_sat_vb": 5
}
```

**Response (200):**
```json
{
  "txid": "hex_txid",
  "amount_sat": 100000,
  "fee_sat": 705,
  "status": "pending"
}
```

---

## 4. Tokenization Endpoints

### 4.1 Submit Asset for Tokenization

```
POST /assets
Authorization: Bearer <token>
Role: seller
```

**Request Body:**
```json
{
  "name": "Downtown Office Building",
  "description": "3-story commercial office building in central business district...",
  "category": "real_estate",
  "valuation_sat": 100000000,
  "documents_url": "https://storage.example.com/docs/abc123"
}
```

**Response (201):**
```json
{
  "asset": {
    "id": "uuid",
    "name": "Downtown Office Building",
    "status": "pending",
    "order_type": "stop_limit",
    "created_at": "2026-04-07T12:00:00Z"
  }
    "trigger_price_sat": 10500,
    "triggered_at": null,
    "is_triggered": false,
}
```

### 4.2 Get Asset Details

```

### 7.6 Referral Reporting

```
GET /referrals/summary
Authorization: Bearer <admin-token>
```

Returns platform-wide referral reward totals.

```
GET /referrals/{user_id}
Authorization: Bearer <admin-token>
```

Returns one user's referral code, referred users, and credited rewards.

### 7.7 Yield Reporting

```
GET /yield/summary
Authorization: Bearer <admin-token>
```

Returns platform-wide yield totals.

```
GET /yield/{user_id}
Authorization: Bearer <admin-token>
```

Returns a user's yield totals, grouped-by-token amounts, and individual accrual records.
GET /assets/{asset_id}
Authorization: Bearer <token>
```

**Response (200):**
```json
{
  "asset": {
    "id": "uuid",
    "owner_id": "uuid",
    "name": "Downtown Office Building",
    "description": "...",
    "category": "real_estate",
    "valuation_sat": 100000000,
    "ai_score": 78.5,
    "ai_analysis": {
      "risk_level": "moderate",
      "projected_roi_annual": 7.2,
      "market_timing": "favorable",
      "summary": "Strong location with consistent occupancy rates..."
    },
    "projected_roi": 7.2,
    "status": "approved",
    "created_at": "2026-04-07T12:00:00Z"
  }
}
```

### 4.3 List Assets

```
GET /assets?status=<status>&category=<category>&cursor=<uuid>&limit=20
Authorization: Bearer <token>
```

### 4.4 Request AI Evaluation

```
POST /assets/{asset_id}/evaluate
Authorization: Bearer <token>
Role: seller (owner only)
```

**Response (202):**
```json
{
  "message": "Evaluation started",
  "estimated_completion": "2026-04-07T12:05:00Z"
}
```

The evaluation runs asynchronously. Results are stored in the asset's `ai_score` and `ai_analysis` fields. A `ai.evaluation.complete` event is published on completion.

### 4.5 Tokenize Approved Asset

```
POST /assets/{asset_id}/tokenize
Authorization: Bearer <token>
Role: seller (owner only)
```

**Request Body:**
```json
{
  "taproot_asset_id": "hex_id",
  "total_supply": 1000,
  "unit_price_sat": 100000
}
```

**Preconditions**:
- Asset status must be `approved`.
- `taproot_asset_id` must resolve in `tapd` and the Taproot asset amount must match `total_supply`.

**Response (201):**
```json
{
  "asset": {
    "id": "uuid",
    "status": "tokenized",
    "token": {
      "id": "uuid",
      "taproot_asset_id": "hex_id",
      "total_supply": 1000,
      "circulating_supply": 1000,
      "unit_price_sat": 100000,
      "issuance_metadata": {
        "asset_id": "hex_id",
        "asset_name": "Downtown Office Building",
        "genesis_point": "hex_outpoint:0",
        "meta_hash": "hex_meta_hash"
      },
      "minted_at": "2026-04-07T12:10:00Z"
    }
  }
}
```

All issued fractions are credited to the originating seller's token balance so they can be listed on the marketplace immediately after tokenization.

---

## 5. Marketplace Endpoints

### 5.1 Place Order

```
POST /orders
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "token_id": "uuid",
  "side": "buy",
  "quantity": 10,
  "price_sat": 100000
}
```

**Preconditions**:
- `buy` orders: user must have sufficient sats (quantity × price)
- `sell` orders: user must have sufficient token balance

**Response (201):**
```json
{
  "order": {
    "id": "uuid",
    "token_id": "uuid",
    "side": "buy",
    "quantity": 10,
    "price_sat": 100000,
    "filled_quantity": 0,
    "status": "open",
    "created_at": "2026-04-07T13:00:00Z"
  }
}
```

### 5.2 List Orders

```
GET /orders?token_id=<uuid>&side=<buy|sell>&status=<status>&cursor=<uuid>&limit=20
Authorization: Bearer <token>
```

### 5.3 Get Order Book

```
GET /orderbook/{token_id}
Authorization: Bearer <token>
```

**Response (200):**
```json
{
  "token_id": "uuid",
  "bids": [
    { "price_sat": 100000, "total_quantity": 50 },
    { "price_sat": 99000, "total_quantity": 120 }
  ],
  "asks": [
    { "price_sat": 101000, "total_quantity": 30 },
    { "price_sat": 102000, "total_quantity": 75 }
  ],
  "last_trade_price_sat": 100500,
  "volume_24h": 500
}
```

### 5.4 Cancel Order

```
DELETE /orders/{order_id}
Authorization: Bearer <token>
```

**Preconditions**: Order must be `open` or `partially_filled` and belong to the authenticated user.

**Response (200):**
```json
{
  "order": {
    "id": "uuid",
    "status": "cancelled"
  }
}
```

### 5.5 Get Trade History

```
GET /trades?token_id=<uuid>&cursor=<uuid>&limit=20
Authorization: Bearer <token>
```

**Response (200):**
```json
{
  "trades": [
    {
      "id": "uuid",
      "token_id": "uuid",
      "quantity": 10,
      "price_sat": 100000,
      "total_sat": 1000000,
      "fee_sat": 5000,
      "status": "settled",
      "created_at": "2026-04-07T13:30:00Z",
      "settled_at": "2026-04-07T13:31:00Z"
    }
  ],
  "next_cursor": "uuid_or_null"
}
```

### 5.6 Get Escrow Details

```
GET /escrows/{trade_id}
Authorization: Bearer <token>
```

**Response (200):**
```json
{
  "escrow": {
    "id": "uuid",
    "trade_id": "uuid",
    "multisig_address": "lq1...",
    "locked_amount_sat": 1005000,
    "funding_txid": "hex_txid",
    "release_txid": null,
    "refund_txid": null,
    "status": "funded",
    "expires_at": "2026-04-08T13:30:00Z",
    "settlement_metadata": {
      "seller_payout_amount_sat": 1000000,
      "marketplace_fee_amount_sat": 0,
      "fee_reserve_sat": 5000,
      "buyer_refund_address": "el1...",
      "seller_payout_address": "el1..."
    }
  }
}
```

Notes:
- `locked_amount_sat` is the exact buyer funding amount for the escrow address.
- The marketplace funding watcher updates `funding_txid` automatically; reads do not trigger funding detection.
- `status` progresses through `created` -> `funded` -> `inspection_pending` -> `released`, with `refunded`, `disputed`, and `expired` as exception paths.

### 5.7 Sign Escrow Release

```
POST /escrows/{trade_id}/sign
Authorization: Bearer <token>
X-2FA-Code: 123456 (required)
```

**Request Body:**
```json
{
  "pset": "cHNldP8B..."
}
```

**Response (200):**
```json
{
  "escrow": {
    "id": "uuid",
    "status": "inspection_pending",
    "release_txid": null
  }
}
```

Signing policy:
- Happy path release requires `seller + buyer`.
- The seller signs first while the escrow is `funded`, which moves the escrow to `inspection_pending`.
- The buyer signs second after delivery verification. The release transaction only broadcasts after both participant signatures are present.
- The platform key is not used on the happy path.

### 5.8 Dispute Trade

```
POST /trades/{trade_id}/dispute
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "reason": "Seller did not provide the agreed documentation."
}
```

---

## 6. Education Endpoints

### 6.1 List Courses

```
GET /courses?category=<category>&difficulty=<level>&cursor=<uuid>&limit=20
```

No authentication required (public catalog).

**Response (200):**
```json
{
  "courses": [
    {
      "id": "uuid",
      "title": "Bitcoin Fundamentals",
      "description": "Learn the basics of...",
      "category": "bitcoin",
      "difficulty": "beginner"
    }
  ],
  "next_cursor": "uuid_or_null"
}
```

### 6.2 Get Course Detail

```
GET /courses/{course_id}
```

### 6.3 Enroll in Course

```
POST /courses/{course_id}/enroll
Authorization: Bearer <token>
```

**Response (201):**
```json
{
  "enrollment": {
    "id": "uuid",
    "course_id": "uuid",
    "progress": 0,
    "enrolled_at": "2026-04-07T14:00:00Z"
  }
}
```

### 6.4 Update Progress

```
PATCH /enrollments/{enrollment_id}
Authorization: Bearer <token>
```

**Request Body:**
```json
{
  "progress": 45.5
}
```

### 6.5 Get Treasury Summary (Public)

```
GET /treasury/summary
```

**Response (200):**
```json
{
  "total_balance_sat": 15000000,
  "total_collected_sat": 25000000,
  "total_disbursed_sat": 10000000,
  "recent_entries": [
    {
      "type": "fee_income",
      "amount_sat": 5000,
      "source_trade_id": "uuid",
      "created_at": "2026-04-07T13:31:00Z"
    }
  ]
}
```

### 6.6 Get Treasury Ledger (Auditor)

```
GET /treasury/ledger?cursor=<uuid>&limit=50
Authorization: Bearer <token>
Role: auditor | admin
```

---

## 7. Admin Endpoints

### 7.1 List Users

```
GET /admin/users?role=<role>&cursor=<uuid>&limit=20
Authorization: Bearer <token>
Role: admin
```

### 7.2 Update User Role

```
PATCH /admin/users/{user_id}
Authorization: Bearer <token>
Role: admin
```

**Request Body:**
```json
{
  "role": "seller"
}
```

### 7.3 Resolve Dispute

```
POST /trades/{trade_id}/dispute/resolve
Authorization: Bearer <token>
Role: admin
```

**Request Body:**
```json
{
  "resolution": "refund",
  "pset": "cHNldP8B..."
}
```

**Resolution options**: `refund`, `release`

Dispute settlement policy:
- `release` uses `seller + platform`.
- `refund` uses `buyer + platform`.
- The dispute endpoint builds and broadcasts a real Liquid settlement transaction, then persists `release_txid` or `refund_txid` on the escrow record.

### 7.4 Create Course

```
POST /admin/courses
Authorization: Bearer <token>
Role: admin
```

### 7.5 Disburse Treasury Funds

```
POST /admin/treasury/disburse
Authorization: Bearer <token>
Role: admin
X-2FA-Code: 123456 (required)
```

**Request Body:**
```json
{
  "amount_sat": 500000,
  "description": "Funding Q2 2026 educational program"
}
```

---

## 8. WebSocket Endpoints

### 8.1 Real-Time Price Feed

```
WS /ws/prices/{token_id}
```

**Outbound Message:**
```json
{
  "event": "price_update",
  "data": {
    "token_id": "uuid",
    "last_price_sat": 101000,
    "bid": 100500,
    "ask": 101500,
    "volume_24h": 520,
    "timestamp": "2026-04-07T14:00:01Z"
  }
}
```

### 8.2 User Notifications

```
WS /ws/notifications
Authorization: Bearer <token> (via query param or first message)
```

**Outbound Messages:**
```json
{
  "event": "order_filled",
  "data": { "order_id": "uuid", "filled_quantity": 10 }
}
```
```json
{
  "event": "escrow_funded",
  "data": { "trade_id": "uuid", "txid": "hex" }
}
```
```json
{
  "event": "ai_evaluation_complete",
  "data": { "asset_id": "uuid", "ai_score": 78.5 }
}
```
