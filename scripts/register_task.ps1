# Create (or replace) the Windows scheduled task that runs scripts\daily.ps1 at 15:30, Monday to Friday.
# Run once on each machine, from the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
# A run missed while the computer was asleep or off starts as soon as it is back (StartWhenAvailable).
# Remove it with:  Unregister-ScheduledTask -TaskName "SEPA - cap nhat hang ngay"

$name = "SEPA - cap nhat hang ngay"
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root "scripts\daily.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday, Tuesday, Wednesday, Thursday, Friday -At "15:30"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 3)

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Scheduled task '$name' runs $script at 15:30 Mon-Fri."
