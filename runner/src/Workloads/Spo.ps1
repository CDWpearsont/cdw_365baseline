#requires -Version 7.0
<#
  Workloads/Spo.ps1 — SharePoint admin settings Graph doesn't expose: Restricted
  SharePoint Search, Restricted Content Discovery, site lifecycle and sharing details.

  PnP.PowerShell connects to the tenant admin site with the consultant's delegated
  token for https://<tenant>-admin.sharepoint.com. Tenant settings are applied with
  Set-PnPTenant ("type": "PnPTenant", "singleton": true); other PnP cmdlets follow the
  shared pattern.
#>
Register-Workload -Key 'spo' -TokenKey 'spo' `
    -Connect {
        param($Ctx)
        Import-Module PnP.PowerShell -ErrorAction Stop
        $tenantName = ($Ctx.TenantId -split '\.')[0]
        Connect-PnPOnline -Url "https://$tenantName-admin.sharepoint.com" -AccessToken $Ctx.Tokens.spo
    } `
    -Apply {
        param($Policy, $Ctx)
        Invoke-PolicyFile -Policy $Policy
    } `
    -Disconnect {
        try { Disconnect-PnPOnline | Out-Null } catch { }
    }
