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

# Time limits: a hung Docker engine must not leave this task running forever (it blocked every later tick once).
function Invoke-Timed([string]$File, [string[]]$Arguments, [int]$Seconds) {
    $outFile = [IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $File -ArgumentList $Arguments -NoNewWindow -PassThru -RedirectStandardOutput $outFile -RedirectStandardError "$outFile.err"
    $null = $p.Handle   # caching the handle is what makes ExitCode readable after the process ends
    $done = $p.WaitForExit($Seconds * 1000)
    if (-not $done) { & taskkill /PID $p.Id /T /F *> $null } else { $p.WaitForExit() }   # the plain wait makes ExitCode available
    $text = @(Get-Content $outFile, "$outFile.err" -ErrorAction SilentlyContinue)
    Remove-Item $outFile, "$outFile.err" -Force -ErrorAction SilentlyContinue
    @{ Code = $(if ($done) { $p.ExitCode } else { -1 }); Out = $text; TimedOut = -not $done }
}

$info = Invoke-Timed 'docker' @('info', '--format', '{{.ServerVersion}}') 30
if ($info.Code -ne 0 -and $Name -eq 'deploy-agent') {
    # The 5-minute task is also the watchdog: if Docker Desktop stopped, start it again (cooldown and hang rules are in dockerwatch.py).
    $watch = Invoke-Timed (Join-Path $avkHome 'venv\Scripts\python.exe') @('-m', 'avk', 'docker-watch') 330
    Write-Log "docker-watch exit=$($watch.Code) $(($watch.Out | Select-Object -Last 2) -join ' | ')"
    $info = Invoke-Timed 'docker' @('info', '--format', '{{.ServerVersion}}') 30
}
if ($info.Code -ne 0) { Write-Log "docker not ready$(if ($info.TimedOut) { ' (engine hung)' }); skipped"; exit 0 }

$python = Join-Path $avkHome 'venv\Scripts\python.exe'
$run = Invoke-Timed $python (@('-m', 'avk') + ($Args -split ' ')) 900
if ($run.TimedOut) { Write-Log "timed out after 900 s; killed"; exit 1 }
Write-Log "exit=$($run.Code) $(($run.Out | Select-Object -Last 6) -join ' | ')"
exit $run.Code
