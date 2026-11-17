#requires -Version 7.0
<#
  Workloads/Common.ps1 — shared create-or-update helper for cmdlet-based workloads.

  Policy files follow one shape, whatever the module:
    { "workload": "...", "type": "<Noun>", "identity": "<name>", "parameters": { ... },
      "rule": { "type": "<Noun>", "identity": "...", "parameters": { ... } } }   (rule optional)
  or, for tenant-wide settings that only have a Set- cmdlet:
    { "workload": "...", "type": "<Noun>", "singleton": true, "parameters": { ... } }

  Get-<type> -Identity finds an existing object; Set-<type> updates it, otherwise
  New-<type> creates it. Only the named object is touched.
#>

function ConvertTo-Splat {
    param([object]$Parameters)
    $p = @{}
    if ($Parameters) {
        $Parameters.PSObject.Properties | ForEach-Object {
            $v = $_.Value
            # JSON arrays arrive as object[]; cmdlets want string[] for most list parameters.
            if ($v -is [object[]] -and ($v | Where-Object { $_ -isnot [string] }).Count -eq 0) { $v = [string[]]$v }
            $p[$_.Name] = $v
        }
    }
    $p
}

function Set-CmdletObject {
    param(
        [Parameter(Mandatory)][string]$Type,
        [string]$Identity,
        [object]$Parameters,
        [string]$NameParam = 'Name',
        [switch]$Singleton,
        [string]$IdentityParam = 'Identity'
    )
    $p = ConvertTo-Splat $Parameters
    if ($Singleton) {
        & "Set-$Type" @p -ErrorAction Stop | Out-Null
        return 'updated'
    }
    $idp = @{ $IdentityParam = $Identity }
    $existing = & "Get-$Type" @idp -ErrorAction SilentlyContinue
    if ($existing) {
        & "Set-$Type" @idp @p -ErrorAction Stop | Out-Null
        return 'updated'
    }
    $p[$NameParam] = $Identity
    & "New-$Type" @p -ErrorAction Stop | Out-Null
    return 'created'
}

function Invoke-PolicyFile {
    param([Parameter(Mandatory)][object]$Policy, [string]$NameParam = 'Name', [string]$IdentityParam = 'Identity')
    $isSingleton = ($Policy.PSObject.Properties.Name -contains 'singleton') -and $Policy.singleton
    $identity = if ($Policy.PSObject.Properties.Name -contains 'identity') { $Policy.identity } else { $null }
    $action = Set-CmdletObject -Type $Policy.type -Identity $identity -Parameters $Policy.parameters `
        -NameParam $NameParam -IdentityParam $IdentityParam -Singleton:$isSingleton
    if (($Policy.PSObject.Properties.Name -contains 'rule') -and $Policy.rule) {
        Set-CmdletObject -Type $Policy.rule.type -Identity $Policy.rule.identity -Parameters $Policy.rule.parameters `
            -NameParam $NameParam -IdentityParam $IdentityParam | Out-Null
    }
    $action
}
