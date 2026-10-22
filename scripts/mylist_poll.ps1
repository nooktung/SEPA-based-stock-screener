# News collection, web-list synchronization and publication every 30 minutes.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
& $python -u -m src.scheduled news
exit $LASTEXITCODE
