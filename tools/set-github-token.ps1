# Stores the deployer's GitHub token in %AVK_HOME%\secrets\platform\github.env. The token is typed hidden, never echoed.
# Run in your own terminal:  powershell -ExecutionPolicy Bypass -File tools\set-github-token.ps1
#
# Create the token first (GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens):
#   Name: avk-deployer      Expiration: 90 days (put the renewal date in your calendar)
#   Repository access: Only select repositories > hello
#   Permissions (Repository): Deployments = Read and write, Contents = Read-only, Metadata = Read-only
# The token can create a Deployment but cannot make either signature, so it cannot get anything deployed.
$ErrorActionPreference = 'Stop'
$avkHome = if ($env:AVK_HOME) { $env:AVK_HOME } else { 'C:\avk' }
$path = Join-Path $avkHome 'secrets\platform\github.env'

$secure = Read-Host 'Deployer GitHub token (hidden; starts with github_pat_)' -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr) }
if ($token -notmatch '^(github_pat_|ghp_)[A-Za-z0-9_]{20,}$') { throw 'that does not look like a GitHub token (should start with github_pat_)' }

# Check it can do the deployer's job: read deployments of hello.
$h = @{ Authorization = "Bearer $token"; Accept = 'application/vnd.github+json'; 'User-Agent' = 'avk-setup' }
try {
    $null = Invoke-RestMethod -Uri 'https://api.github.com/repos/avkExplorer/hello/deployments?environment=prod&per_page=1' -Headers $h
} catch {
    throw "GitHub refused the token for avkExplorer/hello deployments: $($_.Exception.Message). Check the repository and the Deployments permission."
}
try {
    $null = Invoke-RestMethod -Uri 'https://api.github.com/repos/avkExplorer/hello/contents/avk.yaml' -Headers $h
} catch {
    throw "The token cannot read avk.yaml (needs Contents: Read-only): $($_.Exception.Message)"
}

[IO.File]::WriteAllText($path, "GITHUB_TOKEN=$token`n", (New-Object Text.UTF8Encoding $false))
$token = $null
"saved $path (token verified: can read deployments and avk.yaml; it is not shown)"
