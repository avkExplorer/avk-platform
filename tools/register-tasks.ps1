# Registers (or updates) the platform's scheduled tasks for the current user. Idempotent. Run in your own terminal:
#   powershell -ExecutionPolicy Bypass -File tools\register-tasks.ps1
#
#   \AVK\AVK deploy agent     every 5 minutes while you are logged on (polls GitHub, deploys approved releases)
#   \AVK\AVK backup nightly   every day 02:30 (runs when the PC is next on if it was off)
#   \AVK\AVK backup verify    every Sunday 03:30 (restores into a throwaway Postgres and compares row counts)
# The tasks run only while you are logged on, because Docker Desktop runs in your session. Output goes to
# %AVK_HOME%\logs\task-*.log; failures also write %AVK_HOME%\logs\alerts.log.
$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'run-avk-task.ps1'
$user = "$env:USERDOMAIN\$env:USERNAME"
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)

function Register([string]$name, [string]$taskArgs, $trigger) {
    $argument = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$runner`" -Name $name -Args `"$taskArgs`""
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument
    Register-ScheduledTask -TaskName "AVK $($script:labels[$name])" -TaskPath '\AVK\' -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Force | Out-Null
    "registered \AVK\AVK $($script:labels[$name])"
}
$labels = @{ 'deploy-agent' = 'deploy agent'; 'backup-run' = 'backup nightly'; 'backup-verify' = 'backup verify' }

$logon = New-ScheduledTaskTrigger -AtLogOn -User $user
$logon.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 5)).Repetition
Register 'deploy-agent' 'deploy-agent' $logon
Register 'backup-run' 'backup run' (New-ScheduledTaskTrigger -Daily -At '02:30')
Register 'backup-verify' 'backup verify' (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At '03:30')
