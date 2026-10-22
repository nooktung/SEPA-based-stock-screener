# Reuse the existing task identity so the previous polling schedule is replaced.
# Collect news and publish every 30 minutes, 07:00-22:30 daily, including weekends.
$ErrorActionPreference = 'Stop'
$name = 'SEPA - danh sach cua toi'
$root = Split-Path -Parent $PSScriptRoot
$pythonw = Join-Path $root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = (Get-Command pythonw -ErrorAction Stop).Source }
$action = New-ScheduledTaskAction -Execute $pythonw -Argument '-m src.scheduled news' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At '07:00'
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At '07:00' -RepetitionInterval (New-TimeSpan -Minutes 30) -RepetitionDuration (New-TimeSpan -Hours 15 -Minutes 30)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Scheduled '$name': news + publish every 30 minutes, 07:00-22:30, windowless."
