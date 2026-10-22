# Register the daily start + export + deploy workflow at 15:10, Monday-Friday.
$ErrorActionPreference = 'Stop'
$name = 'SEPA - cap nhat hang ngay'
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command pythonw -ErrorAction Stop).Source }
$action = New-ScheduledTaskAction -Execute $pythonw -Argument '-m src.scheduled daily' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '15:10'
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 3)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Scheduled '$name': 15:10 Mon-Fri, windowless."
