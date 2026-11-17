#requires -Version 7.0
<#
  Invoke-Runner.ps1  —  CDW 365 Baseline PowerShell Runner (entry point)

  Granular by design:
    • The front end sends an explicit list of selected policies.
    • The runner fetches ONLY those JSON files from GitHub (source of truth).
    • Every apply is create-or-update BY OBJECT NAME — existing tenant
      configuration that isn't in the selection is never read or modified.
    • One policy failing does not abort the batch.

  Payload contract (blob payloads/<RUN_ID>.json in Azure, or base64 JSON in $env:RUNNER_PAYLOAD locally):
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
# In Azure the broker leaves the payload in blob storage and passes RUN_ID; the runner
# reads it with its managed identity and deletes it at once (it holds delegated tokens).
# Locally, pass the base64 JSON in RUNNER_PAYLOAD instead.
function Get-StorageToken {
    $uri = "$($env:IDENTITY_ENDPOINT)?resource=https://storage.azure.com/&api-version=2019-08-01&client_id=$($env:AZURE_CLIENT_ID)"
    (Invoke-RestMethod -Uri $uri -Headers @{ 'X-IDENTITY-HEADER' = $env:IDENTITY_HEADER }).access_token
}
function Invoke-Blob {
    param([string]$Method, [string]$Path, [string]$Body)
    $headers = @{ Authorization = "Bearer $(Get-StorageToken)"; 'x-ms-version' = '2023-11-03' }
    $params = @{ Method = $Method; Uri = "$($env:RUNNER_STORAGE_URL.TrimEnd('/'))/$Path"; Headers = $headers }
    if ($Method -eq 'PUT') {
        $headers['x-ms-blob-type'] = 'BlockBlob'
        $params.Body = [Text.Encoding]::UTF8.GetBytes($Body)
        $params.ContentType = 'application/json'
    }
    Invoke-RestMethod @params
}

if ($env:RUN_ID) {
    $raw = Invoke-Blob -Method GET -Path "payloads/$($env:RUN_ID).json"
    Invoke-Blob -Method DELETE -Path "payloads/$($env:RUN_ID).json" | Out-Null
    $payload = if ($raw -is [string]) { $raw | ConvertFrom-Json } else { $raw }
}
elseif ($env:RUNNER_PAYLOAD) {
    $payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:RUNNER_PAYLOAD)) | ConvertFrom-Json
}
else { throw 'No payload: set RUN_ID (Azure) or RUNNER_PAYLOAD (local).' }

# The site deploys from the branch it reads (main for prod, test for the test site).
if (($payload.PSObject.Properties.Name -contains 'branch') -and ($payload.branch -in @('main', 'test'))) {
    $RawBase = "https://raw.githubusercontent.com/$RepoOwner/$RepoName/$($payload.branch)"
}

function Get-Prop($Object, [string]$Name, $Default = $null) {
    if ($Object -and $Object.PSObject.Properties.Name -contains $Name) { $Object.$Name } else { $Default }
}
$ctx = [pscustomobject]@{
    TenantId = $payload.tenantId
    Tokens   = Get-Prop $payload 'tokens' ([pscustomobject]@{})
    Config   = Get-Prop $payload 'config' ([pscustomobject]@{})
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
            $token = Get-Prop $ctx.Tokens $wl.TokenKey
            if (-not $token) { throw "No '$($wl.TokenKey)' token supplied for workload '$($item.workload)'." }
            & $wl.Connect $ctx
            $script:Connected[$item.workload] = $true
        }

        $policy = Get-PolicyJson -Path $item.path
        $entry.identity = Get-Prop $policy 'identity'
        $entry.action   = & $wl.Apply $policy $ctx     # create-or-update, idempotent
        $entry.status   = 'ok'
    }
    catch {
        $entry.message = $_.Exception.Message
        Write-Warning "[$($item.path)] $($_.Exception.Message)"
    }
    $results.Add([pscustomobject]$entry)
}

# ── Disconnect everything we connected ─────────────────────────────────────
foreach ($key in $script:Connected.Keys) {
    try { & $script:Workloads[$key].Disconnect } catch { }
}

# ── Emit results (front end reads this) ────────────────────────────────────
$json = ConvertTo-Json -InputObject @($results) -Depth 6
if ($env:RUN_ID) { Invoke-Blob -Method PUT -Path "results/$($env:RUN_ID).json" -Body $json | Out-Null }
$json
