# Mainnet Production Rollout Runbook

This document defines the release flow, rollback plan, and operational
responsibilities for promoting the RWA Tokenization Platform to **Bitcoin mainnet**.

---

## 1. Operational Ownership

| Role | Responsibility |
|:----------------------|:--------------------------------------------------------------|
| **Release Lead** | Owns the go/no-go decision for mainnet deployment. |
| **Infrastructure Lead** | Provisions secrets, node infrastructure, and DNS. |
| **On-Call SRE** | Monitors dashboards during and after rollout. |
| **Security Lead** | Signs off on audit findings and secret rotation. |
| **Product Owner** | Final business approval for mainnet exposure. |

> All launch and rollback decisions require explicit sign-off from the Release Lead
> **and** the Infrastructure Lead.

---

## 2. Release Flow

### 2.1 Preflight Checks

All checks must pass before deployment begins.

- [ ] Beta release-gate checklist is complete (see `deploy/public-beta/README.md`).
- [ ] All unresolved `CRITICAL` alerts in the beta environment have been resolved.
- [ ] Security audit scope has been reviewed and all findings addressed or exempted.
- [ ] Secret rotation has been executed for all `*_FILE` credentials.
- [ ] `BITCOIN_NETWORK` is set to `mainnet` in `infra/.env.mainnet`.
- [ ] Database migrations have been tested against a production-schema clone.
- [ ] Docker images are built from a tagged release (e.g. `v1.0.0-rc.1`).
- [ ] Node infrastructure (bitcoind, LND, Elements) is fully synced on mainnet.
- [ ] KYC verification thresholds are configured in `KYC_TRADE_THRESHOLD_SAT`.
- [ ] Backup and disaster-recovery plan is documented and tested.
- [ ] DNS and TLS certificates for the production domain are provisioned.
- [ ] Grafana dashboards imported: `Platform Overview`, `Mainnet Release Gate`.

### 2.2 Deployment Steps

```
1. Tag the release commit:
   git tag -s v1.0.0 -m "Mainnet release v1.0.0"

2. Build and push production images:
   docker compose -f infra/docker-compose.mainnet.yml build
   docker compose -f infra/docker-compose.mainnet.yml push

3. Run database migrations:
   ENV_PROFILE=production alembic upgrade head

4. Deploy services with zero-downtime rolling restart:
   docker compose -f infra/docker-compose.mainnet.yml up -d --remove-orphans

5. Verify service health:
   curl -s https://<production-host>/health | jq .
   curl -s https://<production-host>/ready  | jq .
```

### 2.3 Post-Deploy Verification

- [ ] All `/health` endpoints return `"status": "ok"`.
- [ ] All `/ready` endpoints return `"status": "ready"`.
- [ ] Auth: register + login + refresh cycle works.
- [ ] Wallet: balance read succeeds for a test account.
- [ ] Marketplace: order book loads, no stale data from beta.
- [ ] KYC: admin can query verification status.
- [ ] Metrics: Prometheus scrape targets are UP.
- [ ] Alertmanager: production receivers are routing correctly.
- [ ] Bitcoin Core: block height matches public explorers.
- [ ] LND + Elements: channels and assets visible on mainnet.

### 2.4 Rollback Procedure

If any verification check fails or a critical alert fires within the
first 60 minutes of mainnet exposure:

```
1. Announce rollback in the ops channel.

2. Stop mainnet services:
   docker compose -f infra/docker-compose.mainnet.yml down

3. If database migrations need to be reverted:
   ENV_PROFILE=production alembic downgrade -1

4. Re-deploy the previous known-good release:
   git checkout v<previous-version>
   docker compose -f infra/docker-compose.mainnet.yml up -d

5. Verify health and readiness on the rolled-back version.

6. Post-incident: file an RCA within 24 hours.
```

> **Rollback authority**: The Release Lead or Infrastructure Lead can
> initiate a rollback unilaterally.

---

## 3. Mainnet Secrets Boundary

All mainnet secrets are stored in Docker secret files and referenced via
`*_FILE` environment variables. They must **never** appear in git, `.env`
files, or CI logs.

| Secret | Env Variable | Mount Path |
|:-------------------------------|:-------------------------------|:---------------------------------|
| PostgreSQL password | `POSTGRES_PASSWORD_FILE` | `/run/secrets/postgres_password` |
| Bitcoin RPC password | `BITCOIN_RPC_PASSWORD_FILE` | `/run/secrets/bitcoin_rpc_password` |
| LND admin macaroon | `LND_MACAROON_PATH` | `/run/secrets/lnd_admin_macaroon` |
| LND TLS cert | `LND_TLS_CERT_PATH` | `/run/secrets/lnd_tls_cert` |
| Elements RPC password | `ELEMENTS_RPC_PASSWORD_FILE` | `/run/secrets/elements_rpc_password` |
| JWT signing secret | `JWT_SECRET_FILE` | `/run/secrets/jwt_secret` |
| Wallet encryption key | `WALLET_ENCRYPTION_KEY_FILE` | `/run/secrets/wallet_encryption_key` |
| OpenAI API key | `OPENAI_API_KEY_FILE` | `/run/secrets/openai_api_key` |
| Alert webhook URL | `ALERT_WEBHOOK_URL_FILE` | `/run/secrets/production_alert_webhook` |

---

## 4. KYC / Verification Requirements

Mainnet deployments must enforce KYC checks for high-value trades:

- The `KYC_TRADE_THRESHOLD_SAT` environment variable defines the satoshi
  threshold above which KYC verification is required (default: 10 000 000 sats).
- Users whose `kyc_status` is not `verified` are blocked from placing or
  matching orders whose total value exceeds the threshold.
- Admins can view and update KYC status via the `/auth/kyc/admin/{user_id}` endpoints.
- Verification status changes are recorded in the audit log.

---

## 5. Environment Profiles

| Profile | Network | KYC Enforced | Secrets Source |
|:-------------|:--------|:-------------|:---------------|
| `local` | regtest | No | `.env` file |
| `staging` | testnet | No | `.env.staging` |
| `beta` | signet | No | `.env.beta` |
| `production` | mainnet | **Yes** | Docker secrets |
