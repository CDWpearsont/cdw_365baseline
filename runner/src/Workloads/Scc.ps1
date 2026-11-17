#requires -Version 7.0
<#
  Workloads/Scc.ps1 — Security & Compliance (Purview): DLP, retention, sensitivity
  labels and label policies, alert policies, auditing.

  Connect-IPPSSession takes the consultant's delegated token for the compliance
  endpoint (https://ps.compliance.protection.outlook.com). Policies use the shared
  Get/Set/New pattern, e.g. type DlpCompliancePolicy with an optional
  DlpComplianceRule as "rule".
#>
Register-Workload -Key 'scc' -TokenKey 'scc' `
    -Connect {
        param($Ctx)
        Import-Module ExchangeOnlineManagement -ErrorAction Stop
        Connect-IPPSSession -AccessToken $Ctx.Tokens.scc -Organization $Ctx.TenantId -ShowBanner:$false
    } `
    -Apply {
        param($Policy, $Ctx)
        # Rules point back at their policy with -Policy; the file carries that value.
        Invoke-PolicyFile -Policy $Policy
    } `
    -Disconnect {
        try { Disconnect-ExchangeOnline -Confirm:$false | Out-Null } catch { }
    }
