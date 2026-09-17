#Requires -Version 7.0
<#
.SYNOPSIS
    Exports every Endpoint Security template in the tenant and the settings each one contains.

.DESCRIPTION
    Read-only. Makes no changes to the tenant and writes one CSV locally.

    For each configuration policy template (all versions, not just the ones our policies
    pin), it lists every setting the template exposes, including child settings nested
    under choice options and group collections.

    Use it to answer "can this setting live in an Endpoint Security policy, and if so
    which template and which version?" rather than inferring it from existing policies.

.EXAMPLE
    Connect-MgGraph -TenantId <reference-tenant-id> -Scopes DeviceManagementConfiguration.Read.All
    .\Export-CdwEsTemplates.ps1

.EXAMPLE
    .\Export-CdwEsTemplates.ps1 -FamilyFilter endpointSecurity -OutFile es-templates.csv
#>
[CmdletBinding()]
param(
    [string] $OutFile      = 'es-template-settings.csv',
    [string] $FamilyFilter = 'endpointSecurity'   # '' for every family, including baselines
)

$ErrorActionPreference = 'Stop'
if (-not (Get-Command Invoke-MgGraphRequest -ErrorAction SilentlyContinue)) {
    throw 'Microsoft.Graph.Authentication is required: Install-Module Microsoft.Graph.Authentication -Scope CurrentUser'
}
if (-not (Get-MgContext)) {
    throw 'Not connected. Run: Connect-MgGraph -TenantId <reference-tenant-id> -Scopes DeviceManagementConfiguration.Read.All'
}

$base = 'https://graph.microsoft.com/beta/deviceManagement'
$rows = [System.Collections.Generic.List[object]]::new()

function Get-GraphAll([string] $Uri) {
    $items = [System.Collections.Generic.List[object]]::new()
    while ($Uri) {
        $page = Invoke-MgGraphRequest -Method GET -Uri $Uri -OutputType PSObject
        foreach ($v in @($page.value)) { if ($v) { $items.Add($v) } }
        $Uri = $page.'@odata.nextLink'
    }
    return $items
}

# A setting template nests children under choice options and group collections, so walk it
# rather than reading only the top-level settingDefinitionId.
function Add-SettingTemplate($Node, $Template, $Depth, $ParentId) {
    if (-not $Node) { return }
    $id = $Node.settingDefinitionId
    if ($id) {
        $rows.Add([pscustomobject]@{
            TemplateId          = $Template.id
            TemplateFamily      = $Template.templateFamily
            TemplateName        = $Template.displayName
            TemplateVersion     = $Template.versionInfo
            SettingDefinitionId = $id
            InstanceTemplateId  = $Node.settingInstanceTemplateId
            Depth               = $Depth
            ParentSettingId     = $ParentId
        })
    }

    foreach ($v in @($Node.choiceSettingValueTemplate, $Node.simpleSettingValueTemplate)) {
        foreach ($c in @($v.children)) { Add-SettingTemplate $c $Template ($Depth + 1) $id }
    }
    foreach ($v in @($Node.choiceSettingCollectionValueTemplate, $Node.simpleSettingCollectionValueTemplate)) {
        foreach ($c in @($v.children)) { Add-SettingTemplate $c $Template ($Depth + 1) $id }
    }
    foreach ($g in @($Node.groupSettingCollectionValueTemplate)) {
        foreach ($c in @($g.children)) { Add-SettingTemplate $c $Template ($Depth + 1) $id }
    }
    foreach ($c in @($Node.children)) { Add-SettingTemplate $c $Template ($Depth + 1) $id }
}

Write-Host 'Reading configuration policy templates...' -ForegroundColor Cyan
$templates = Get-GraphAll "$base/configurationPolicyTemplates"
if ($FamilyFilter) {
    $templates = @($templates | Where-Object { $_.templateFamily -like "$FamilyFilter*" })
}
Write-Host "  $($templates.Count) templates to read"

$i = 0
foreach ($t in $templates) {
    $i++
    Write-Progress -Activity 'Reading setting templates' -Status "$($t.displayName) ($($t.templateFamily))" `
        -PercentComplete ([int](100 * $i / [Math]::Max($templates.Count, 1)))
    try {
        $settingTemplates = Get-GraphAll "$base/configurationPolicyTemplates('$($t.id)')/settingTemplates"
    } catch {
        Write-Warning "  $($t.id): $($_.Exception.Message)"
        continue
    }
    foreach ($st in $settingTemplates) {
        Add-SettingTemplate $st.settingInstanceTemplate $t 0 $null
    }
}
Write-Progress -Activity 'Reading setting templates' -Completed

$rows | Sort-Object TemplateFamily, TemplateName, TemplateVersion, SettingDefinitionId |
    Export-Csv -Path $OutFile -NoTypeInformation -Encoding utf8

Write-Host "`nWrote $OutFile — $($rows.Count) settings across $($templates.Count) templates." -ForegroundColor Green
$rows | Group-Object TemplateFamily, TemplateName, TemplateVersion |
    Sort-Object Name |
    Select-Object @{ n = 'Template'; e = { $_.Name } }, @{ n = 'Settings'; e = { $_.Count } } |
    Format-Table -AutoSize | Out-String -Width 250 | Write-Host
