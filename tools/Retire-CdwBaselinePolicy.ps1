#Requires -Version 7.0
<#
.SYNOPSIS
    Retires superseded CDW Baseline CIS policies from the reference tenant and the repo.

.DESCRIPTION
    Pick the set with -PolicySet. Each set names the policies a newer benchmark replaces.

    Run it in two stages, because the repo and the tenant unblock each other in that order:

      -Stage RepoFiles  removes the superseded JSONs from the repo only. This clears the
                        duplicate ids that stop the catalog and manifest builds, so the new
                        policies reach the site and can be deployed. Nothing in the tenant
                        changes, so the next backup would re-export the old policies —
                        finish stage two the same day.
      -Stage Tenant     deletes the superseded policies from the tenant, once the new set is
                        deployed. Removes any superseded repo files still present.
      -Stage Both       both at once (the default). Only works when the new set is already
                        deployed, which normally means the repo files were never in the way.

    Dry run by default: reports what would be removed and changes nothing.
    With -Execute (after a typed confirmation):
      1. Deletes the matching v4.0.0 policies from the connected tenant.
      2. Removes the matching v4.0.0 JSON files from the repo (git rm when the repo is a
         git working copy, otherwise Remove-Item). You commit and push.

    Safety checks:
      * Every v5.0.0 policy file in the repo must already exist in the tenant, otherwise
        nothing is deleted (the nightly backup would remove v5 files missing from the tenant).
      * Policies with assignments are skipped unless -IncludeAssigned is used.
      * Policies are matched by exact name, never by pattern.

    Not touched: v4.0.0 Defender, ASR, Edge, Office and OneDrive policies (not superseded
    by this benchmark). Five v4.0.0 policies have no v5.0.0 equivalent; they are kept
    unless -IncludeNoV5Equivalent is used.

.EXAMPLE
    # Stage one — unblock the builds. No tenant connection needed.
    .\tools\Retire-CdwBaselinePolicy.ps1 -RepoPath . -PolicySet DefenderAntivirusV1 -Stage RepoFiles -Execute

.EXAMPLE
    # Stage two — after the new policies are deployed.
    Connect-MgGraph -TenantId <reference-tenant-id> -Scopes DeviceManagementConfiguration.ReadWrite.All
    .\tools\Retire-CdwBaselinePolicy.ps1 -RepoPath . -PolicySet DefenderAntivirusV1 -Stage Tenant -Execute
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RepoPath,
    [ValidateSet('WindowsV5','DefenderAntivirusV1')] [string] $PolicySet = 'WindowsV5',
    [ValidateSet('RepoFiles','Tenant','Both')] [string] $Stage = 'Both',
    [switch] $Execute,
    [switch] $IncludeNoV5Equivalent,
    [switch] $IncludeAssigned
)

$ErrorActionPreference = 'Stop'

# ---------------------------------------------------------------- policy sets
$WindowsV5Superseded = @(
    'Win - CDW Baseline - ES - CIS L1 - Encryption - D - BitLocker (OS Disk) - v4.0.0'
    'Win - CDW Baseline - ES - CIS L1 - Windows Firewall - D - Firewall Configuration - v4.0.0'
    'Win - CDW Baseline - ES - CIS L1 - Windows Hello for Business - D - WHfB Configuration - v4.0.0'
    'Win - CDW Baseline - ES - CIS L1 - Windows LAPS - D - LAPS Configuration - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Above Lock - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Administrative Templates - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Auditing - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Delivery Optimization - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Delivery Optimization - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Device Guard - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Device Lock - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Enhanced Phishing Protection - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Experience - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Lanman Workstation - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Local Security Authority - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Local Security Policies - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Microsoft App Store - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Privacy - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Search - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Smart Screen - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Sudo - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - System - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - System Services - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - User Rights - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Virtualization Based Technology - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Widgets - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Windows Ink Workspace - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Windows Sandbox - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Windows Update For Business - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Windows Update for Business - Delivery Optimisation - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Windows User Experience - Feature Configuration - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Microsoft Store - D - Configuration - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Microsoft Store - U - Configuration - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Windows Update for Business - Reports and Telemetry - v4.0.0'
)

$WindowsV5NoEquivalent = @(
    'Win - CDW Baseline - ES - CIS L1 - Local Group Membership - D - Local Administrators - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Windows Apps - D - In-Box App Removal - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Windows Hello for Business - Cloud Kerberos Trust - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Windows User Experience - Copilot - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Windows User Experience - Settings Sync - v4.0.0'
)

# Superseded by the CIS Defender Antivirus v1.0.0 benchmark. Every setting in these is
# covered by the v1.0.0 set.
$DefenderAntivirusV1Superseded = @(
    'Win - CDW Baseline - ES - CIS L1 - Attack Surface Reduction - D - ASR Rules - v4.0.0'
    'Win - CDW Baseline - ES - CIS L1 - Defender Antivirus - D - Antivirus Configuration - v4.0.0'
    'Win - CDW Baseline - ES - CIS L1 - Defender Antivirus - D - Security Experience - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Defender - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Windows Defender Security Center - D - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Defender Antivirus - Additional Configuration - v4.0.0'
)
$DefenderAntivirusV1NoEquivalent = @()

# Replacement versions do not always increase: the Defender benchmark restarts at v1.0.0
# after v4.0.0 files, so each set names its replacement version explicitly rather than
# assuming the highest wins.
$Sets = @{
    WindowsV5 = @{
        Superseded   = $WindowsV5Superseded
        NoEquivalent = $WindowsV5NoEquivalent
        Replacement  = 'v5.0.0'
        Pattern      = 'CIS L[12] '
        Benchmark    = 'CIS Microsoft Intune for Windows 11 Benchmark v5.0.0'
    }
    DefenderAntivirusV1 = @{
        Superseded   = $DefenderAntivirusV1Superseded
        NoEquivalent = $DefenderAntivirusV1NoEquivalent
        Replacement  = 'v1.0.0'
        Pattern      = 'Defender Antivirus|Attack Surface Reduction'
        Benchmark    = 'CIS Microsoft Intune for Microsoft Defender Antivirus Benchmark v1.0.0'
    }
}
$set            = $Sets[$PolicySet]
$NoV5Equivalent = $set.NoEquivalent
$targets        = @($set.Superseded)
if ($IncludeNoV5Equivalent) { $targets += $NoV5Equivalent }

# ---------------------------------------------------------------- prerequisites
$touchTenant = $Stage -in @('Tenant','Both')
$touchRepo   = $Stage -in @('RepoFiles','Both')
$ctx = $null
if ($touchTenant) {
    if (-not (Get-Command Invoke-MgGraphRequest -ErrorAction SilentlyContinue)) {
        throw 'Microsoft.Graph.Authentication is required: Install-Module Microsoft.Graph.Authentication -Scope CurrentUser'
    }
    $ctx = Get-MgContext
    if (-not $ctx) {
        throw 'Not connected. Run: Connect-MgGraph -TenantId <reference-tenant-id> -Scopes DeviceManagementConfiguration.ReadWrite.All'
    }
}
$intuneRoot = Join-Path $RepoPath 'IntuneConfig'
if (-not (Test-Path $intuneRoot)) { throw "IntuneConfig not found under $RepoPath" }

$base = 'https://graph.microsoft.com/beta/deviceManagement'
function Get-GraphAll([string] $Uri) {
    $items = [System.Collections.Generic.List[object]]::new()
    while ($Uri) {
        $page = Invoke-MgGraphRequest -Method GET -Uri $Uri -OutputType PSObject
        foreach ($v in @($page.value)) { if ($v) { $items.Add($v) } }
        $Uri = $page.'@odata.nextLink'
    }
    return $items
}

if ($touchTenant) { Write-Host "Tenant: $($ctx.TenantId)   Account: $($ctx.Account)" -ForegroundColor Cyan }
Write-Host "Policy set: $PolicySet — superseded by $($set.Benchmark)" -ForegroundColor Cyan
Write-Host "Stage: $Stage$(if (-not $touchTenant) { ' (repo only — the tenant is not touched)' })" -ForegroundColor Cyan

# ---------------------------------------------------------------- read tenant + repo
$tenantPolicies   = @()
$tenantNames      = [System.Collections.Generic.HashSet[string]]::new()
if ($touchTenant) {
    $tenantPolicies   = Get-GraphAll "$base/configurationPolicies?`$select=id,name"
    $tenantCompliance = Get-GraphAll "$base/deviceCompliancePolicies?`$select=id,displayName"
    $tenantNames      = [System.Collections.Generic.HashSet[string]]::new([string[]]@($tenantPolicies.name + $tenantCompliance.displayName))
}

$repoFiles = Get-ChildItem -Path $intuneRoot -Filter *.json -Recurse -File | ForEach-Object {
    $j = Get-Content -Raw $_.FullName | ConvertFrom-Json -Depth 64
    $n = if ($j.name) { $j.name } else { $j.displayName }
    if ($n) { [pscustomobject]@{ Name = $n; Path = $_.FullName } }
}

# ---------------------------------------------------------------- safety: v5 set present in tenant
$verEsc    = [regex]::Escape($set.Replacement)
$v5Files   = @($repoFiles | Where-Object { $_.Name -match $set.Pattern -and $_.Name -match "$verEsc$" })
if ($v5Files.Count -eq 0) { throw "No $($set.Replacement) policy files found in the repo for $PolicySet — nothing to supersede with." }

if ($touchTenant) {
    # Deleting from the tenant is only safe once the replacements are live there, otherwise
    # the workload is left unprotected and the backup deletes the new repo files.
    $v5Missing = @($v5Files | Where-Object { -not $tenantNames.Contains($_.Name) })
    Write-Host "$($set.Replacement) policies in repo: $($v5Files.Count); present in tenant: $($v5Files.Count - $v5Missing.Count)"
    if ($v5Missing.Count -gt 0) {
        Write-Host "These $($set.Replacement) policies are not in the tenant — deploy them first:" -ForegroundColor Red
        $v5Missing | ForEach-Object { Write-Host "  $($_.Name)" }
        Write-Host "If the site cannot show them because the builds are failing, run -Stage RepoFiles first." -ForegroundColor Yellow
        if ($Execute) { throw "Aborting: $($set.Replacement) set incomplete in tenant. Nothing was deleted." }
    }
} else {
    Write-Host "$($set.Replacement) policies in repo: $($v5Files.Count)"
}

# ---------------------------------------------------------------- build plan
$plan = foreach ($name in $targets) {
    $tp = if ($touchTenant) { @($tenantPolicies | Where-Object { $_.name -ceq $name }) } else { @() }
    $rf = @($repoFiles     | Where-Object { $_.Name -ceq $name })
    $assigned = $false
    foreach ($p in $tp) {
        $a = Invoke-MgGraphRequest -Method GET -Uri "$base/configurationPolicies('$($p.id)')/assignments" -OutputType PSObject
        if (@($a.value).Count -gt 0) { $assigned = $true }
    }
    # Build arrays from matches only — @($empty.Property) yields @($null), a phantom entry
    [pscustomobject]@{
        Name       = $name
        TenantIds  = @($tp | ForEach-Object { $_.id }   | Where-Object { $_ })
        RepoFiles  = @($rf | ForEach-Object { $_.Path } | Where-Object { $_ })
        Assigned   = $assigned
        Action     = if ($assigned -and -not $IncludeAssigned) { 'SKIP (assigned)' }
                     elseif (-not $tp -and -not $rf)          { 'none (already gone)' }
                     else                                      { 'retire' }
    }
}

$plan | Select-Object Action,
    @{ n = 'Tenant'; e = { $_.TenantIds.Count } },
    @{ n = 'Repo';   e = { $_.RepoFiles.Count } },
    Name | Format-Table -AutoSize | Out-String -Width 250 | Write-Host

if (-not $IncludeNoV5Equivalent -and $NoV5Equivalent.Count -gt 0) {
    Write-Host "Kept (no $($set.Replacement) equivalent — use -IncludeNoV5Equivalent to retire):" -ForegroundColor Yellow
    $NoV5Equivalent | ForEach-Object { Write-Host "  $_" }
}

$todo = @($plan | Where-Object Action -eq 'retire')
if (-not $Execute) {
    Write-Host "`nDry run: $($todo.Count) policies would be retired. Re-run with -Execute to apply." -ForegroundColor Cyan
    return
}
if ($todo.Count -eq 0) { Write-Host 'Nothing to retire.'; return }

$answer = Read-Host "`nType RETIRE to delete $($todo.Count) policies from tenant $($ctx.TenantId) and remove their repo files"
if ($answer -cne 'RETIRE') { Write-Host 'Cancelled. Nothing changed.'; return }

# ---------------------------------------------------------------- execute
$useGit = (Test-Path (Join-Path $RepoPath '.git')) -and (Get-Command git -ErrorAction SilentlyContinue)
$log = foreach ($p in $todo) {
    if ($touchTenant) {
     foreach ($id in $p.TenantIds) {
        try {
            Invoke-MgGraphRequest -Method DELETE -Uri "$base/configurationPolicies('$id')" | Out-Null
            [pscustomobject]@{ Result = 'deleted (tenant)'; Name = $p.Name }
        } catch {
            [pscustomobject]@{ Result = "FAILED (tenant): $($_.Exception.Message)"; Name = $p.Name }
        }
     }
    }
    foreach ($f in $p.RepoFiles) {
        try {
            if (-not (Test-Path -LiteralPath $f)) {
                [pscustomobject]@{ Result = 'already removed (repo)'; Name = $p.Name }
                continue
            }
            if ($useGit) {
                $out = git -C $RepoPath rm --quiet -- $f 2>&1
                # git reports failure via exit code, not a PowerShell error
                if ($LASTEXITCODE -ne 0) { throw "git rm exited $LASTEXITCODE`: $out" }
            } else {
                Remove-Item -LiteralPath $f
            }
            [pscustomobject]@{ Result = 'removed (repo)'; Name = $p.Name }
        } catch {
            [pscustomobject]@{ Result = "FAILED (repo): $($_.Exception.Message)"; Name = $p.Name }
        }
    }
}
$log | Format-Table -AutoSize | Out-String -Width 250 | Write-Host
Write-Host 'Done. Review with git status, then commit and push.' -ForegroundColor Green
if (-not $touchTenant) {
    Write-Host "The superseded policies are still in the tenant. Re-run with -Stage Tenant once the $($set.Replacement) set is deployed — before the 04:00 backup re-exports them." -ForegroundColor Yellow
}
