# Infrastructure

Docker Compose files, Nginx/Traefik configuration, and environment templates.

## Contents

- `docker-compose.local.yml` — Local orchestration for shared platform infra and services
- `docker-compose.observability.yml` — Prometheus, Grafana, Alertmanager, blackbox, and cAdvisor
- `docker-compose.public-beta.yml` — Public beta deployment profile wired for signet
- `.env.example` — Template for required environment variables
- `.env.local.example` — Local development profile
- `.env.staging.example` — Staging profile
- `.env.beta.example` — Public beta profile
- `.env.production.example` — Production profile

## Local orchestration (PostgreSQL + Redis + platform services)

Before first run, create the active local profile from the template:

```bash
cp infra/.env.local.example infra/.env.local
```

`infra/.env.local` is the runtime env file used by Docker Compose.

By default, the local profile now launches a real regtest stack for PostgreSQL, Redis, Bitcoin Core, LND, and Elements. `LND_GRPC_REQUIRED=true` and `ELEMENTS_RPC_REQUIRED=true` in `infra/.env.local.example`, so local readiness only turns green when Lightning and Liquid dependencies are actually up.

Run from repository root:

```bash
docker compose -f infra/docker-compose.local.yml up -d
```

This starts:

- `postgres` on `localhost:5432`
- `redis` on `localhost:6379`
- `bitcoind` on `localhost:18443`
- `lnd` on `localhost:10009`
- `elementsd` on `localhost:7041`
- `wallet`, `tokenization`, `marketplace`, `education`, `nostr`
- `gateway` on `localhost:8000`

Stop and clean up:

```bash
docker compose -f infra/docker-compose.local.yml down
```

To remove persisted local database/cache volumes:

```bash
docker compose -f infra/docker-compose.local.yml down -v
```

### Health checks

- Gateway: `GET http://localhost:8000/health`
- Wallet via gateway: `GET http://localhost:8000/v1/wallet/health`
- PostgreSQL readiness: container healthcheck with `pg_isready`
- Redis readiness: container healthcheck with `redis-cli ping`
- Bitcoin Core readiness: container healthcheck with `bitcoin-cli getblockchaininfo`
- LND readiness: container healthcheck with `lncli getinfo`
- Elements readiness: container healthcheck with `elements-cli getwalletinfo`

## Bitcoin Core (regtest)

The local stack includes a pre-configured Bitcoin Core node running in `regtest` mode with ZMQ block and transaction publishers enabled for LND.

- **RPC Endpoint**: `localhost:18443`
- **Default RPC User**: `local_rpc`
- **Default RPC Password**: `local_rpc_password`

The local profile template in `infra/.env.local.example` is aligned with these same regtest credentials.

## Marketplace escrow runtime

The marketplace service now includes an internal escrow watcher that:

- scans `created` escrows for Liquid funding on a schedule,
- prepares the seller-release PSET after funding is detected,
- expires unfunded escrows and restores seller inventory when `expires_at` passes.

Relevant local env knobs:

- `MARKETPLACE_ESCROW_WATCH_INTERVAL_SECONDS`
- `MARKETPLACE_ESCROW_FEE_RESERVE_SAT`

## LND (regtest)

The compose profile includes an `lnd` service wired to the local `bitcoind` over JSON-RPC and ZMQ. For local development it uses `noseedbackup`, so the node self-initializes on first boot and persists its state in the `lnd_data` Docker volume.

- **gRPC Endpoint**: `localhost:10009`
- **TLS / Macaroon Mount for Python services**: `/run/secrets/lnd/...`

The `wallet` service mounts the same LND volume read-only, so `services/wallet/lnd_client.py` connects to a real daemon instead of falling back to its local mock whenever the compose stack is running.

## Elements (elementsregtest)

The local profile also builds an `elementsd` container from the official Elements release binaries and wires it to the same `bitcoind` via mainchain RPC so peg-in and peg-out RPC flows can be exercised against a real sidechain daemon.

- **RPC Endpoint**: `localhost:7041`
- **Default RPC User**: `user`
- **Default RPC Password**: `pass`
- **Default Wallet Name**: `platform`

On first boot, the container creates or loads the configured wallet and mines bootstrap blocks so issuance and Liquid wallet RPC methods have spendable local funds.

For Lightning routing tests, remember this stack only launches a single LND node. Invoice creation and gRPC integration are real, but multi-hop payment tests still require an additional peer/channel topology.

### Mining Blocks

Since it is a regtest environment, you need to manually mine blocks to confirm transactions. A helper script is provided:

```bash
# Mine 1 block (default)
bash scripts/mine-blocks.sh

# Mine 10 blocks
bash scripts/mine-blocks.sh 10
```

### Manual CLI access

You can interact with the node via `bitcoin-cli` through Docker:

```bash
docker exec tokenization-bitcoind bitcoin-cli -regtest -rpcuser=local_rpc -rpcpassword=local_rpc_password <command>
```

## Shared Python Configuration

All Python services use the shared settings loader in `services/common/config.py`.

### Environment profile selection

- Set `ENV_PROFILE` to `local`, `staging`, `beta`, or `production`.
- The loader reads, in order (when present):
    1. `.env`
    2. `infra/.env`
    3. `infra/.env.<profile>`
- Dependency gating can be tuned with `BITCOIN_RPC_REQUIRED`, `LND_GRPC_REQUIRED`, and `ELEMENTS_RPC_REQUIRED`.

## Public beta

The beta environment is intended for external validation on Bitcoin `signet`.

1. Copy `infra/.env.beta.example` to `infra/.env.beta`.
2. Wire the `*_FILE` secrets and signet infrastructure endpoints.
3. Start the stack with `docker compose -f infra/docker-compose.public-beta.yml up -d`.
4. Follow [deploy/public-beta/README.md](../deploy/public-beta/README.md) before exposing the environment.

## Observability

Shared monitoring assets live under [infra/observability](./observability).

```bash
docker compose -f infra/docker-compose.observability.yml up -d
```

### Secret handling convention

For each secret value, you can use either:

- Direct variable, e.g. `JWT_SECRET=...`
- File-backed value, e.g. `JWT_SECRET_FILE=/run/secrets/jwt_secret`

If both are present, `*_FILE` is prioritized.

Never commit real secret values. Only commit `*.example` templates.
