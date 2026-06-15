#requires -Version 7.0
<#
  Invoke-Runner.ps1  —  CDW 365 Baseline PowerShell Runner (entry point)

  Granular by design:
    • The front end sends an explicit list of selected policies.
    • The runner fetches ONLY those JSON files from GitHub (source of truth).
    • Every apply is create-or-update BY OBJECT NAME — existing tenant
      configuration that isn't in the selection is never read or modified.
    • One policy failing does not abort the batch.

  Payload contract (base64 JSON in $env:RUNNER_PAYLOAD):
  {
    "tenantId": "contoso.onmicrosoft.com",
    "config":   { "mdcaUrl": "https://contoso.eu2.portal.cloudappsecurity.com" },
    "tokens":   {                       // one delegated token PER RESOURCE
      "exo":  "<token for https://outlook.office365.com/.default>",
      "scc":  "<token for https://ps.compliance.protection.outlook.com/.default>",
      "spo":  "<token for https://contoso.sharepoint.com/.default>",
      "mdca": "<token for the Defender for Cloud Apps API>"
    },
    "selected": [                        // <-- the granular selection
      { "workload": "exo",  "path": "ExchangeOnline/SafeLinks/Safe_Links_excluding_email.json" },
      { "workload": "mdca", "path": "DefenderCloudApps/Policies/Bulk_Download_Monitoring.json" }
    ]
  }

  Result: JSON array on stdout (one entry per selected policy) for the
  front-end deploy log — mirrors the Graph deployer's Deployed/Skipped/Failed.
#>

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# ── Repo (source of truth) — matches the Graph front end ────────────────
$RepoOwner = 'CDWpearsont'
$RepoName  = 'cdw_365baseline'
$RepoBranch= 'main'
$RawBase   = "https://raw.githubusercontent.com/$RepoOwner/$RepoName/$RepoBranch"

# ── Workload registry ───────────────────────────────────────────────────
# Each file in Workloads/ registers itself here. Adding a workload =
# dropping in a new file. Nothing else in the runner changes.
$script:Workloads = @{}
$script:Connected = @{}

function Register-Workload {
    param(
        [Parameter(Mandatory)][string]      $Key,        # 'exo', 'mdca', ...
        [Parameter(Mandatory)][string]      $TokenKey,   # which token in payload.tokens
        [Parameter(Mandatory)][scriptblock] $Connect,    # param($Ctx)        -> connects once
        [Parameter(Mandatory)][scriptblock] $Apply,      # param($Policy,$Ctx)-> 'created'|'updated'|'skipped'
        [scriptblock]                        $Disconnect = {}
    )
    $script:Workloads[$Key] = [pscustomobject]@{
        TokenKey = $TokenKey; Connect = $Connect; Apply = $Apply; Disconnect = $Disconnect
    }
}

# Load all workload handlers
Get-ChildItem "$PSScriptRoot/Workloads" -Filter '*.ps1' | ForEach-Object { . $_.FullName }

# ── Parse payload ────────────────────────────────────────────────────────
if (-not $env:RUNNER_PAYLOAD) { throw 'RUNNER_PAYLOAD env var is empty.' }
$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:RUNNER_PAYLOAD)) | ConvertFrom-Json

$ctx = [pscustomobject]@{
    TenantId = $payload.tenantId
    Tokens   = $payload.tokens
    Config   = $payload.config
}

# ── Fetch one selected JSON fresh from GitHub (cache-busted, like the UI) ──
function Get-PolicyJson {
    param([string]$Path)
    $bust = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    Invoke-RestMethod -Uri "$RawBase/$Path`?t=$bust" -Method Get
}

# ── Process the selection ──────────────────────────────────────────────────
$results = [System.Collections.Generic.List[object]]::new()

foreach ($item in $payload.selected) {
    $entry = [ordered]@{
        path     = $item.path
        workload = $item.workload
        identity = $null
        action   = $null
        status   = 'failed'
        message  = $null
    }
    try {
        $wl = $script:Workloads[$item.workload]
        if (-not $wl) { throw "No handler registered for workload '$($item.workload)'." }

        # Connect once per workload, lazily, on first use.
        if (-not $script:Connected.ContainsKey($item.workload)) {
            $token = $ctx.Tokens.$($wl.TokenKey)
            if (-not $token) { throw "No '$($wl.TokenKey)' token supplied for workload '$($item.workload)'." }
            & $wl.Connect $ctx
            $script:Connected[$item.workload] = $true
        }

        $policy = Get-PolicyJson -Path $item.path
        $entry.identity = $policy.identity
        $entry.action   = & $wl.Apply $policy $ctx     # create-or-update, idempotent
        $entry.status   = 'ok'
    }
    catch {
        $entry.message = $_.Exception.Message
        Write-Error "[$($item.path)] $($_.Exception.Message)"
    }
    $results.Add([pscustomobject]$entry)
}

# ── Disconnect everything we connected ─────────────────────────────────────
foreach ($key in $script:Connected.Keys) {
    try { & $script:Workloads[$key].Disconnect } catch { }
}

# ── Emit results (front end reads this) ────────────────────────────────────
$results | ConvertTo-Json -Depth 6
