#requires -Version 7.0
<#
.SYNOPSIS
  Deploys (or updates) the CDW 365 Baseline PowerShell runner: infrastructure from
  main.bicep, then the broker Function code.

.DESCRIPTION
  Needs the Azure CLI (az) signed in to the subscription that hosts the deployer site.
  Safe to re-run: the Bicep deployment is idempotent and the broker code is replaced.

.EXAMPLE
  ./runner/infra/Deploy-Runner.ps1 -SubscriptionId <guid>
#>
param(
    [Parameter(Mandatory)][string] $SubscriptionId,
    [string] $ResourceGroup = 'rg-cdw365-runner',
    [string] $Location      = 'uksouth',
    [string] $SpaClientId   = 'c89cd498-eba7-43de-9a70-2a59d6feb655',
    [string[]] $SiteOrigins = @(
        'https://gray-moss-014b92b03.7.azurestaticapps.net',
        'https://gray-moss-014b92b03-test.westeurope.7.azurestaticapps.net'
    ),
    [ValidateSet('main', 'test')][string] $ImageTag = 'main'
)
$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

az account set --subscription $SubscriptionId
if ($LASTEXITCODE) { throw 'az account set failed: run az login first.' }

az group create --name $ResourceGroup --location $Location --output none

$params = @{
    spaClientId = @{ value = $SpaClientId }
    siteOrigins = @{ value = $SiteOrigins }
    runnerImage = @{ value = "ghcr.io/cdwpearsont/cdw365-runner:$ImageTag" }
} | ConvertTo-Json -Depth 5 -Compress
$paramFile = New-TemporaryFile
@{ '$schema' = 'https://schema.management.azure.com/schemas/2019-04-01/deploymentParameters.json#'; contentVersion = '1.0.0.0'; parameters = ($params | ConvertFrom-Json) } |
    ConvertTo-Json -Depth 6 | Set-Content $paramFile

Write-Host 'Deploying infrastructure (3-5 minutes)...'
$out = az deployment group create --resource-group $ResourceGroup --template-file "$here/main.bicep" `
    --parameters "@$paramFile" --query properties.outputs --output json | ConvertFrom-Json
if ($LASTEXITCODE) { throw 'Infrastructure deployment failed.' }
Remove-Item $paramFile

Write-Host 'Deploying broker code...'
$zip = Join-Path ([IO.Path]::GetTempPath()) 'cdw365-broker.zip'
Remove-Item $zip -ErrorAction SilentlyContinue
Compress-Archive -Path "$here/../broker/*" -DestinationPath $zip
az functionapp deployment source config-zip --resource-group $ResourceGroup --name $out.brokerName.value `
    --src $zip --build-remote true --output none
if ($LASTEXITCODE) { throw 'Broker code deployment failed.' }

Write-Host ''
Write-Host "Broker URL: $($out.brokerUrl.value)" -ForegroundColor Green
Write-Host 'Send this URL to Claude (or set RUNNER_API in site/index.html).'
