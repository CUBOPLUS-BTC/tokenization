# Run the local stack with the Polar LND override enabled.
# Examples:
#   ./scripts/compose-polar.ps1 up -d wallet gateway
#   ./scripts/compose-polar.ps1 up -d --force-recreate wallet gateway
#   ./scripts/compose-polar.ps1 logs -f wallet

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]] $ComposeArgs = @("up", "-d")
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$LocalCompose = Join-Path $RepoRoot "infra/docker-compose.local.yml"
$PolarCompose = Join-Path $RepoRoot "infra/docker-compose.polar.yml"
$PolarEnv = Join-Path $RepoRoot "infra/.env.polar"

if (-not (Test-Path $PolarEnv)) {
    Write-Error "Missing infra/.env.polar. Create it from infra/.env.polar.example and fill it with the Polar node connection values."
}

$DockerArgs = @(
    "compose",
    "--project-directory", $RepoRoot,
    "--env-file", $PolarEnv,
    "-f", $LocalCompose,
    "-f", $PolarCompose
) + $ComposeArgs

& docker @DockerArgs
exit $LASTEXITCODE
