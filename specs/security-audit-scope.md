# Security Audit Scope

> Migration notice: this scope document still references some Taproot / tapd infrastructure assumptions. The current runtime dependency for asset issuance and Liquid settlement is Elements RPC.
> Defines the scope, assets, and areas of focus for security audits of the
> OpenProof asset-tokenization platform.

---

## 1. Audit Objective

Identify vulnerabilities, misconfigurations, and architectural weaknesses
across the authentication, wallet, escrow, and key-management subsystems that
could lead to:

- Unauthorized access or privilege escalation
- Loss, theft, or misuse of funds (on-chain or Lightning)
- Compromise of cryptographic key material
- Manipulation of trade execution or settlement

---

## 2. In-Scope Services & Components

### 2.1 Authentication & Identity (`services/auth`)

| Area | Files / Endpoints | Risk Level |
|---|---|---|
| Password hashing & timing-safe comparison | `main.py` – `register`, `login` | **Critical** |
| JWT issuance, rotation & revocation | `jwt_utils.py`, `main.py` – `refresh`, `logout` | **Critical** |
| Nostr-based authentication flow | `nostr_utils.py`, `main.py` – `nostr_login` | **High** |
| TOTP 2FA enrollment & verification | `main.py` – `enable_2fa_endpoint`, `verify_2fa_endpoint` | **High** |
| Role-based access control (RBAC) | `main.py` – `_require_roles`, role-check endpoints | **High** |
| Refresh-token session persistence | `db.py` – session table operations | **Medium** |

### 2.2 Wallet & Fund Management (`services/wallet`)

| Area | Files / Endpoints | Risk Level |
|---|---|---|
| HD seed generation & AES-256-GCM encryption | `key_manager.py` | **Critical** |
| On-chain withdrawal flow with 2FA | `main.py` – `withdraw_onchain` | **Critical** |
| Lightning invoice creation & payment | `main.py` – `create_invoice`, `pay_invoice` | **High** |
| LND gRPC credential handling | `lnd_client.py` | **High** |
| Balance integrity & double-spend prevention | `db.py` – balance mutation queries | **Critical** |
| Sensitive-data log redaction | `log_filter.py`, `common/security.py` | **Medium** |

### 2.3 Escrow & Settlement (`services/marketplace`)

| Area | Files / Endpoints | Risk Level |
|---|---|---|
| 2-of-3 multisig address derivation | `escrow.py` – `generate_2of3_multisig_address` | **Critical** |
| Escrow funding verification (Bitcoin RPC) | `main.py` – `_refresh_escrow_funding` | **Critical** |
| Partial-signature collection & release | `main.py` – escrow sign endpoint | **High** |
| Platform counter-signature derivation | `main.py` – `_derive_platform_release_signature` | **Critical** |
| Trade matching & order-book integrity | `main.py` – `place_order`, `db.py` – `find_best_match` | **High** |
| Dispute resolution & fund routing | `main.py` / `admin/main.py` – dispute endpoints | **High** |

### 2.4 Key Management & Cryptography

| Area | Files / Endpoints | Risk Level |
|---|---|---|
| AES-256-GCM key storage & rotation | `wallet/key_manager.py`, env-var/secret-file loading | **Critical** |
| JWT signing key management | `common/config.py` – `jwt_secret` / `jwt_secret_file` | **Critical** |
| Wallet encryption key management | `common/config.py` – `wallet_encryption_key` | **Critical** |
| Nostr private key handling | `common/config.py` – `nostr_private_key` | **High** |
| TLS certificate & macaroon storage | LND/tapd config paths | **High** |
| Secret-file resolution logic | `common/config.py` – `_resolve_secret` | **Medium** |

### 2.5 Admin & Treasury (`services/admin`)

| Area | Files / Endpoints | Risk Level |
|---|---|---|
| Treasury disbursement with 2FA | `main.py` – `disburse_treasury_endpoint` | **Critical** |
| User-role mutation (privilege escalation) | `main.py` – `update_user_role_endpoint` | **High** |
| Dispute resolution with escrow release | `main.py` – `resolve_escrow_dispute_endpoint` | **High** |

### 2.6 Shared Infrastructure (`services/common`)

| Area | Files | Risk Level |
|---|---|---|
| Secret hydration from env / file | `config.py` – `_resolve_secret`, `_hydrate_secrets_and_validate` | **High** |
| Sensitive-data redaction in logs | `security.py` – `SensitiveDataFilter`, `sanitize_for_logging` | **Medium** |
| Rate-limiting middleware | `security.py` – `RateLimitMiddleware` | **Medium** |
| Audit-event persistence | `audit.py` – `record_audit_event` | **Medium** |
| Request-ID propagation | `security.py` – `RequestContextMiddleware` | **Low** |

---

## 3. Out-of-Scope (for Initial Audit)

- Frontend / React client-side code
- Education & Nostr relay services (non-financial)
- Infrastructure-level concerns (Docker image hardening, Kubernetes RBAC)
- Third-party dependency CVE scanning (handled by Dependabot / Snyk)
- Physical or social-engineering attacks

---

## 4. Methodology

1. **Static Analysis** – Manual code review of all in-scope files listed above
2. **Configuration Review** – Verify env-var defaults, secret management,
   TLS settings, and CORS policies
3. **Authentication Testing** – Brute-force, credential-stuffing, token
   replay, and session-fixation scenarios
4. **Authorization Testing** – RBAC bypass, horizontal privilege escalation,
   IDOR on wallet/escrow/trade resources
5. **Cryptographic Review** – Key-derivation parameters, nonce reuse analysis,
   HMAC construction, bcrypt cost factors
6. **Business-Logic Testing** – Double-spend, order-book manipulation,
   escrow race conditions, fee bypass

---

## 5. Deliverables

| Deliverable | Format |
|---|---|
| Findings register with severity / owner / remediation | `specs/security-findings-registry.md` |
| Abuse-control definitions per endpoint class | `specs/abuse-controls.md` |
| Remediation verification evidence | Per-finding test cases |

---

## 6. Audit Cadence

- **Pre-launch**: Full scope audit as defined above
- **Quarterly**: Delta review of changed files in scope areas
- **Post-incident**: Targeted review of affected subsystem
