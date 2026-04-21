#!/usr/bin/env bash
set -euo pipefail

# Rebuild and re-run migrations, then start the stack.
# Prevents stale exited migrate containers from gating app services against an empty DB.
COMPOSE_FILE="${1:-infra/docker-compose.local.yml}"

docker compose -f "$COMPOSE_FILE" up -d --force-recreate --build migrate
docker compose -f "$COMPOSE_FILE" up -d
