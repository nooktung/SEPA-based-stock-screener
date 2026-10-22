# Daily pipeline and publication; normally launched by pythonw via Task Scheduler.
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = (Get-Command python -ErrorAction Stop).Source }
& $python -u -m src.scheduled daily
exit $LASTEXITCODE
