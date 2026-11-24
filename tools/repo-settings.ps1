# Applies the platform's standard protections to a GitHub repository. Safe to re-run (every call is idempotent).
#
#   powershell -ExecutionPolicy Bypass -File tools\repo-settings.ps1 -Repo avkExplorer/hello -RequiredChecks "ci / gates" -ProdEnvironment
#
# What it sets (all free on public repositories):
#   - squash merges only, branches deleted after merge
#   - Dependabot alerts and security updates, secret scanning with push protection, private vulnerability reporting
#   - Actions: read-only default token, workflows cannot approve pull requests, fork PRs from ANY outside
#     contributor wait for the owner's approval
#   - main: pull request required (0 approvals, the owner is the only reviewer), listed status checks must pass,
#     no force pushes, no deletion. Admins are NOT locked out (enforce_admins off), so the owner can still fix a broken pipeline.
#   - optional prod environment: the owner is the required reviewer (self-review allowed: the owner is the only user)
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Repo,
    [string[]]$RequiredChecks = @(),
    [switch]$ProdEnvironment,
    [string]$Branch = 'main'
)
$ErrorActionPreference = 'Continue'
$gh = if (Get-Command gh -ErrorAction SilentlyContinue) { 'gh' } else { "$env:ProgramFiles\GitHub CLI\gh.exe" }
$failed = 0

function Call([string]$what, [string]$method, [string]$path, $body = $null) {
    if ($null -ne $body) {
        # A temp file without a byte-order mark: piping text from Windows PowerShell can prepend one, and GitHub rejects it.
        $tmp = [IO.Path]::GetTempFileName()
        try {
            [IO.File]::WriteAllText($tmp, ($body | ConvertTo-Json -Depth 8 -Compress), (New-Object Text.UTF8Encoding $false))
            $out = & $gh api -X $method $path --input $tmp 2>&1
        } finally { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    } else {
        $out = & $gh api -X $method $path 2>&1
    }
    if ($LASTEXITCODE -eq 0) { "ok    $what" }
    else { $script:failed++; "fail  $what :: " + (($out | Out-String).Trim() -replace '\s+', ' ').Substring(0, [Math]::Min(220, (($out | Out-String).Trim()).Length)) }
}

Call 'merge settings' PATCH "repos/$Repo" @{ allow_squash_merge = $true; allow_merge_commit = $false; allow_rebase_merge = $false; delete_branch_on_merge = $true }
Call 'dependabot alerts' PUT "repos/$Repo/vulnerability-alerts"
Call 'dependabot security updates' PUT "repos/$Repo/automated-security-fixes"
Call 'secret scanning + push protection' PATCH "repos/$Repo" @{ security_and_analysis = @{
    secret_scanning = @{ status = 'enabled' }; secret_scanning_push_protection = @{ status = 'enabled' } } }
Call 'private vulnerability reporting' PUT "repos/$Repo/private-vulnerability-reporting"
Call 'actions: read-only token, no PR approval' PUT "repos/$Repo/actions/permissions/workflow" @{
    default_workflow_permissions = 'read'; can_approve_pull_request_reviews = $false }
Call 'actions: approval for all outside contributors' PUT "repos/$Repo/actions/permissions/fork-pr-contributor-approval" @{
    approval_policy = 'all_external_contributors' }

$protection = @{
    required_status_checks        = if ($RequiredChecks.Count) { @{ strict = $false; contexts = $RequiredChecks } } else { $null }
    enforce_admins                = $false
    required_pull_request_reviews = @{ required_approving_review_count = 0; dismiss_stale_reviews = $true }
    restrictions                  = $null
    allow_force_pushes            = $false
    allow_deletions               = $false
}
Call "branch protection on $Branch" PUT "repos/$Repo/branches/$Branch/protection" $protection

if ($ProdEnvironment) {
    $owner = $Repo.Split('/')[0]
    $uid = [int](& $gh api "users/$owner" --jq .id)
    Call 'environment prod (required reviewer: owner, main only)' PUT "repos/$Repo/environments/prod" @{
        reviewers = @(@{ type = 'User'; id = $uid }); prevent_self_review = $false
        deployment_branch_policy = @{ protected_branches = $false; custom_branch_policies = $true } }
    Call 'environment prod: allow branch main' POST "repos/$Repo/environments/prod/deployment-branch-policies" @{ name = $Branch; type = 'branch' }
}

if ($failed) { "`n$failed call(s) failed: read the lines above."; exit 1 } else { "`nall settings applied to $Repo" }
