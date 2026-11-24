# Writes %AVK_HOME%\secrets\prod\platform-mail.env with the Resend API key. The key is typed hidden, never echoed.
# Run in your own terminal:  powershell -ExecutionPolicy Bypass -File tools\set-mail-secret.ps1
$ErrorActionPreference = 'Stop'
$avkHome = if ($env:AVK_HOME) { $env:AVK_HOME } else { 'C:\avk' }
$dir = Join-Path $avkHome 'secrets\prod'
New-Item -ItemType Directory -Force $dir | Out-Null
$path = Join-Path $dir 'platform-mail.env'

$secure = Read-Host 'Resend API key (hidden; starts with re_)' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $key = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if ($key -notmatch '^re_[A-Za-z0-9_\-]{20,}$') { throw 'that does not look like a Resend API key (should start with re_)' }

# Check the key without sending anything: listing domains needs no email and proves the key works.
# A sending-only key is refused by this call (HTTP 401 restricted_api_key), which is also fine.
$state = 'unknown'
try {
    $r = Invoke-RestMethod -Uri 'https://api.resend.com/domains' -Headers @{ Authorization = "Bearer $key" }
    $state = 'full access (consider a sending-only key)'
} catch {
    $code = [int]$_.Exception.Response.StatusCode
    if ($code -eq 401 -or $code -eq 403) { $state = 'restricted (sending-only): good' } else { throw "Resend check failed: HTTP $code" }
}

$body = "RESEND_API_KEY=$key`nMAIL_FROM_APPS=noreply@notify.avkaiexplorer.com`n"
[IO.File]::WriteAllText($path, $body, (New-Object Text.UTF8Encoding $false))
$key = $null; $body = $null
"saved $path  (key type: $state; the key is not shown)"
