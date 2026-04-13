# Infrastructure

Docker Compose files, Nginx/Traefik configuration, and environment templates.

## Contents

- `docker-compose.local.yml` — Local orchestration for shared platform infra and services
- `.env.example` — Template for required environment variables
- `.env.local.example` — Local development profile
- `.env.staging.example` — Staging profile
- `.env.production.example` — Production profile

## Local orchestration (PostgreSQL + Redis + platform services)

Before first run, create the active local profile from the template:

```bash
cp infra/.env.local.example infra/.env.local
```

`infra/.env.local` is the runtime env file used by Docker Compose.

Run from repository root:

```bash
docker compose -f infra/docker-compose.local.yml up -d
```

This starts:

- `postgres` on `localhost:5432`
- `redis` on `localhost:6379`
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

## Shared Python Configuration

All Python services use the shared settings loader in `services/common/config.py`.

### Environment profile selection

- Set `ENV_PROFILE` to `local`, `staging`, or `production`.
- The loader reads, in order (when present):
    1. `.env`
    2. `infra/.env`
    3. `infra/.env.<profile>`

### Secret handling convention

For each secret value, you can use either:

- Direct variable, e.g. `JWT_SECRET=...`
- File-backed value, e.g. `JWT_SECRET_FILE=/run/secrets/jwt_secret`

If both are present, `*_FILE` is prioritized.

Never commit real secret values. Only commit `*.example` templates.
