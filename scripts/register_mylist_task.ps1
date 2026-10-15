# Create (or replace) the scheduled task that reads the user's list from the web every 30 minutes,
# 07:00-22:30 every day (scripts\mylist_poll.ps1). Only needed once the web copy is set up (run.md 7).
# Run once, from the project folder:
#   powershell -ExecutionPolicy Bypass -File scripts\register_mylist_task.ps1
# Remove it with:  Unregister-ScheduledTask -TaskName "SEPA - danh sach cua toi"

$name = "SEPA - danh sach cua toi"
$root = Split-Path -Parent $PSScriptRoot
$script = Join-Path $root "scripts\mylist_poll.ps1"

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
    -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At "07:00"
$trigger.Repetition = (New-ScheduledTaskTrigger -Once -At "07:00" -RepetitionInterval (New-TimeSpan -Minutes 30) `
    -RepetitionDuration (New-TimeSpan -Hours 15 -Minutes 30)).Repetition
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 25)

Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Write-Host "Scheduled task '$name' runs $script every 30 minutes, 07:00-22:30."
