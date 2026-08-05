[CmdletBinding()]
param(
    [string]$Commit = "",
    [switch]$SkipTests,
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not $SkipTests) {
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        & $venvPython -m pytest --confcutdir=tests/rennebot tests/rennebot
    } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        uv run pytest --confcutdir=tests/rennebot tests/rennebot
    } else {
        throw "No .venv or uv was found. Create the development environment first."
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Local tests failed; deployment was cancelled."
    }
}

if ([string]::IsNullOrWhiteSpace($Commit)) {
    $Commit = (git rev-parse HEAD).Trim()
    $deployHead = $true
} else {
    $deployHead = $Commit -eq (git rev-parse HEAD).Trim()
}
if ($Commit -notmatch '^[0-9a-f]{40}$') {
    throw "Commit must be a complete 40-character Git SHA."
}

if (-not $NoPush) {
    if (-not $deployHead) {
        throw "Use -NoPush to deploy a historical commit already in remote master history."
    }
    git push origin HEAD:refs/heads/master
}

$deployHost = $env:RENNEBOT_DEPLOY_HOST
if ([string]::IsNullOrWhiteSpace($deployHost)) {
    throw "Set RENNEBOT_DEPLOY_HOST, for example user@your-server."
}
$remoteAppDir = if ($env:RENNEBOT_REMOTE_APP_DIR) {
    $env:RENNEBOT_REMOTE_APP_DIR
} else {
    "/opt/rennebot/app"
}
$remoteRuntimeDir = if ($env:RENNEBOT_REMOTE_RUNTIME_DIR) {
    $env:RENNEBOT_REMOTE_RUNTIME_DIR
} else {
    "/opt/rennebot/runtime"
}

$remoteScript = @'
set -euo pipefail
app_dir="$1"
runtime_dir="$2"
commit="$3"

test -d "$app_dir/.git"
test -f "$runtime_dir/bot.env"
git -C "$app_dir" fetch origin master
git -C "$app_dir" cat-file -e "${commit}^{commit}"

database="$runtime_dir/astrbot-data/plugin_data/qq_game_registry/rennebot.sqlite3"
if [ -f "$database" ]; then
  mkdir -p "$runtime_dir/backups"
  cp -a "$database" "$runtime_dir/backups/rennebot-${commit:0:12}-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
fi

git -C "$app_dir" checkout --detach "$commit"
cd "$app_dir"
BOT_ENV_FILE="$runtime_dir/bot.env" RUNTIME_DIR="$runtime_dir" docker compose -f compose.rennebot.yml up -d --build --remove-orphans
BOT_ENV_FILE="$runtime_dir/bot.env" RUNTIME_DIR="$runtime_dir" docker compose -f compose.rennebot.yml ps
'@

Write-Host "Deploying $Commit to $deployHost ..."
$remoteScript | & ssh $deployHost "bash -s -- '$remoteAppDir' '$remoteRuntimeDir' '$Commit'"
if ($LASTEXITCODE -ne 0) {
    throw "Remote deployment failed. The server retains the database backup created before deployment."
}
Write-Host "Deployment completed: $Commit"
