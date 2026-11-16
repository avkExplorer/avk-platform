# Writes %AVK_HOME%\secrets\platform\cloudflare.env. The token is typed hidden and never echoed.
# Run in your own terminal:  powershell -ExecutionPolicy Bypass -File tools\set-cloudflare-secrets.ps1
$ErrorActionPreference = 'Stop'
$avkHome = if ($env:AVK_HOME) { $env:AVK_HOME } else { 'C:\avk' }
$path = Join-Path $avkHome 'secrets\platform\cloudflare.env'

$account = (Read-Host 'Cloudflare Account ID (dashboard > domain > Overview, right column)').Trim()
$zone    = (Read-Host 'Cloudflare Zone ID (same place)').Trim()
$secure  = Read-Host 'Cloudflare API token (hidden)' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }

foreach ($pair in @(@('Account ID', $account), @('Zone ID', $zone))) {
    if ($pair[1] -notmatch '^[0-9a-f]{32}$') { throw "$($pair[0]) should be 32 hex characters" }
}
if ($token.Length -lt 30) { throw 'token looks too short' }

# Check the token with Cloudflare before saving it.
$check = Invoke-RestMethod -Uri 'https://api.cloudflare.com/client/v4/user/tokens/verify' -Headers @{ Authorization = "Bearer $token" }
if (-not $check.success -or $check.result.status -ne 'active') { throw 'Cloudflare says the token is not active' }

$body = "CLOUDFLARE_API_TOKEN=$token`nCLOUDFLARE_ACCOUNT_ID=$account`nCLOUDFLARE_ZONE_ID=$zone`n"
[IO.File]::WriteAllText($path, $body, (New-Object Text.UTF8Encoding $false))
$token = $null; $body = $null
"saved $path (token verified as active; it is not shown)"
