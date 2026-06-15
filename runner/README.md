# CDW 365 Baseline — PowerShell Runner

Granular, container-based apply runner for the M365 workloads that have no
Microsoft Graph create API (Exchange Online, Defender for Office 365,
Security & Compliance, SharePoint, Teams, Defender for Cloud Apps).

Runs as an **Azure Container Apps job** — one ephemeral container per run,
torn down afterwards, no state or credentials carried between customer
engagements.

## How granularity works

The front end sends an explicit list of selected policies. The runner:

1. Fetches **only** those JSON files from GitHub (cache-busted, same source of
   truth as the Graph deployer).
2. Applies each as **create-or-update by object name**.
3. Never reads or modifies anything not in the selection — existing tenant
   config is left intact.
4. Reports per-policy `ok` / `failed` with an action of `created` / `updated` /
   `skipped`, so the deploy log mirrors the Graph deployer's
   Deployed / Skipped / Failed.

A consultant can select one Defender for Cloud Apps policy, or every EXO
policy, or any mix — the unit of selection is the individual JSON file.

## Payload (base64 JSON in `RUNNER_PAYLOAD`)

```json
{
  "tenantId": "contoso.onmicrosoft.com",
  "config":   { "mdcaUrl": "https://contoso.eu2.portal.cloudappsecurity.com" },
  "tokens": {
    "exo":  "<token for https://outlook.office365.com/.default>",
    "scc":  "<token for https://ps.compliance.protection.outlook.com/.default>",
    "spo":  "<token for https://contoso.sharepoint.com/.default>",
    "mdca": "<token for the Defender for Cloud Apps API>"
  },
  "selected": [
    { "workload": "exo",  "path": "ExchangeOnline/SafeLinks/Safe_Links_excluding_email.json" },
    { "workload": "mdca", "path": "DefenderCloudApps/Policies/Bulk_Download_Monitoring.json" }
  ]
}
```

### One token per resource — important

There is **no single token** that covers all these workloads. EXO, SharePoint,
Teams and MDCA each authenticate against a different resource, so the browser
must acquire a separately-scoped delegated token for each workload the
consultant selected and place it under the matching key in `tokens`.

## Adding a workload

1. `Install-Module` the module in the `Dockerfile`.
2. Add `src/Workloads/<Name>.ps1` that calls `Register-Workload` with a
   `Connect` and an idempotent `Apply` (create-or-update by name).
3. Add the policy JSON files to the repo.

No change to `Invoke-Runner.ps1` is needed.

## Known auth caveats (be aware, not yet solved here)

- **Security & Compliance** (`Connect-IPPSSession`) delegated-token support is
  weaker than EXO's; this may need an app-only certificate path.
- **Teams** (`Connect-MicrosoftTeams`) expects two tokens (Graph + Teams).
- **Microsoft365DSC** delegated `-AccessToken` support is partial and
  resource-specific; some resources only accept app-cert auth. The EXO example
  here uses native cmdlets with a delegated token precisely because that path
  works cleanly today.
- **Defender for Cloud Apps** has no create API for the doc's policy types —
  see the honesty note in `src/Workloads/Mdca.ps1`.
