# Registers (or updates) the "AVK prod at logon" task for the current user. Idempotent.
$ErrorActionPreference = 'Stop'
$script = Join-Path $PSScriptRoot 'prod-at-logon.ps1'
$user = "$env:USERDOMAIN\$env:USERNAME"
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName 'AVK prod at logon' -TaskPath '\AVK\' -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
"registered \AVK\AVK prod at logon -> $script"
