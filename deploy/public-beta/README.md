# Public Beta Operations

This directory defines the operational gate for the public beta environment that runs on Bitcoin `signet`.

## Goals

- Expose a non-production environment to beta participants without touching mainnet.
- Validate monitoring, dashboards, and alert routing before production exposure.
- Verify the tokenization, trading, and escrow lifecycle end to end before mainnet release.

## Environment Profile

- Profile: `beta`
- Bitcoin network: `signet`
- Compose entrypoint: `infra/docker-compose.public-beta.yml`
- Env template: `infra/.env.beta.example`
- Monitoring stack: `infra/docker-compose.observability.yml`

## Operator Onboarding

1. Copy `infra/.env.beta.example` to `infra/.env.beta`.
2. Replace placeholder hostnames with the beta cluster addresses.
3. Mount the required secrets referenced by `*_FILE`.
4. Point `BITCOIN_RPC_*`, `LND_*`, and `TAPD_*` to signet-connected infrastructure.
5. Start the platform with `docker compose -f infra/docker-compose.public-beta.yml up -d`.
6. Open Grafana at `http://<beta-observability-host>:3000` and confirm the `Platform Overview` and `Beta Release Gate` dashboards load.
7. Confirm Alertmanager routes are mapped to beta receivers before opening access to participants.

## Safety Boundaries

- Beta is signet-only. `BITCOIN_NETWORK` must remain `signet`; no mainnet credentials may be mounted.
- Wallets, escrow, and tokenization data in beta are non-production and must never be promoted into production databases.
- Secrets for beta must be isolated from production secrets and receivers.
- The beta alert receivers must stay separate from production on-call destinations.
- Faucet-funded balances are for validation only and must be treated as disposable test value.
- Mainnet launch remains blocked until the beta checklist below is completed and signed off.

## Release Gate Checklist

- [ ] `Platform Overview` dashboard shows green readiness across beta services.
- [ ] `Beta Release Gate` shows zero settlement failures for the previous 24 hours.
- [ ] Auth works for at least one seller account and one buyer account.
- [ ] Seller submits an asset and requests evaluation.
- [ ] Approved asset is tokenized against a signet-connected tapd issuance.
- [ ] Seller places a sell order and buyer places a matching buy order.
- [ ] Escrow funding is detected and visible on the marketplace endpoints.
- [ ] Escrow release completes or a dispute path is exercised successfully.
- [ ] Metrics for uptime, latency, error rate, and business events appear in Grafana.
- [ ] Alert routing is validated by firing a beta test alert and confirming only beta receivers are notified.

## Mainnet Promotion Rule

Production exposure is blocked until the beta checklist is complete, the dashboards remain healthy, and no unresolved settlement alerts are active.
