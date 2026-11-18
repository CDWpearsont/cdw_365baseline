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
  An optional "rule" block in the JSON is applied the same create-or-update way;
  "{{acceptedDomains}}" in a value becomes the tenant's accepted domains.

  Tenant-wide settings with only a Set- cmdlet (OrganizationConfig,
  TransportConfig, ExternalInOutlook, AtpPolicyForO365) use "singleton": true.
  Default objects (the default remote domain, sharing and OWA policies, the
  default outbound spam policy) are updated by identity.
#>

Register-Workload -Key 'exo' -TokenKey 'exo' `
    -Connect {
        param($Ctx)
        Import-Module ExchangeOnlineManagement -ErrorAction Stop
        # Delegated token pass-through — EXO V3 supports -AccessToken.
        # The consultant's interactive sign-in produced this token in the browser;
        # nothing is stored on the runner. Only the cmdlets the library uses are loaded.
        $nouns = @(
            'SafeLinksPolicy', 'SafeLinksRule', 'SafeAttachmentPolicy', 'SafeAttachmentRule',
            'AntiPhishPolicy', 'AntiPhishRule', 'MalwareFilterPolicy', 'MalwareFilterRule',
            'HostedContentFilterPolicy', 'HostedContentFilterRule', 'HostedOutboundSpamFilterPolicy',
            'AtpPolicyForO365', 'OrganizationConfig', 'TransportConfig', 'ExternalInOutlook',
            'RemoteDomain', 'SharingPolicy', 'OwaMailboxPolicy'
        )
        $commands = @('Get-AcceptedDomain') + ($nouns | ForEach-Object { "Get-$_", "New-$_", "Set-$_" })
        Connect-ExchangeOnline -AccessToken $Ctx.Tokens.exo `
                               -Organization $Ctx.TenantId `
                               -ShowBanner:$false -CommandName $commands
        $script:ExoAcceptedDomains = $null
    } `
    -Apply {
        param($Policy, $Ctx)

        # Tokens in parameter values: {{acceptedDomains}} -> every accepted domain
        # in the customer's tenant, so a rule applies to all their recipients.
        function Expand-ExoValue($Value) {
            if ($Value -is [System.Collections.IEnumerable] -and $Value -isnot [string]) {
                $out = foreach ($v in $Value) {
                    if ($v -eq '{{acceptedDomains}}') {
                        if (-not $script:ExoAcceptedDomains) {
                            $script:ExoAcceptedDomains = @(Get-AcceptedDomain | ForEach-Object { [string]$_.DomainName })
                        }
                        $script:ExoAcceptedDomains
                    } else { $v }
                }
                return [string[]]@($out)
            }
            return $Value
        }

        function ConvertTo-ExoSplat($Parameters) {
            $p = @{}
            if ($Parameters) { $Parameters.PSObject.Properties | ForEach-Object { $p[$_.Name] = Expand-ExoValue $_.Value } }
            $p
        }

        # The policy a rule points at can only be given when the rule is created.
        $ruleLinks = 'SafeLinksPolicy', 'SafeAttachmentPolicy', 'AntiPhishPolicy', 'MalwareFilterPolicy',
                     'HostedContentFilterPolicy', 'HostedOutboundSpamFilterPolicy'

        function Set-ExoObject {
            param([string]$Type, [string]$Identity, [object]$Parameters, [switch]$Singleton)
            $p = ConvertTo-ExoSplat $Parameters
            if ($Singleton) {
                & "Set-$Type" @p -Confirm:$false -ErrorAction Stop | Out-Null
                return 'updated'
            }
            $existing = & "Get-$Type" -Identity $Identity -ErrorAction SilentlyContinue
            if ($existing) {
                if ($Type -like '*Rule') { $ruleLinks | ForEach-Object { $p.Remove($_) } }
                & "Set-$Type" -Identity $Identity @p -Confirm:$false -ErrorAction Stop | Out-Null
                return 'updated'
            }
            $p['Name'] = $Identity
            & "New-$Type" @p -Confirm:$false -ErrorAction Stop | Out-Null
            return 'created'
        }

        $isSingleton = ($Policy.PSObject.Properties.Name -contains 'singleton') -and $Policy.singleton

        # 1) the policy (or tenant-wide setting)
        $action = Set-ExoObject -Type $Policy.type -Identity $Policy.identity -Parameters $Policy.parameters -Singleton:$isSingleton

        # 2) the rule, if present (scopes the policy to recipients)
        if ($Policy.PSObject.Properties.Name -contains 'rule' -and $Policy.rule) {
            Set-ExoObject -Type $Policy.rule.type -Identity $Policy.rule.identity -Parameters $Policy.rule.parameters | Out-Null
        }
        return $action
    } `
    -Disconnect {
        try { Disconnect-ExchangeOnline -Confirm:$false | Out-Null } catch { }
    }
