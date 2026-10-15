# The user's list edited on the web ("Danh sách của tôi", Cloudflare KV): read it into this machine,
# bring tickers just added up to date (prices, technicals, foreign flow, news), then publish a new
# copy of the dashboard so the web shows them. Run every 30 minutes by the scheduled task
# "SEPA - danh sach cua toi" (scripts\register_mylist_task.ps1); does nothing when the list did not
# change, or when the web list is not set up in .env.
#   powershell -ExecutionPolicy Bypass -File scripts\mylist_poll.ps1

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    $python = if ($found) { $found.Source } else { "python" }
}
$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("mylist_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
$utf8 = New-Object System.Text.UTF8Encoding($false)
function Note($text) { [System.IO.File]::AppendAllText($log, ("[{0}] {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $text), $utf8) }
function Run($cmd) {
    & "$env:SystemRoot\System32\cmd.exe" /d /c "`"$python`" -m src.main $cmd >> `"$log`" 2>&1"
    return $LASTEXITCODE
}

# While the daily job runs it does all of this itself (its own mylist-sync, monitor, export).
if (Test-Path (Join-Path $root "data\pipeline.lock")) { exit 0 }
$code = Run "mylist-sync --refresh"
if ($code -ne 10) { exit 0 }                 # unchanged (0), not set up (3) or unreadable (1)
Note "danh sách đổi: xuất bản lại"
# Only what the change alters (~2 min); the full export when the copy is of an older screen run.
if ((Run "export-site --update") -eq 0) {
    $deploy = Join-Path $root "scripts\deploy_site.ps1"
    & "$env:SystemRoot\System32\cmd.exe" /d /c "powershell -NoProfile -ExecutionPolicy Bypass -File `"$deploy`" >> `"$log`" 2>&1"
    Note "deploy_site xong, exit=$LASTEXITCODE"
} else {
    Note "export-site không xuất bản (dữ liệu chưa phải phiên mới nhất): bản web cập nhật sau tác vụ 15:30"
}
