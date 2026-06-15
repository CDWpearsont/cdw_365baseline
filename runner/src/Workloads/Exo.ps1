#requires -Version 7.0
<#
  Workloads/Exo.ps1 — Exchange Online + Defender for Office 365

  Generic over policy TYPE. One handler covers every EXO/MDO object that
  follows the New-/Set-/Get-<Type> + -Name/-Identity convention:
    SafeLinksPolicy, SafeAttachmentPolicy, AntiPhishPolicy,
    MalwareFilterPolicy, HostedContentFilterPolicy, ...
  Adding a new MDO policy type needs NO code here — just a new JSON file.

  Idempotency: Get-<Type> by identity -> Set if it exists, New if it doesn't.
  Only the named object is touched. Untouched policies stay exactly as-is.

  Most MDO policies are inert without a matching *Rule* (recipient scoping).
  An optional "rule" block in the JSON is applied the same create-or-update way.
#>

Register-Workload -Key 'exo' -TokenKey 'exo' `
    -Connect {
        param($Ctx)
        Import-Module ExchangeOnlineManagement -ErrorAction Stop
        # Delegated token pass-through — EXO V3 supports -AccessToken.
        # The consultant's interactive sign-in produced this token in the browser;
        # nothing is stored on the runner.
        Connect-ExchangeOnline -AccessToken $Ctx.Tokens.exo `
                               -Organization $Ctx.TenantId `
                               -ShowBanner:$false -CommandName @(
                                   'Get-SafeLinksPolicy','New-SafeLinksPolicy','Set-SafeLinksPolicy',
                                   'Get-SafeLinksRule','New-SafeLinksRule','Set-SafeLinksRule',
                                   'Get-SafeAttachmentPolicy','New-SafeAttachmentPolicy','Set-SafeAttachmentPolicy',
                                   'Get-AntiPhishPolicy','New-AntiPhishPolicy','Set-AntiPhishPolicy',
                                   'Get-MalwareFilterPolicy','New-MalwareFilterPolicy','Set-MalwareFilterPolicy',
                                   'Get-HostedContentFilterPolicy','New-HostedContentFilterPolicy','Set-HostedContentFilterPolicy',
                                   'Get-SafeAttachmentRule','New-SafeAttachmentRule','Set-SafeAttachmentRule',
                                   'Get-AntiPhishRule','New-AntiPhishRule','Set-AntiPhishRule'
                               )
    } `
    -Apply {
        param($Policy, $Ctx)

        function Set-ExoObject {
            param([string]$Type, [string]$Identity, [object]$Parameters, [string]$NameParam = 'Name')
            $p = @{}
            if ($Parameters) { $Parameters.PSObject.Properties | ForEach-Object { $p[$_.Name] = $_.Value } }

            $existing = & "Get-$Type" -Identity $Identity -ErrorAction SilentlyContinue
            if ($existing) {
                & "Set-$Type" -Identity $Identity @p -Confirm:$false | Out-Null
                return 'updated'
            }
            else {
                $p[$NameParam] = $Identity
                & "New-$Type" @p -Confirm:$false | Out-Null
                return 'created'
            }
        }

        # 1) the policy
        $action = Set-ExoObject -Type $Policy.type -Identity $Policy.identity -Parameters $Policy.parameters

        # 2) the rule, if present (scopes the policy to recipients)
        if ($Policy.PSObject.Properties.Name -contains 'rule' -and $Policy.rule) {
            Set-ExoObject -Type $Policy.rule.type -Identity $Policy.rule.identity -Parameters $Policy.rule.parameters | Out-Null
        }
        return $action
    } `
    -Disconnect {
        try { Disconnect-ExchangeOnline -Confirm:$false | Out-Null } catch { }
    }
