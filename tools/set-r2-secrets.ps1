# Adds the Cloudflare R2 off-site backup settings to %AVK_HOME%\secrets\platform\backup.env (keeps the restic password).
# Keys are typed hidden, never echoed. Run in your own terminal:  powershell -ExecutionPolicy Bypass -File tools\set-r2-secrets.ps1
#
# Create these first in the Cloudflare dashboard:
#   R2 > Create bucket (name e.g. avk-backups; any location)
#   R2 > Manage API tokens > Create API token: permission "Object Read & Write", scoped to that ONE bucket.
#   Copy the Access Key ID and Secret Access Key (shown once) and your Account ID (R2 overview page).
$ErrorActionPreference = 'Stop'
$avkHome = if ($env:AVK_HOME) { $env:AVK_HOME } else { 'C:\avk' }
$path = Join-Path $avkHome 'secrets\platform\backup.env'
if (-not (Test-Path $path)) { throw "$path not found: run  avk secrets platform  first" }

function Plain([string]$prompt) {
    $s = Read-Host $prompt -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { [Runtime.InteropServices.Marshal]::PtrToStringBSTR($b).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}
$account = (Read-Host 'Cloudflare Account ID (32 hex characters)').Trim()
$bucket = (Read-Host 'R2 bucket name').Trim()
$key = Plain 'R2 Access Key ID (hidden)'
$secret = Plain 'R2 Secret Access Key (hidden)'
if ($account -notmatch '^[0-9a-f]{32}$') { throw 'Account ID should be 32 hex characters' }
if ($bucket -notmatch '^[a-z0-9][a-z0-9-]{2,62}$') { throw 'bucket name looks wrong' }
if ($key.Length -lt 16 -or $secret.Length -lt 32) { throw 'the keys look too short' }

# Keep the existing lines (restic password, local repo path); replace any old R2 lines.
$kept = Get-Content $path | Where-Object { $_ -notmatch '^R2_' }
$lines = @($kept) + "R2_ACCOUNT_ID=$account", "R2_BUCKET=$bucket", "R2_ACCESS_KEY_ID=$key", "R2_SECRET_ACCESS_KEY=$secret"
[IO.File]::WriteAllText($path, (($lines -join "`n") + "`n"), (New-Object Text.UTF8Encoding $false))
$key = $null; $secret = $null
"saved R2 settings to $path (values are not shown). Next:  avk backup run"
