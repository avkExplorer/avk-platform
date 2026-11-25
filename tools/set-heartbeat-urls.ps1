# Writes %AVK_HOME%\secrets\platform\heartbeats.env with the three heartbeat URLs from Better Stack. Each URL is typed hidden and never echoed.
# Run in your own terminal:  powershell -ExecutionPolicy Bypass -File tools\set-heartbeat-urls.ps1
# A heartbeat URL is a secret: anyone who has it can fake "the job ran". Press Enter to keep a value you saved before.
$ErrorActionPreference = 'Stop'
$avkHome = if ($env:AVK_HOME) { $env:AVK_HOME } else { 'C:\avk' }
$dir = Join-Path $avkHome 'secrets\platform'
New-Item -ItemType Directory -Force $dir | Out-Null
$path = Join-Path $dir 'heartbeats.env'

$existing = @{}
if (Test-Path $path) {
    foreach ($line in Get-Content $path) { $k, $v = $line -split '=', 2; if ($k -and $v) { $existing[$k.Trim()] = $v.Trim() } }
}

$names = [ordered]@{
    HEARTBEAT_BACKUP_RUN    = 'nightly backup (period 1 day)'
    HEARTBEAT_BACKUP_VERIFY = 'weekly backup verify (period 7 days)'
    HEARTBEAT_DEPLOY_AGENT  = 'deploy agent / health checks (period 5 minutes)'
}
$out = [ordered]@{}
foreach ($key in $names.Keys) {
    $kept = if ($existing.ContainsKey($key)) { ' [Enter keeps the saved one]' } else { '' }
    $secure = Read-Host "Heartbeat URL for $($names[$key])$kept (hidden)" -AsSecureString
    $bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $url = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
    if (-not $url -and $existing.ContainsKey($key)) { $url = $existing[$key] }
    if (-not $url) { continue }                                  # leave a heartbeat unset: the job simply does not ping
    if ($url -notmatch '^https://[A-Za-z0-9._~:/?#\[\]@!$&''()*+,;=%-]+$') { throw "$key does not look like an https URL" }
    $out[$key] = $url
}
$body = ($out.GetEnumerator() | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join "`n"
[IO.File]::WriteAllText($path, $body + "`n", (New-Object Text.UTF8Encoding $false))
"saved $path with $($out.Count) heartbeat(s); the URLs are not shown"
$body = $null; $out = $null; $existing = $null
