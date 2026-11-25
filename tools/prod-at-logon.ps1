# Runs at the owner's Windows logon (Task Scheduler): wait for Docker, then bring prod up.
# Every Docker call has a time limit: after one cold boot the Docker engine API hung for 10+ minutes, and a plain call
# would hang with it. If the engine hangs, Docker Desktop is restarted once and the step is retried.
# Log: %AVK_HOME%\logs\prod-at-logon.log
$ErrorActionPreference = 'Continue'
$avkHome = if ($env:AVK_HOME) { $env:AVK_HOME } else { 'C:\avk' }
$log = Join-Path $avkHome 'logs\prod-at-logon.log'
function Write-Log($msg) { "$(Get-Date -Format o) $msg" | Out-File -FilePath $log -Append -Encoding utf8 }

# Runs a command with a time limit; returns @{Code; Out; TimedOut}. A timed-out process tree is killed.
function Invoke-Timed([string]$File, [string[]]$Arguments, [int]$Seconds) {
    $outFile = [IO.Path]::GetTempFileName()
    $p = Start-Process -FilePath $File -ArgumentList $Arguments -NoNewWindow -PassThru -RedirectStandardOutput $outFile -RedirectStandardError "$outFile.err"
    $null = $p.Handle   # caching the handle is what makes ExitCode readable after the process ends
    $done = $p.WaitForExit($Seconds * 1000)
    if (-not $done) { & taskkill /PID $p.Id /T /F *> $null } else { $p.WaitForExit() }   # the plain wait makes ExitCode available
    $text = (Get-Content $outFile, "$outFile.err" -ErrorAction SilentlyContinue) -join ' '
    Remove-Item $outFile, "$outFile.err" -Force -ErrorAction SilentlyContinue
    @{ Code = $(if ($done) { $p.ExitCode } else { -1 }); Out = $text; TimedOut = -not $done }
}

function Start-DockerDesktop {
    $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $desktop) { Write-Log 'starting Docker Desktop'; Start-Process -FilePath $desktop -WindowStyle Minimized }
    else { Write-Log "Docker Desktop not found at $desktop" }
}

function Restart-DockerDesktop {
    Write-Log 'docker engine hung; restarting Docker Desktop'
    Get-Process 'Docker Desktop', 'com.docker.backend' -ErrorAction SilentlyContinue | Stop-Process -Force
    & wsl --shutdown *> $null
    Start-Sleep -Seconds 5
    Start-DockerDesktop
}

# True once `docker info` answers; waits up to $Minutes.
function Wait-Engine([int]$Minutes) {
    $deadline = (Get-Date).AddMinutes($Minutes)
    do {
        $r = Invoke-Timed $docker.Source @('info', '--format', '{{.ServerVersion}}') 20
        if ($r.Code -eq 0) { return $true }
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)
    return $false
}

Write-Log 'logon task started'
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) { Write-Log 'docker not on PATH; giving up'; exit 1 }

$first = Invoke-Timed $docker.Source @('info', '--format', '{{.ServerVersion}}') 20
if ($first.Code -ne 0) { Start-DockerDesktop }   # Docker Desktop's own "start at sign-in" may be off

if (-not (Wait-Engine 5)) { Write-Log 'docker engine not ready after 5 minutes'; exit 1 }
Write-Log 'docker engine ready'

$python = Join-Path $avkHome 'venv\Scripts\python.exe'
for ($attempt = 1; $attempt -le 2; $attempt++) {
    $r = Invoke-Timed $python @('-m', 'avk', 'prod', 'up') 180
    if ($r.TimedOut) {
        Write-Log "avk prod up timed out after 180 s (attempt $attempt)"
        if ($attempt -eq 2) { exit 1 }
        Restart-DockerDesktop
        if (-not (Wait-Engine 5)) { Write-Log 'docker engine not ready after restart'; exit 1 }
        Write-Log 'docker engine ready after restart'
        continue
    }
    Write-Log "avk prod up exit=$($r.Code) $($r.Out)"
    exit $r.Code
}
