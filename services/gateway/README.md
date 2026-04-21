# API Gateway

Reverse proxy and entry point for all client requests.

## Responsibility

- TLS termination
- JWT authentication enforcement
- CORS policy: `CORS_ALLOWED_ORIGINS` is a **comma-separated** list of allowed browser `Origin` values (e.g. `http://localhost:3000,http://127.0.0.1:3000`). At container start, [`30-render-cors-map.sh`](30-render-cors-map.sh) writes `/etc/nginx/conf.d/cors_map.conf` (nginx `map`). Preflight `OPTIONS` is handled with `if ($request_method = OPTIONS) { ... return 204; }` and full `Access-Control-*` headers (with `always`).
- Rate limiting (100 req/min per user)
- Request routing to internal services

## Local browser clients

1. Set `CORS_ALLOWED_ORIGINS` in the env file used by Compose for the gateway (e.g. [`infra/.env.local`](../infra/.env.local)) or override via Compose `environment` / host env for `${CORS_ALLOWED_ORIGINS:-...}` interpolation.
2. Rebuild/recreate the gateway: `docker compose -f infra/docker-compose.local.yml up -d --build --force-recreate gateway`
3. Verify preflight: `curl -i -X OPTIONS http://localhost:8000/v1/auth/login -H "Origin: http://localhost:3000" -H "Access-Control-Request-Method: POST"`

## Technology

Nginx or Traefik (see [architecture.md](../../specs/architecture.md) §5).

## Port

`:443` (production)
