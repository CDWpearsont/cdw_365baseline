#Requires -Version 7.0
<#
.SYNOPSIS
    Retires the CIS Windows v4.0.0 policies superseded by the CIS Intune for Windows 11
    v5.0.0 set, from the reference tenant and from the repo.

.DESCRIPTION
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
    Connect-MgGraph -TenantId <reference-tenant-id> -Scopes DeviceManagementConfiguration.ReadWrite.All
    .\tools\Retire-CdwCisV4.ps1 -RepoPath .

.EXAMPLE
    .\tools\Retire-CdwCisV4.ps1 -RepoPath . -Execute
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $RepoPath,
    [switch] $Execute,
    [switch] $IncludeNoV5Equivalent,
    [switch] $IncludeAssigned
)

$ErrorActionPreference = 'Stop'

# Superseded by CIS Intune for Windows 11 v5.0.0
$Superseded = @(
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

# v4.0.0 policies whose settings are NOT in CIS v5.0.0 — kept unless -IncludeNoV5Equivalent
$NoV5Equivalent = @(
    'Win - CDW Baseline - ES - CIS L1 - Local Group Membership - D - Local Administrators - v4.0.0'
    'Win - CDW Baseline - SC - CIS L1 - Windows Apps - D - In-Box App Removal - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Windows Hello for Business - Cloud Kerberos Trust - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Windows User Experience - Copilot - v4.0.0'
    'Win - CDW Baseline - SC - CIS L2 - Windows User Experience - Settings Sync - v4.0.0'
)

$targets = @($Superseded)
if ($IncludeNoV5Equivalent) { $targets += $NoV5Equivalent }

# ---------------------------------------------------------------- prerequisites
if (-not (Get-Command Invoke-MgGraphRequest -ErrorAction SilentlyContinue)) {
    throw 'Microsoft.Graph.Authentication is required: Install-Module Microsoft.Graph.Authentication -Scope CurrentUser'
}
$ctx = Get-MgContext
if (-not $ctx) {
    throw 'Not connected. Run: Connect-MgGraph -TenantId <reference-tenant-id> -Scopes DeviceManagementConfiguration.ReadWrite.All'
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

Write-Host "Tenant: $($ctx.TenantId)   Account: $($ctx.Account)" -ForegroundColor Cyan

# ---------------------------------------------------------------- read tenant + repo
$tenantPolicies   = Get-GraphAll "$base/configurationPolicies?`$select=id,name"
$tenantCompliance = Get-GraphAll "$base/deviceCompliancePolicies?`$select=id,displayName"
$tenantNames      = [System.Collections.Generic.HashSet[string]]::new([string[]]@($tenantPolicies.name + $tenantCompliance.displayName))

$repoFiles = Get-ChildItem -Path $intuneRoot -Filter *.json -Recurse -File | ForEach-Object {
    $j = Get-Content -Raw $_.FullName | ConvertFrom-Json -Depth 64
    $n = if ($j.name) { $j.name } else { $j.displayName }
    if ($n) { [pscustomobject]@{ Name = $n; Path = $_.FullName } }
}

# ---------------------------------------------------------------- safety: v5 set present in tenant
$v5Files   = @($repoFiles | Where-Object { $_.Name -match 'CIS L[12] .* - v5\.0\.0$' })
$v5Missing = @($v5Files | Where-Object { -not $tenantNames.Contains($_.Name) })
Write-Host "v5.0.0 policies in repo: $($v5Files.Count); present in tenant: $($v5Files.Count - $v5Missing.Count)"
if ($v5Files.Count -eq 0) { throw 'No v5.0.0 CIS policy files found in the repo — nothing to supersede with.' }
if ($v5Missing.Count -gt 0) {
    Write-Host 'These v5.0.0 policies are not in the tenant — deploy them first:' -ForegroundColor Red
    $v5Missing | ForEach-Object { Write-Host "  $($_.Name)" }
    if ($Execute) { throw 'Aborting: v5.0.0 set incomplete in tenant. Nothing was deleted.' }
}

# ---------------------------------------------------------------- build plan
$plan = foreach ($name in $targets) {
    $tp = @($tenantPolicies | Where-Object { $_.name -ceq $name })
    $rf = @($repoFiles     | Where-Object { $_.Name -ceq $name })
    $assigned = $false
    foreach ($p in $tp) {
        $a = Invoke-MgGraphRequest -Method GET -Uri "$base/configurationPolicies('$($p.id)')/assignments" -OutputType PSObject
        if (@($a.value).Count -gt 0) { $assigned = $true }
    }
    [pscustomobject]@{
        Name       = $name
        TenantIds  = @($tp.id)
        RepoFiles  = @($rf.Path)
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

if (-not $IncludeNoV5Equivalent) {
    Write-Host 'Kept (no v5.0.0 equivalent — use -IncludeNoV5Equivalent to retire):' -ForegroundColor Yellow
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
    foreach ($id in $p.TenantIds) {
        try {
            Invoke-MgGraphRequest -Method DELETE -Uri "$base/configurationPolicies('$id')" | Out-Null
            [pscustomobject]@{ Result = 'deleted (tenant)'; Name = $p.Name }
        } catch {
            [pscustomobject]@{ Result = "FAILED (tenant): $($_.Exception.Message)"; Name = $p.Name }
        }
    }
    foreach ($f in $p.RepoFiles) {
        try {
            if ($useGit) { git -C $RepoPath rm --quiet -- $f | Out-Null } else { Remove-Item -LiteralPath $f }
            [pscustomobject]@{ Result = 'removed (repo)'; Name = $p.Name }
        } catch {
            [pscustomobject]@{ Result = "FAILED (repo): $($_.Exception.Message)"; Name = $p.Name }
        }
    }
}
$log | Format-Table -AutoSize | Out-String -Width 250 | Write-Host
Write-Host 'Done. Review with git status, then commit and push.' -ForegroundColor Green
