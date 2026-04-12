# Infrastructure

Docker Compose files, Nginx/Traefik configuration, and environment templates.

## Contents

- `docker/` — Dockerfiles and compose files for local development
- `.env.example` — Template for required environment variables
- `.env.local.example` — Local development profile
- `.env.staging.example` — Staging profile
- `.env.production.example` — Production profile

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
