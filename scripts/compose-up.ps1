# Rebuild and re-run migrations, then start the stack.
# Prevents stale exited migrate containers from gating app services against an empty DB.
param(
    [string] $ComposeFile = "infra/docker-compose.local.yml"
)

$ErrorActionPreference = "Stop"

docker compose -f $ComposeFile up -d --force-recreate --build migrate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker compose -f $ComposeFile up -d
exit $LASTEXITCODE
