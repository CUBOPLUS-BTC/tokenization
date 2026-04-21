# Infrastructure

Docker Compose files, Nginx/Traefik configuration, and environment templates.

## Contents

- `docker-compose.local.yml` — Local orchestration for shared platform infra and services (publishes infra ports to the host)
- `docker-compose.regtest.yml` — Isolated regtest stack (same chain stack; only `gateway` published on `8000` by default)
- `docker-compose.testnet4.yml` — Testnet4 + Liquid testnet profile
- `docker-compose.public-beta.yml` — Public beta deployment profile (signet-backed services in `.env.beta`)
- `docker-compose.observability.yml` — Prometheus, Grafana, Alertmanager, blackbox, and cAdvisor
- `.env.example` — Template for required environment variables
- `.env.local.example` — Local development profile
- `.env.staging.example` — Staging profile
- `.env.beta.example` — Public beta profile
- `.env.production.example` — Production profile

## Database migrations (Alembic)

The canonical schema lives in `services/common/db/metadata.py`. Migrations live under `alembic/versions/`; the baseline is a **squashed** `0001_initial_schema` that runs `metadata.create_all()`.

If you already had a database created with the **old** multi-revision chain, you must reset before applying the new history:

```bash
# Against a dev database (destructive)
docker compose --project-directory . -f infra/docker-compose.local.yml exec postgres \
  psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
alembic upgrade head
# Or remove the Postgres volume and recreate: docker compose ... down -v
```

## Invoking Compose (always from repository root)

Use a single pattern so volume paths and `build.context` resolve correctly:

```bash
docker compose --project-directory . -f infra/docker-compose.<profile>.yml <command>
```

Do **not** run `docker compose -f infra/docker-compose.local.yml` from inside `infra/` unless you adjust every path — the files assume the project directory is the repo root.

## Local orchestration (PostgreSQL + Redis + platform services)

Before first run, create the active local profile from the template:

```bash
cp infra/.env.local.example infra/.env.local
```

`infra/.env.local` is the runtime env file used by Docker Compose.

By default, the local profile launches a real regtest stack for PostgreSQL, Redis, Bitcoin Core, LND, and Elements. `LND_GRPC_REQUIRED=true` and `ELEMENTS_RPC_REQUIRED=true` in `infra/.env.local.example`, so local readiness only turns green when Lightning and Liquid dependencies are actually up.

### Port matrix: published host ports

| Profile | postgres | redis | bitcoind RPC | lnd gRPC/p2p | elementsd RPC | gateway |
|---------|----------|-------|--------------|--------------|---------------|---------|
| **local** (`docker-compose.local.yml`) | 5432 | 6379 | 18444→18443 | 10009, 9735 | 7041 | 8000 |
| **regtest** (`docker-compose.regtest.yml`) | (internal) | (internal) | (internal) | (internal) | (internal) | 8000 |
| **testnet4** | (internal) | (internal) | (internal) | (internal) | (internal) | 8000 |
| **public-beta** | (internal) | (internal) | — | — | — | 8000 |

Use **local** when you need direct host access to PostgreSQL, Redis, Bitcoin, LND, or Elements CLI. Use **regtest** when you only need the API through the gateway (cleaner isolation and separate Docker volumes: `regtest_*`).

## Dedicated regtest profile

Use this when you need a regtest stack that is isolated from `infra/.env.local` (separate Compose project `tokenization-regtest` and volumes prefixed `regtest_`):

```bash
cp infra/.env.regtest.example infra/.env.regtest
docker compose --project-directory . -f infra/docker-compose.regtest.yml up -d postgres redis bitcoind lnd elementsd
python scripts/run_db_bootstrap.py --profile regtest
docker compose --project-directory . -f infra/docker-compose.regtest.yml up -d
```

`infra/docker-compose.regtest.yml` injects `infra/.env.regtest` into PostgreSQL, migrations, and the Python services.

On a brand-new `regtest_lnd_data` volume, LND may stay unhealthy until a wallet is created. With `noseedbackup=1` in `infra/lnd/lnd.conf`, a wallet is usually created automatically; if the healthcheck still fails, initialize manually before relying on `wallet`:

```bash
docker compose --project-directory . -f infra/docker-compose.regtest.yml exec lnd \
  lncli --network=regtest --lnddir=/data/lnd create
```

After the wallet exists, `admin.macaroon` will be present and the `wallet` service can pass its dependency gate.

### Local profile (same regtest chain, host ports exposed)

```bash
docker compose --project-directory . -f infra/docker-compose.local.yml up -d postgres redis bitcoind lnd elementsd
python scripts/run_db_bootstrap.py --profile local
docker compose --project-directory . -f infra/docker-compose.local.yml up -d
```

Container names are prefixed with `tokenization-local-*` (e.g. `tokenization-local-bitcoind`). `db-bootstrap` is not part of Compose startup; run migrations with `python scripts/run_db_bootstrap.py --profile <profile>`.

Stop and clean up:

```bash
docker compose --project-directory . -f infra/docker-compose.local.yml down
```

To remove persisted local database/cache volumes:

```bash
docker compose --project-directory . -f infra/docker-compose.local.yml down -v
```

### Health checks

- Gateway: `GET http://localhost:8000/health`
- Per-service via gateway: `GET http://localhost:8000/health/wallet`, `/ready/wallet`, etc.
- PostgreSQL readiness: container healthcheck with `pg_isready`
- Redis readiness: container healthcheck with `redis-cli ping`
- Bitcoin Core readiness: container healthcheck with `bitcoin-cli getblockchaininfo`
- LND readiness: container healthcheck with `lncli getinfo`
- Elements readiness: container healthcheck with `elements-cli getblockchaininfo`

## Coolify and reverse-proxy notes

- **testnet4** and **public-beta** compose files do not declare a custom bridge network so platform proxies (e.g. Coolify) can attach to the default project network.
- **local** and **regtest** use an explicit `platform` bridge network (`tokenization-local_platform` / `tokenization-regtest_platform`) for reproducible service DNS.
- In all profiles, only **`gateway`** needs to be published on port **8000** for browser/API access; other services use Docker DNS (`auth`, `wallet`, …).

## Bitcoin Core (regtest)

The local stack includes a pre-configured Bitcoin Core node running in `regtest` mode with ZMQ block and transaction publishers enabled for LND.

- **RPC Endpoint**: `localhost:18444` (published host port in **local** profile) / `bitcoind:18443` (inside the Compose network)
- **Default RPC User**: `local_rpc`
- **Default RPC Password**: `local_rpc_password`

The local profile template in `infra/.env.local.example` is aligned with these regtest credentials.

## Marketplace escrow runtime

The marketplace service includes an internal escrow watcher that:

- scans `created` escrows for Liquid funding on a schedule,
- prepares the seller-release PSET after funding is detected,
- expires unfunded escrows and restores seller inventory when `expires_at` passes.

Relevant local env knobs:

- `MARKETPLACE_ESCROW_WATCH_INTERVAL_SECONDS`
- `MARKETPLACE_ESCROW_FEE_RESERVE_SAT`

## LND (regtest)

The compose profile includes an `lnd` service wired to the local `bitcoind` over JSON-RPC and ZMQ. For local/regtest it uses `noseedbackup=1`, so the node self-initializes on first boot and persists its state in the `lnd_data` / `regtest_lnd_data` Docker volume.

- **gRPC Endpoint**: `localhost:10009` (local profile) / internal `lnd:10009`
- **TLS / Macaroon mount for Python services**: `/run/secrets/lnd/...`

The `wallet` service mounts the same LND volume read-only, so `services/wallet/lnd_client.py` connects to a real daemon instead of falling back to its local mock when the compose stack is running.

### Testnet4 LND

`infra/lnd/lnd.testnet4.conf` uses `noseedbackup=1` for a deterministic dev wallet. For production-like testnet4 nodes, prefer removing `noseedbackup` and running `lncli create` once, then ensure `admin.macaroon` exists before starting `wallet`.

## Elements (elementsregtest)

The local profile builds an `elementsd` container from the official Elements release binaries and wires it to the same `bitcoind` via mainchain RPC.

- **RPC Endpoint**: `localhost:7041` (local profile) / `elementsd:7041` internally
- **Default RPC User**: `user`
- **Default RPC Password**: `pass`
- **Default Wallet Name**: `platform`

On first boot, the entrypoint creates or loads the configured wallet and mines bootstrap blocks on elementsregtest so issuance and Liquid wallet RPC methods have spendable local funds.

For Lightning routing tests, this stack only launches a single LND node. Invoice creation and gRPC integration are real, but multi-hop payment tests require additional peer/channel topology.

### Mining Blocks

Since it is a regtest environment, mine blocks to confirm transactions:

```bash
# Mine 1 block (default); container name matches local compose
bash scripts/mine-blocks.sh

# Mine 10 blocks
bash scripts/mine-blocks.sh 10

# Override if using regtest compose bitcoind:
# BTC_CONTAINER=tokenization-regtest-bitcoind bash scripts/mine-blocks.sh
```

### Manual CLI access

**Local** profile (`tokenization-local-bitcoind`):

```bash
docker exec tokenization-local-bitcoind bitcoin-cli -regtest -datadir=/data/.bitcoin getblockchaininfo
```

**Regtest** profile:

```bash
docker exec tokenization-regtest-bitcoind bitcoin-cli -regtest -datadir=/data/.bitcoin getblockchaininfo
```

## Shared Python Configuration

All Python services use the shared settings loader in `services/common/config.py`.

### Environment profile selection

- Set `ENV_PROFILE` to `local`, `regtest`, `staging`, `beta`, or `production`.
- The loader reads, in order (when present):

    1. `.env`
    2. `infra/.env`
    3. `infra/.env.<profile>`

- Dependency gating can be tuned with `BITCOIN_RPC_REQUIRED`, `LND_GRPC_REQUIRED`, and `ELEMENTS_RPC_REQUIRED`.
- `AUTH_SERVICE_URL` defaults to `http://auth:8000` if unset; it is set explicitly in `infra/.env.local` and `infra/.env.regtest` templates.

## Public beta

The beta environment is intended for external validation on Bitcoin `signet`.

1. Copy `infra/.env.beta.example` to `infra/.env.beta`.
2. Wire the `*_FILE` secrets and signet infrastructure endpoints.
3. Start the shared dependencies with `docker compose --project-directory . -f infra/docker-compose.public-beta.yml up -d postgres redis`.
4. Run `python scripts/run_db_bootstrap.py --profile public-beta`.
5. Start the application stack with `docker compose --project-directory . -f infra/docker-compose.public-beta.yml up -d`.
6. Follow [deploy/public-beta/README.md](../deploy/public-beta/README.md) before exposing the environment.

The `gateway` service is exposed on `8000:8000`.

## Observability

Shared monitoring assets live under [infra/observability](./observability).

```bash
docker compose --project-directory . -f infra/docker-compose.observability.yml up -d
```

### Secret handling convention

For each secret value, you can use either:

- Direct variable, e.g. `JWT_SECRET=...`
- File-backed value, e.g. `JWT_SECRET_FILE=/run/secrets/jwt_secret`

If both are present, `*_FILE` is prioritized.

Never commit real secret values. Only commit `*.example` templates.
