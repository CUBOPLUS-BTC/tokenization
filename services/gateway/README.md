# API Gateway

Reverse proxy and entry point for all client requests.

## Responsibility

- TLS termination
- JWT authentication enforcement
- CORS policy (gateway is the **single source of truth**; upstream `Access-Control-*` headers are stripped via `proxy_hide_header`)
- Rate limiting (100 req/min per user)
- Request routing to internal services

## CORS configuration (env-driven)

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
- [`31-render-cors-snippets.sh`](31-render-cors-snippets.sh) writes `/etc/nginx/snippets/cors-preflight.conf` and `/etc/nginx/snippets/cors-headers.conf` from the remaining `CORS_*` vars.

[`gateway.conf`](gateway.conf) then `include`s those two snippets inside each `location` block instead of duplicating ~18 lines per route.

## Adding a new service

A new upstream only needs two `location` blocks and no CORS changes:

```nginx
location = /v1/foo {
    include /etc/nginx/snippets/cors-preflight.conf;
    include /etc/nginx/snippets/cors-headers.conf;
    rewrite ^/v1/foo$ / break;
    proxy_pass http://foo:8010;
}
location /v1/foo/ {
    include /etc/nginx/snippets/cors-preflight.conf;
    include /etc/nginx/snippets/cors-headers.conf;
    rewrite ^/v1/foo/(.*) /$1 break;
    proxy_pass http://foo:8010;
}
```

## Local browser clients

1. Set `CORS_ALLOWED_ORIGINS` (and any other `CORS_*` override) in the env file used by Compose for the gateway (e.g. [`infra/.env.local`](../../infra/.env.local)) or via host env for `${CORS_ALLOWED_ORIGINS:-...}` interpolation.
2. Rebuild/recreate the gateway: `docker compose --project-directory . -f infra/docker-compose.local.yml up -d --build --force-recreate gateway`
3. Verify preflight: `curl -i -X OPTIONS http://localhost:8000/v1/auth/login -H "Origin: http://localhost:3000" -H "Access-Control-Request-Method: POST"`

## Technology

Nginx or Traefik (see [architecture.md](../../specs/architecture.md) §5).

## Port

`:443` (production)
