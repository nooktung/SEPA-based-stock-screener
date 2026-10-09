# Windows setup for vn-stock-agent on a new machine. Run from the project folder:
#   powershell -ExecutionPolicy Bypass -File setup.ps1
# Creates .venv, installs the requirements, creates .env from the template, runs the tests,
# creates the empty database and checks the configuration. Safe to run again.

Write-Host "vn-stock-agent :: setup" -ForegroundColor Cyan
Set-Location $PSScriptRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Python not found on PATH. Install Python 3.11+ (python.org, tick 'Add to PATH') first." -ForegroundColor Red
    exit 1
}

if (-not (Test-Path ".venv")) { python -m venv .venv }
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host ""
    Write-Host "Created .env -- open it and fill in DNSE_API_KEY and DNSE_API_SECRET." -ForegroundColor Yellow
}

& $py -m pytest -q
& $py -m src.main initdb
& $py -m src.main doctor

Write-Host ""
Write-Host "Done. Next (see README.md):" -ForegroundColor Green
Write-Host "  1. Fill DNSE_API_KEY / DNSE_API_SECRET in .env"
Write-Host "  2. .venv\Scripts\python -m src.main universe"
Write-Host "  3. .venv\Scripts\python -m src.main start"
