# Security Findings Registry

> Tracks all security findings, their owners, severity, and remediation
> status. Each finding is assigned a unique identifier and traced back
> to the audit scope area that surfaced it.

---

## 1. Finding Severity Levels

| Level | Description | SLA |
|---|---|---|
| **P0 – Critical** | Active exploitation possible; direct fund loss or key compromise | Fix within 24 hours |
| **P1 – High** | Exploitable under realistic conditions; privilege escalation or data leak | Fix within 7 days |
| **P2 – Medium** | Requires specific conditions; defence-in-depth gap | Fix within 30 days |
| **P3 – Low** | Informational; best-practice deviation | Fix in next sprint |

---

## 2. Findings Register

### F-001: Default JWT Secret in Non-Production Code Paths

| Field | Value |
|---|---|
| **ID** | F-001 |
| **Severity** | P0 – Critical |
| **Scope Area** | Auth – JWT signing key management |
| **Owner** | Auth Service Lead |
| **Description** | `_jwt_secret()` falls back to `"dev-secret-change-me"` when `jwt_secret` is `None`. If this fallback is reachable in staging/production, all JWTs can be forged. |
| **Affected Files** | `services/auth/main.py:164`, `services/wallet/main.py:170`, `services/marketplace/main.py:153`, `services/tokenization/main.py:158`, `services/admin/main.py:153` |
| **Mitigation** | `Settings._hydrate_secrets_and_validate` raises `ValueError` for staging/production when `jwt_secret` is missing. |
| **Remediation** | Add a runtime guard in each `_jwt_secret()` that raises an error instead of returning the fallback when `env_profile != "local"`. Add an integration test. |
| **Status** | 🟡 Mitigated (config validator exists) – hardening recommended |

---

### F-002: Wallet Encryption Key Stored in Environment Variable

| Field | Value |
|---|---|
| **ID** | F-002 |
| **Severity** | P1 – High |
| **Scope Area** | Key Management – AES-256-GCM key storage |
| **Owner** | Wallet Service Lead |
| **Description** | `wallet_encryption_key` is loaded from an env var or a secret file. In container environments, env vars may be visible via `/proc/*/environ` or orchestrator metadata APIs. |
| **Affected Files** | `services/common/config.py:69-70`, `services/wallet/key_manager.py` |
| **Mitigation** | `wallet_encryption_key_file` option allows mounting from a Docker/K8s secret volume. |
| **Remediation** | Enforce `wallet_encryption_key_file` in production profiles; reject bare env-var usage. Document in deployment guide. |
| **Status** | 🟡 Open |

---

### F-003: In-Memory Rate Limiter Not Shared Across Replicas

| Field | Value |
|---|---|
| **ID** | F-003 |
| **Severity** | P2 – Medium |
| **Scope Area** | Shared Infrastructure – Rate limiting |
| **Owner** | Platform Team |
| **Description** | `RateLimitMiddleware` uses an in-process `dict` + `asyncio.Lock`. When a service runs multiple replicas behind a load balancer, each replica maintains independent counters, effectively multiplying the allowed rate by the replica count. |
| **Affected Files** | `services/common/security.py:128-181` |
| **Mitigation** | Gateway-level (`nginx`) rate limiting provides an additional layer. |
| **Remediation** | Migrate rate-limit state to Redis (e.g., sliding-window counter) for multi-replica deployments. |
| **Status** | 🟡 Open |

---

### F-004: Nostr Event Signature Validation Scope

| Field | Value |
|---|---|
| **ID** | F-004 |
| **Severity** | P1 – High |
| **Scope Area** | Auth – Nostr authentication flow |
| **Owner** | Auth Service Lead |
| **Description** | `validate_nostr_event` must verify the event kind, content, created_at freshness, and Schnorr signature. Insufficient validation could allow replayed or crafted events to log in as any Nostr identity. |
| **Affected Files** | `services/auth/nostr_utils.py` |
| **Mitigation** | Current implementation validates pubkey and signature. |
| **Remediation** | Verify event freshness (reject events older than 5 minutes), bind to a server-issued challenge nonce, and verify the event kind is the expected auth kind. |
| **Status** | 🟡 Open |

---

### F-005: TOTP Brute-Force Window

| Field | Value |
|---|---|
| **ID** | F-005 |
| **Severity** | P2 – Medium |
| **Scope Area** | Auth – TOTP 2FA |
| **Owner** | Auth Service Lead |
| **Description** | TOTP verification uses a `valid_window=1` (±1 step, 90-second window). Combined with the 10 req/60s rate limit on sensitive paths, an attacker can attempt ~10 codes per minute. A 6-digit TOTP has 1M possibilities, making brute-force impractical at this rate, but account-lockout after N failures would add defence-in-depth. |
| **Affected Files** | `services/auth/main.py` (2FA endpoints), `services/marketplace/main.py`, `services/wallet/main.py`, `services/admin/main.py` |
| **Mitigation** | Rate limiting on sensitive paths (10 req/60s per IP+path). |
| **Remediation** | Implement temporary account lockout after 5 consecutive failed 2FA attempts. |
| **Status** | 🟡 Open |

---

### F-006: Audit Log Write Failure Silently Continues

| Field | Value |
|---|---|
| **ID** | F-006 |
| **Severity** | P2 – Medium |
| **Scope Area** | Shared Infrastructure – Audit event persistence |
| **Owner** | Platform Team |
| **Description** | `record_audit_event` catches all exceptions and logs them but does not fail the parent request. An attacker who can cause audit-log failures (e.g., DB connection exhaustion) could perform actions without an audit trail. |
| **Affected Files** | `services/common/audit.py:89-102` |
| **Mitigation** | Audit failures now trigger a `CRITICAL` alert via `AlertDispatcher` in the updated `alerting.py`. |
| **Remediation** | For critical actions (treasury disbursement, escrow release), consider failing the request if the audit write fails. |
| **Status** | 🟡 Mitigated (alerting added) – hardening recommended |

---

### F-007: Platform Counter-Signature Uses HMAC Instead of ECDSA

| Field | Value |
|---|---|
| **ID** | F-007 |
| **Severity** | P1 – High |
| **Scope Area** | Escrow – Platform counter-signature |
| **Owner** | Marketplace Service Lead |
| **Description** | `_derive_platform_release_signature` uses `HMAC-SHA256(secret, escrow-release:{id}:{trade_id})` to produce a deterministic "signature". This is not a valid Bitcoin partial signature and cannot be verified by the Bitcoin script interpreter in a real multisig. |
| **Affected Files** | `services/marketplace/main.py:460-464` |
| **Mitigation** | Current escrow flow treats this as an application-level approval rather than an on-chain signature. |
| **Remediation** | Replace with proper secp256k1 ECDSA/Schnorr signing using the platform's private key for the multisig. |
| **Status** | 🟡 Open |

---

## 3. Summary Dashboard

| Severity | Total | Open | Mitigated | Resolved |
|---|---|---|---|---|
| P0 – Critical | 1 | 0 | 1 | 0 |
| P1 – High | 3 | 2 | 0 | 0 |
| P2 – Medium | 3 | 2 | 1 | 0 |
| P3 – Low | 0 | 0 | 0 | 0 |
| **Total** | **7** | **4** | **2** | **0** |

---

## 4. Remediation Workflow

```
 Found → Triaged → Assigned → In Progress → Verified → Closed
   │                                            │
   └── Won't Fix (with justification) ──────────┘
```

1. **Found**: Finding recorded in this registry with severity and scope area
2. **Triaged**: Owner assigned; remediation SLA set per severity table
3. **Assigned**: Owner confirms and creates implementation task
4. **In Progress**: Code changes underway
5. **Verified**: Fix confirmed via test case or manual validation
6. **Closed**: Finding marked as resolved with evidence link
