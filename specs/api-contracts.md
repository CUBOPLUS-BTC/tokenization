# API Contracts Specification

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
  "display_name": "Alice"
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
        "unit_price_sat": 10000
      }
    ],
    "total_value_sat": 1150000
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

### 3.3 Create Lightning Invoice

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

### 3.4 Pay Lightning Invoice

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
    "created_at": "2026-04-07T12:00:00Z"
  }
}
```

### 4.2 Get Asset Details

```
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
  "total_supply": 1000,
  "unit_price_sat": 100000
}
```

**Preconditions**: Asset status must be `approved`.

**Response (201):**
```json
{
  "token": {
    "id": "uuid",
    "asset_id": "uuid",
    "taproot_asset_id": "hex_id",
    "total_supply": 1000,
    "unit_price_sat": 100000,
    "minted_at": "2026-04-07T12:10:00Z"
  }
}
```

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
    "multisig_address": "bc1p...",
    "locked_amount_sat": 1000000,
    "funding_txid": "hex_txid",
    "status": "funded",
    "expires_at": "2026-04-08T13:30:00Z"
  }
}
```

### 5.7 Sign Escrow Release

```
POST /escrows/{trade_id}/sign
Authorization: Bearer <token>
X-2FA-Code: 123456 (required)
```

**Request Body:**
```json
{
  "partial_signature": "hex_signature"
}
```

**Response (200):**
```json
{
  "escrow": {
    "id": "uuid",
    "status": "released",
    "release_txid": "hex_txid"
  }
}
```

### 5.8 Dispute Trade

```
POST /escrows/{trade_id}/dispute
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
POST /admin/escrows/{trade_id}/resolve
Authorization: Bearer <token>
Role: admin
X-2FA-Code: 123456 (required)
```

**Request Body:**
```json
{
  "resolution": "refund_buyer",
  "notes": "Seller failed to provide documentation within 48 hours."
}
```

**Resolution options**: `refund_buyer`, `release_to_seller`

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
