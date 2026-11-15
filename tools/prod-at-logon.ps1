# Runs at the owner's Windows logon (Task Scheduler): wait for Docker, then bring prod up.
# Log: %AVK_HOME%\logs\prod-at-logon.log
$ErrorActionPreference = 'Continue'
$avkHome = if ($env:AVK_HOME) { $env:AVK_HOME } else { 'C:\avk' }
$log = Join-Path $avkHome 'logs\prod-at-logon.log'
function Write-Log($msg) { "$(Get-Date -Format o) $msg" | Out-File -FilePath $log -Append -Encoding utf8 }

Write-Log 'logon task started'
$docker = Get-Command docker -ErrorAction SilentlyContinue
if (-not $docker) { Write-Log 'docker not on PATH; giving up'; exit 1 }

& $docker.Source info --format '{{.ServerVersion}}' *> $null
if ($LASTEXITCODE -ne 0) {
    # Docker Desktop's own "start at sign-in" may be off; start it ourselves.
    $desktop = Join-Path $env:ProgramFiles 'Docker\Docker\Docker Desktop.exe'
    if (Test-Path $desktop) { Write-Log 'starting Docker Desktop'; Start-Process -FilePath $desktop -WindowStyle Minimized }
    else { Write-Log "Docker Desktop not found at $desktop" }
}

$deadline = (Get-Date).AddMinutes(5)
do {
    & $docker.Source info --format '{{.ServerVersion}}' *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 10
} while ((Get-Date) -lt $deadline)
if ($LASTEXITCODE -ne 0) { Write-Log 'docker engine not ready after 5 minutes'; exit 1 }
Write-Log 'docker engine ready'

$python = Join-Path $avkHome 'venv\Scripts\python.exe'
$out = & $python -m avk prod up 2>&1
Write-Log "avk prod up exit=$LASTEXITCODE $($out -join ' ')"
exit $LASTEXITCODE
