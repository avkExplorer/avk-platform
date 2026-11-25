# Runs one `avk` command for Task Scheduler and logs its output. Used by the deploy agent and the backup tasks.
#   run-avk-task.ps1 -Name deploy-agent -Args "deploy-agent"
#   run-avk-task.ps1 -Name backup-run   -Args "backup run"
# Skips quietly (exit 0) when Docker is not running yet: the logon task starts Docker, and the next tick tries again.
param(
    [Parameter(Mandatory)][string]$Name,
    [Parameter(Mandatory)][string]$Args
)
$ErrorActionPreference = 'Continue'
$avkHome = if ($env:AVK_HOME) { $env:AVK_HOME } else { 'C:\avk' }
$logDir = Join-Path $avkHome 'logs'
New-Item -ItemType Directory -Force $logDir | Out-Null
$log = Join-Path $logDir "task-$Name.log"
function Write-Log($msg) { "$(Get-Date -Format o) $msg" | Out-File -FilePath $log -Append -Encoding utf8 }

# Keep each log under about 1 MB.
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 1MB)) { Move-Item $log "$log.old" -Force }

docker info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) { Write-Log "docker not ready; skipped"; exit 0 }

$python = Join-Path $avkHome 'venv\Scripts\python.exe'
$out = & $python -m avk ($Args -split ' ') 2>&1
$code = $LASTEXITCODE
Write-Log "exit=$code $(($out | Select-Object -Last 6) -join ' | ')"
exit $code
