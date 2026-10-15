# Upload data\site (python -m src.main export-site) to Cloudflare Pages: the read-only copy of the
# dashboard, opened only with an account of its login (site-user; run.md, "Bản web cho người khác").
# Reads CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID from .env (never committed) and the project
# name from config/thresholds.yaml site.pages_project. Uses Cloudflare's own CLI (wrangler, via npx).
# Exit 3 without the two keys, 2 without an exported copy: nothing is uploaded then.
#   powershell -ExecutionPolicy Bypass -File scripts\deploy_site.ps1 [-Branch main|preview]
param([string]$Branch = "main")

$ErrorActionPreference = "Continue"
# UTF-8 output: in the daily job's log the Vietnamese lines came out garbled (console code page).
try { [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false) } catch { }
$OutputEncoding = New-Object System.Text.UTF8Encoding($false)
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    $python = if ($found) { $found.Source } else { "python" }
}

# Only the two Cloudflare values are read from .env, into this process alone.
$envFile = Join-Path $root ".env"
if (Test-Path $envFile) {
    foreach ($line in [System.IO.File]::ReadAllLines($envFile)) {
        if ($line -match '^\s*(CLOUDFLARE_API_TOKEN|CLOUDFLARE_ACCOUNT_ID)\s*=\s*(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim().Trim('"').Trim("'"), "Process")
        }
    }
}
if (-not $env:CLOUDFLARE_API_TOKEN -or -not $env:CLOUDFLARE_ACCOUNT_ID) {
    Write-Output "Chưa có CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID trong .env: không đẩy bản web."
    exit 3
}
$site = Join-Path $root "data\site"
if (-not (Test-Path (Join-Path $site "site.json"))) {
    Write-Output "Chưa có data\site (chạy: python -m src.main export-site): không đẩy."
    exit 2
}
$project = & $python -c "from src.config import load_config; print(load_config().thresholds.raw['site']['pages_project'])"
Write-Output "Đẩy $site lên Cloudflare Pages, dự án $project, nhánh $Branch"
# From cloudflare\ so wrangler also uploads cloudflare\functions (the list's /api/mylist on the web).
Set-Location (Join-Path $root "cloudflare")
& npx --yes wrangler pages deploy $site --project-name $project --branch $Branch --commit-dirty=true
exit $LASTEXITCODE
