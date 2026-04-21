# Rebuild and re-run migrations, then start the stack.
# Prevents stale exited migrate containers from gating app services against an empty DB.
param(
    [string] $ComposeFile = "infra/docker-compose.local.yml"
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ComposePath = if ([System.IO.Path]::IsPathRooted($ComposeFile)) {
    (Resolve-Path $ComposeFile).Path
} else {
    (Resolve-Path (Join-Path $RepoRoot $ComposeFile)).Path
}

docker compose --project-directory $RepoRoot -f $ComposePath up -d --force-recreate --build migrate
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

docker compose --project-directory $RepoRoot -f $ComposePath up -d
exit $LASTEXITCODE
