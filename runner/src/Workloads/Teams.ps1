#requires -Version 7.0
<#
  Workloads/Teams.ps1 — Microsoft Teams admin settings: external access, guest
  access, meeting and messaging policies, app permission policies.

  Connect-MicrosoftTeams -AccessTokens takes two delegated tokens: Microsoft Graph and
  the Teams admin API (48ac35b8-9aa8-4d74-927d-1f4a14a0b239). Tenant-wide settings
  (CsTenantFederationConfiguration, CsTeamsClientConfiguration, ...) use
  "singleton": true; named policies (CsTeamsMeetingPolicy -Identity Global or a name)
  use the shared Get/Set/New pattern with -Identity.
#>
Register-Workload -Key 'teams' -TokenKey 'teams' `
    -Connect {
        param($Ctx)
        Import-Module MicrosoftTeams -ErrorAction Stop
        Connect-MicrosoftTeams -AccessTokens @($Ctx.Tokens.teamsGraph, $Ctx.Tokens.teams) | Out-Null
    } `
    -Apply {
        param($Policy, $Ctx)
        Invoke-PolicyFile -Policy $Policy -NameParam 'Identity'
    } `
    -Disconnect {
        try { Disconnect-MicrosoftTeams | Out-Null } catch { }
    }
