# Daily after-close job: reference prices, VN-Index session figures, monitor (prices + events), foreign flow, news, logos of any new ticker,
# then the whole market: backfill every ticker and rebuild the "Toàn thị trường" snapshot.
# Run by the Windows scheduled task "SEPA - cap nhat hang ngay" at 15:30 Mon-Fri;
# can also be run by hand:  powershell -ExecutionPolicy Bypass -File scripts\daily.ps1
# Output goes to data\logs\daily_<date>.log (UTF-8). Research tool only: no order path.

$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONIOENCODING = "utf-8"

# The project's own environment (setup.ps1 creates .venv), else the Python on PATH.
$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    $found = Get-Command python -ErrorAction SilentlyContinue
    $python = if ($found) { $found.Source } else { "python" }
}

$logDir = Join-Path $root "data\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir ("daily_{0}.log" -f (Get-Date -Format "yyyy-MM-dd"))
$utf8 = New-Object System.Text.UTF8Encoding($false)

function Note($text) {
    [System.IO.File]::AppendAllText($log, ("[{0}] {1}`r`n" -f (Get-Date -Format "HH:mm:ss"), $text), $utf8)
}

function Step($name) {
    Note "=== $name ==="
    # cmd.exe appends Python's UTF-8 output byte for byte; PowerShell 5.1's own
    # redirection would re-encode it as UTF-16 and garble the log.
    & "$env:SystemRoot\System32\cmd.exe" /d /c "`"$python`" -m src.main $name >> `"$log`" 2>&1"
    $code = $LASTEXITCODE
    Note "$name xong, exit=$code"
    return $code
}

# The user's list edited on the web (Cloudflare KV) first, so today's monitor, flow and news cover it.
# Without the web list set up in .env it says so and exits 3; nothing changes.
Step "mylist-sync" | Out-Null
# Reference / ceiling / floor of today's session (DNSE publishes the next session's at ~20:45,
# so 15:30 still reads today's): the dashboard's % follows the price boards (~1-2 min).
Step "refprices" | Out-Null
# matched volume / breadth of VN-Index and the trading calendar, before monitor counts distribution days
Step "index-session" | Out-Null
# VN-Index bars vs HOSE's own values: a report only (data\audit\vnindex_check_<date>.json)
Step "index-check" | Out-Null
if ((Step "monitor") -eq 0) { Step "flow" | Out-Null; Step "news" | Out-Null; Step "logos --all" | Out-Null }
else { Note "monitor thất bại, bỏ qua news" }
# Whole market last (~40-45 min): prices for every ticker, then the dashboard's snapshot.
# backfill ends PARTIAL most days (flagged series), which is normal: the snapshot runs anyway.
Step "backfill" | Out-Null
Step "market" | Out-Null
# Invariants over what the dashboard shows (report only; exit 1 on a violation, the job goes on).
Step "check" | Out-Null
# UPCoM's reference is the previous session's average price: fill it for every UPCoM ticker's
# latest session, so the whole-market view shows the % the price boards show (~230 requests).
Step "refprices --market" | Out-Null
# SSI FastConnect Data, cross-check only (seconds). Without SSI keys in .env both steps
# print "chưa có khóa" and exit 3; nothing in the DNSE tables is changed either way.
if ((Step "ssi --days 3") -eq 0) { Step "crosscheck --days 30" | Out-Null }
# Last: the same pipeline as `start`, without opening the web (seconds to minutes). It re-screens
# the session when it has not been screened (industry, fundamentals, screen, monitor --no-fetch) and
# rebuilds the whole-market table on the new RS. Before 2026-10-11 the 15:30 job never screened,
# so sessions without a manual `start` had no screen run (tester report B21).
Step "start --update-only" | Out-Null
# A `start` the user opened while this job ran holds the pipeline lock, and its screen run is what
# the export needs (14/10/2026: this job exported at 20:06, was refused, the web stayed on 13/10).
# Wait for it (up to 90 minutes, within the task's 3 hours) instead of exporting at once.
$lock = Join-Path $root "data\pipeline.lock"
$waited = 0
while ((Test-Path $lock) -and $waited -lt 90) { Start-Sleep -Seconds 60; $waited++ }
if ($waited -gt 0) { Note "đã chờ lượt cập nhật khác chạy xong: $waited phút" }
# The read-only copy for the viewers the user chose (run.md, "Bản web cho người khác"): export-site
# refuses (exit 2) unless the data is the latest closed session and its screen run exists, and the
# published copy then stays as it is; the upload runs only after a good export. Without the
# Cloudflare keys in .env the upload step says so and exits 3.
if ((Step "export-site") -eq 0) {
    Note "=== deploy_site ==="
    $deploy = Join-Path $root "scripts\deploy_site.ps1"
    & "$env:SystemRoot\System32\cmd.exe" /d /c "powershell -NoProfile -ExecutionPolicy Bypass -File `"$deploy`" >> `"$log`" 2>&1"
    Note "deploy_site xong, exit=$LASTEXITCODE"
}
