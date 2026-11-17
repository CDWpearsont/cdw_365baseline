// CDW 365 Baseline - PowerShell runner infrastructure
//
//   Deployer site --(consultant's ID token + per-workload delegated tokens)--> Function (broker)
//   Function --writes payload blob, starts execution--> Container Apps job (runner image)
//   Runner --reads + deletes payload, applies, writes results blob--> Storage
//   Deployer site --polls--> Function --reads results / execution status
//
// Nothing tenant-specific is stored: payload blobs are deleted by the runner as soon as
// they are read, and a lifecycle rule removes anything left behind after a day.

@description('Azure region for every resource.')
param location string = resourceGroup().location

@description('Short prefix for resource names (lowercase letters and digits).')
@maxLength(12)
param prefix string = 'cdw365run'

@description('Client ID of the deployer site app registration (multi-tenant). Broker callers must present an ID token issued to it.')
param spaClientId string

@description('Origins allowed to call the broker (the deployer site, production and test).')
param siteOrigins array

@description('Runner container image.')
param runnerImage string = 'ghcr.io/cdwpearsont/cdw365-runner:main'

@description('Most runner executions allowed at once, across all consultants.')
param maxRunning int = 5

var suffix = uniqueString(resourceGroup().id)
var storageName = take('${prefix}${suffix}', 24)

// ── Monitoring ───────────────────────────────────────────────────────
resource logs 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${prefix}-logs'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource insights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${prefix}-insights'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logs.id
    DisableLocalAuth: true
  }
}

// ── Storage: payloads, results, function package ─────────────────────
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageName
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
}

resource payloads 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'payloads'
}

resource results 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'results'
}

resource packages 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'function-package'
}

resource lifecycle 'Microsoft.Storage/storageAccounts/managementPolicies@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    policy: {
      rules: [
        {
          name: 'expire-runs'
          enabled: true
          type: 'Lifecycle'
          definition: {
            filters: {
              blobTypes: [ 'blockBlob' ]
              prefixMatch: [ 'payloads/', 'results/' ]
            }
            actions: {
              baseBlob: { delete: { daysAfterModificationGreaterThan: 1 } }
            }
          }
        }
      ]
    }
  }
}

// ── Runner: Container Apps job ───────────────────────────────────────
resource runnerIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${prefix}-runner-id'
  location: location
}

resource env 'Microsoft.App/managedEnvironments@2024-03-01' = {
  name: '${prefix}-env'
  location: location
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: logs.properties.customerId
        sharedKey: logs.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [
      { name: 'Consumption', workloadProfileType: 'Consumption' }
    ]
  }
}

var runnerEnv = [
  { name: 'RUNNER_STORAGE_URL', value: storage.properties.primaryEndpoints.blob }
  { name: 'AZURE_CLIENT_ID', value: runnerIdentity.properties.clientId }
]

resource job 'Microsoft.App/jobs@2024-03-01' = {
  name: '${prefix}-job'
  location: location
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${runnerIdentity.id}': {} }
  }
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      triggerType: 'Manual'
      replicaTimeout: 1800
      replicaRetryLimit: 0
      manualTriggerConfig: {
        parallelism: 1
        replicaCompletionCount: 1
      }
    }
    template: {
      containers: [
        {
          name: 'runner'
          image: runnerImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: runnerEnv
        }
      ]
    }
  }
}

// ── Broker: Function app (Flex Consumption, Python) ─────────────────
resource plan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: '${prefix}-plan'
  location: location
  kind: 'functionapp'
  sku: { name: 'FC1', tier: 'FlexConsumption' }
  properties: { reserved: true }
}

resource broker 'Microsoft.Web/sites@2024-04-01' = {
  name: '${prefix}-broker-${take(suffix, 6)}'
  location: location
  kind: 'functionapp,linux'
  identity: { type: 'SystemAssigned' }
  properties: {
    serverFarmId: plan.id
    httpsOnly: true
    functionAppConfig: {
      deployment: {
        storage: {
          type: 'blobContainer'
          value: '${storage.properties.primaryEndpoints.blob}function-package'
          authentication: { type: 'SystemAssignedIdentity' }
        }
      }
      scaleAndConcurrency: {
        maximumInstanceCount: 40
        instanceMemoryMB: 2048
      }
      runtime: { name: 'python', version: '3.11' }
    }
    siteConfig: {
      minTlsVersion: '1.2'
      cors: { allowedOrigins: siteOrigins }
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: storage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: insights.properties.ConnectionString }
        { name: 'APPLICATIONINSIGHTS_AUTHENTICATION_STRING', value: 'Authorization=AAD' }
        { name: 'SPA_CLIENT_ID', value: spaClientId }
        { name: 'RUNNER_JOB_ID', value: job.id }
        { name: 'RUNNER_IMAGE', value: runnerImage }
        { name: 'RUNNER_STORAGE_URL', value: storage.properties.primaryEndpoints.blob }
        { name: 'RUNNER_IDENTITY_CLIENT_ID', value: runnerIdentity.properties.clientId }
        { name: 'MAX_RUNNING', value: string(maxRunning) }
      ]
    }
  }
}

// ── Role assignments ─────────────────────────────────────────────────
var roles = {
  blobDataOwner: 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
  blobDataContributor: 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
  queueDataContributor: '974c5e8b-45b9-4653-ba55-5f855dd0fb88'
  tableDataContributor: '0a9a7e1f-b9d0-4cc4-a60d-0319b160aaa3'
  contributor: 'b24988ac-6180-42a0-ab88-20f7382dd24c'
  monitoringMetricsPublisher: '3913510d-42f4-4e42-8a64-420c390055eb'
}

resource runnerBlob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, runnerIdentity.id, roles.blobDataContributor)
  scope: storage
  properties: {
    principalId: runnerIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.blobDataContributor)
  }
}

resource brokerStorageRoles 'Microsoft.Authorization/roleAssignments@2022-04-01' = [for r in [ roles.blobDataOwner, roles.queueDataContributor, roles.tableDataContributor ]: {
  name: guid(storage.id, broker.id, r)
  scope: storage
  properties: {
    principalId: broker.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', r)
  }
}]

// The broker can start and read executions of this one job, nothing else.
resource brokerJob 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(job.id, broker.id, roles.contributor)
  scope: job
  properties: {
    principalId: broker.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.contributor)
  }
}

resource brokerInsights 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(insights.id, broker.id, roles.monitoringMetricsPublisher)
  scope: insights
  properties: {
    principalId: broker.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roles.monitoringMetricsPublisher)
  }
}

output brokerName string = broker.name
output brokerUrl string = 'https://${broker.properties.defaultHostName}/api'
output jobName string = job.name
output storageAccount string = storage.name
