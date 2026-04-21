# API Gateway

Reverse proxy and entry point for all client requests.

## Responsibility

- TLS termination
- JWT authentication enforcement
- CORS policy (gateway is the **single source of truth**; upstream `Access-Control-*` headers are stripped via `proxy_hide_header`)
- Rate limiting (100 req/min per user)
- Request routing to internal services

## CORS policy (definitive)

CORS is applied **uniformly** on every route exposed by the gateway, including error handlers:

- `/` (root discovery)
- `/v1/**` (all upstream service routes, known or unknown — unknown paths return a 404 JSON payload **with** CORS headers instead of the opaque nginx default)
- `/health` and `/health/<service>`
- `/ready/<service>` and `/metrics/<service>`
- `@upstream_error` fallback (so `502`/`503`/`504` responses also carry CORS headers and the browser can surface the real error instead of a CORS failure)

All `Access-Control-*` headers emitted by upstream services are stripped via `proxy_hide_header` so the browser never sees duplicates.

### Configuration (env-driven)

Everything about CORS is configurable with environment variables. The gateway image carries sensible defaults; override only what you need per environment.

| Variable | Default | Purpose |
| --- | --- | --- |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:3000` | Comma-separated list of browser `Origin` values allowed. Each entry is mapped exactly (no wildcards). |
| `CORS_ALLOWED_METHODS` | `GET, POST, PUT, PATCH, DELETE, OPTIONS` | Value for `Access-Control-Allow-Methods` (preflight). |
| `CORS_ALLOWED_HEADERS` | `Authorization, Content-Type, X-Requested-With, Accept, Origin, X-Request-ID` | Value for `Access-Control-Allow-Headers` (preflight). |
| `CORS_EXPOSE_HEADERS` | `X-Request-ID` | Value for `Access-Control-Expose-Headers` (responses). |
| `CORS_MAX_AGE` | `86400` | Value for `Access-Control-Max-Age` in seconds (preflight cache). |
| `CORS_ALLOW_CREDENTIALS` | `true` | Value for `Access-Control-Allow-Credentials`. |

At container start, two entrypoint scripts render the nginx bits from env:

- [`30-render-cors-map.sh`](30-render-cors-map.sh) writes `/etc/nginx/conf.d/cors_map.conf` with the `map $http_origin $cors_origin` from `CORS_ALLOWED_ORIGINS`.
- [`31-render-cors-snippets.sh`](31-render-cors-snippets.sh) writes `/etc/nginx/snippets/cors.conf` (the unified preflight + headers snippet) from the remaining `CORS_*` vars. Two legacy snippets (`cors-preflight.conf` and `cors-headers.conf`) are also rendered for backwards-compat; new code should only include `cors.conf`.

[`gateway.conf`](gateway.conf) then includes `cors.conf` inside **every** `location` block (upstream routes, health, readiness, metrics, the `/v1/` catch-all, and the `@upstream_error` fallback), so the browser always sees `Access-Control-Allow-Origin`.

## Adding a new service

A new upstream only needs two `location` blocks. Both must include `cors.conf`:

```nginx
location = /v1/foo {
    include /etc/nginx/snippets/cors.conf;
    rewrite ^/v1/foo$ / break;
    proxy_pass http://foo:8010;
}
location /v1/foo/ {
    include /etc/nginx/snippets/cors.conf;
    rewrite ^/v1/foo/(.*) /$1 break;
    proxy_pass http://foo:8010;
}
```

Optionally add matching entries in the `/health/<svc>`, `/ready/<svc>` and `/metrics/<svc>` blocks.

## Local browser clients

1. Set `CORS_ALLOWED_ORIGINS` (and any other `CORS_*` override) in the env file used by Compose for the gateway (e.g. [`infra/.env.local`](../../infra/.env.local)) or via host env for `${CORS_ALLOWED_ORIGINS:-...}` interpolation.
2. **Rebuild the gateway** — this is mandatory any time `gateway.conf`, the render scripts, or any `CORS_*` env var changes, because those are baked at image build time (and the entrypoint scripts only run on container (re)create):

   ```bash
   docker compose -f infra/docker-compose.local.yml up -d --build --force-recreate gateway
   ```

   Regtest equivalent:

   ```bash
   docker compose -f infra/docker-compose.regtest.yml up -d --build --force-recreate gateway
   ```

3. Verify preflight and regular responses:

   ```bash
   # Preflight (expected: 204 with Access-Control-* headers)
   curl -i -X OPTIONS http://localhost:8000/v1/wallet/onchain/address \
     -H "Origin: http://localhost:3000" \
     -H "Access-Control-Request-Method: POST"

   # Regular request (expected: 200/401/4xx with Access-Control-Allow-Origin)
   curl -i http://localhost:8000/v1/wallet/onchain/address \
     -H "Origin: http://localhost:3000"

   # Health endpoints (expected: 200 with CORS headers)
   curl -i http://localhost:8000/health         -H "Origin: http://localhost:3000"
   curl -i http://localhost:8000/health/wallet  -H "Origin: http://localhost:3000"

   # Upstream down (expected: 502 with CORS headers)
   docker compose -f infra/docker-compose.local.yml stop wallet
   curl -i http://localhost:8000/v1/wallet/onchain/address \
     -H "Origin: http://localhost:3000"
   ```

   Every response above must include `Access-Control-Allow-Origin: http://localhost:3000` and `Vary: Origin`.

## Technology

Nginx (see [architecture.md](../../specs/architecture.md) §5).

## Port

`:8000` (local/regtest Compose) / `:443` (production)
