#Requires -Version 7.0
<#
.SYNOPSIS
    Validates CDW Baseline Settings Catalog / Endpoint Security policy JSONs against the
    connected tenant, and optionally fills missing Endpoint Security template references.

.DESCRIPTION
    For every policy JSON under -Path (compliance policies are skipped):
      * every settingDefinitionId must exist in the tenant's Settings Catalog
      * every choice value must be a valid option of its definition
      * Endpoint Security policies: every top-level setting must carry the correct
        settingInstanceTemplateReference for the policy's template.

    With -FixTemplateReferences, missing top-level template references (and the matching
    choice value template references) are looked up from the template and written back
    into the file. Template reference IDs are global, so resolving them against the
    reference tenant makes the file deployable to any tenant.

    Read-only against Graph. Only writes to local files, and only with -FixTemplateReferences.

.EXAMPLE
    Connect-MgGraph -TenantId <reference-tenant-id> -Scopes DeviceManagementConfiguration.Read.All
    .\Test-CdwPolicyJson.ps1 -Path .\IntuneConfig

.EXAMPLE
    .\Test-CdwPolicyJson.ps1 -Path .\IntuneConfig\EndpointSecurity -FixTemplateReferences
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string] $Path,
    [switch] $FixTemplateReferences
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Command Invoke-MgGraphRequest -ErrorAction SilentlyContinue)) {
    throw 'Microsoft.Graph.Authentication is required: Install-Module Microsoft.Graph.Authentication -Scope CurrentUser'
}
if (-not (Get-MgContext)) {
    throw 'Not connected. Run: Connect-MgGraph -TenantId <reference-tenant-id> -Scopes DeviceManagementConfiguration.Read.All'
}

$base      = 'https://graph.microsoft.com/beta/deviceManagement'
$defCache  = @{}
$tplCache  = @{}
$results   = [System.Collections.Generic.List[object]]::new()

function Get-Definition([string] $Id) {
    if ($defCache.ContainsKey($Id)) { return $defCache[$Id] }
    $d = $null
    try {
        $d = Invoke-MgGraphRequest -Method GET -OutputType PSObject `
            -Uri "$base/configurationSettings/$([uri]::EscapeDataString($Id))"
    } catch { $d = $null }
    $defCache[$Id] = $d
    return $d
}

function Get-TemplateMap([string] $TemplateId) {
    if ($tplCache.ContainsKey($TemplateId)) { return $tplCache[$TemplateId] }
    $map = @{}
    $uri = "$base/configurationPolicyTemplates('$TemplateId')/settingTemplates"
    while ($uri) {
        $page = Invoke-MgGraphRequest -Method GET -Uri $uri -OutputType PSObject
        foreach ($t in $page.value) {
            $sit = $t.settingInstanceTemplate
            if ($sit.settingDefinitionId) { $map[$sit.settingDefinitionId] = $sit }
        }
        $uri = $page.'@odata.nextLink'
    }
    $tplCache[$TemplateId] = $map
    return $map
}

function Test-Instance($Si, [System.Collections.Generic.List[string]] $Errors) {
    $id  = $Si.settingDefinitionId
    $def = Get-Definition $id
    if (-not $def) { $Errors.Add("Definition not found: $id"); return }

    if ($Si.choiceSettingValue) {
        $val = $Si.choiceSettingValue.value
        if ($def.options -and ($def.options.itemId -notcontains $val)) {
            $Errors.Add("Invalid option '$val' for $id")
        }
        foreach ($c in @($Si.choiceSettingValue.children)) { if ($c) { Test-Instance $c $Errors } }
    }
    foreach ($g in @($Si.groupSettingCollectionValue)) {
        if ($g) { foreach ($c in @($g.children)) { if ($c) { Test-Instance $c $Errors } } }
    }
}

$files = Get-ChildItem -Path $Path -Filter *.json -Recurse -File
foreach ($file in $files) {
    $json = Get-Content -Raw -Path $file.FullName | ConvertFrom-Json -Depth 64
    if (-not $json.settings) { continue }   # compliance / device configuration JSONs

    $errors = [System.Collections.Generic.List[string]]::new()
    $fixed  = 0
    $isEs   = [bool]$json.templateReference.templateId

    $tpl = $null
    if ($isEs) {
        try { $tpl = Get-TemplateMap $json.templateReference.templateId }
        catch { $errors.Add("Template $($json.templateReference.templateId) not readable: $($_.Exception.Message)") }
    }

    foreach ($s in $json.settings) {
        $si = $s.settingInstance
        Test-Instance $si $errors

        if ($isEs -and $tpl) {
            $sit = $tpl[$si.settingDefinitionId]
            if (-not $sit) {
                $errors.Add("Not part of template $($json.templateReference.templateFamily): $($si.settingDefinitionId)")
                continue
            }
            $have = $si.settingInstanceTemplateReference.settingInstanceTemplateId
            if (-not $have) {
                if ($FixTemplateReferences) {
                    $si | Add-Member -NotePropertyName settingInstanceTemplateReference -Force `
                        -NotePropertyValue ([ordered]@{ settingInstanceTemplateId = $sit.settingInstanceTemplateId })
                    $vt = $sit.choiceSettingValueTemplate.settingValueTemplateId
                    if ($vt -and $si.choiceSettingValue -and -not $si.choiceSettingValue.settingValueTemplateReference) {
                        $si.choiceSettingValue | Add-Member -NotePropertyName settingValueTemplateReference -Force `
                            -NotePropertyValue ([ordered]@{ settingValueTemplateId = $vt; useTemplateDefault = $false })
                    }
                    $fixed++
                } else {
                    $errors.Add("Missing template reference: $($si.settingDefinitionId) (run with -FixTemplateReferences)")
                }
            } elseif ($have -ne $sit.settingInstanceTemplateId) {
                $errors.Add("Template reference mismatch for $($si.settingDefinitionId): file $have, template $($sit.settingInstanceTemplateId)")
            }
        }
    }

    if ($fixed -gt 0) {
        $text = $json | ConvertTo-Json -Depth 64
        [System.IO.File]::WriteAllText($file.FullName, $text, [System.Text.UTF8Encoding]::new($false))
    }

    $results.Add([pscustomobject]@{
        File     = $file.Name
        Settings = @($json.settings).Count
        Fixed    = $fixed
        Errors   = $errors.Count
        Detail   = ($errors -join '; ')
    })
}

$results | Sort-Object Errors -Descending | Format-Table File, Settings, Fixed, Errors -AutoSize | Out-Host
$bad = $results | Where-Object Errors -gt 0
if ($bad) {
    Write-Host "`nIssues:" -ForegroundColor Yellow
    foreach ($r in $bad) { Write-Host "  $($r.File)`n    $($r.Detail -replace '; ', "`n    ")" }
    exit 1
}
Write-Host "`nAll $($results.Count) policy files validated against the tenant." -ForegroundColor Green
