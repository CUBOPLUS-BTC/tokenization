#!/usr/bin/env bash
set -euo pipefail

# Rebuild and re-run migrations, then start the stack.
# Prevents stale exited migrate containers from gating app services against an empty DB.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="${1:-infra/docker-compose.local.yml}"

if [[ "$COMPOSE_FILE" != /* ]]; then
  COMPOSE_FILE="$REPO_ROOT/$COMPOSE_FILE"
fi

docker compose --project-directory "$REPO_ROOT" -f "$COMPOSE_FILE" up -d --force-recreate --build migrate
docker compose --project-directory "$REPO_ROOT" -f "$COMPOSE_FILE" up -d
