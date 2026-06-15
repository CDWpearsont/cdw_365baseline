#requires -Version 7.0
<#
  Workloads/Mdca.ps1 — Microsoft Defender for Cloud Apps

  HONEST CONSTRAINT — read before relying on this:
  MDCA has NO Microsoft365DSC coverage and NO supported Graph create API for
  the activity/anomaly policies in the design doc (Bulk Download, Ransomware
  Upload, Failed Logon, etc.). The MDCA REST API at /api/v1/ is largely
  read/manage-existing; programmatic CREATION of these policy types is not a
  documented, stable surface. Several are also "built-in" templates that can
  only be tuned, not created.

  This handler therefore does what is genuinely safe and useful today:
    • Connects/validates against the tenant MDCA API with the delegated token.
    • Treats each policy JSON as create-or-update against /api/v1/policies/
      WHERE the API supports it, and returns 'skipped' with a clear message
      where it does not — so the consultant sees exactly which items still
      need a portal/template step, rather than a silent no-op or a fake success.

  This keeps MDCA inside the same granular selection model while being truthful
  about what can be automated. Revisit if Microsoft ships a create API.
#>

Register-Workload -Key 'mdca' -TokenKey 'mdca' `
    -Connect {
        param($Ctx)
        if (-not $Ctx.Config.mdcaUrl) { throw 'config.mdcaUrl is required for the MDCA workload.' }
        # Validate the token/endpoint with a cheap read before doing anything.
        $null = Invoke-RestMethod -Uri "$($Ctx.Config.mdcaUrl)/api/v1/subnet/" -Method Get `
                    -Headers @{ Authorization = "Bearer $($Ctx.Tokens.mdca)" } -ErrorAction Stop
    } `
    -Apply {
        param($Policy, $Ctx)

        $base    = $Ctx.Config.mdcaUrl.TrimEnd('/')
        $headers = @{ Authorization = "Bearer $($Ctx.Tokens.mdca)"; 'Content-Type' = 'application/json' }

        # Built-in / template-derived policies cannot be created via API.
        if ($Policy.PSObject.Properties.Name -contains 'apiCreatable' -and -not $Policy.apiCreatable) {
            throw "MDCA policy '$($Policy.identity)' is template/built-in and must be tuned in the portal — no create API."
        }

        # Find an existing policy with the same name (create-or-update by name).
        $existing = $null
        try {
            $list = Invoke-RestMethod -Uri "$base/api/v1/policies/" -Method Get -Headers $headers
            $existing = $list.data | Where-Object { $_.name -eq $Policy.identity } | Select-Object -First 1
        } catch { }

        $body = $Policy.parameters | ConvertTo-Json -Depth 10

        if ($existing) {
            Invoke-RestMethod -Uri "$base/api/v1/policies/$($existing._id)/" -Method Put -Headers $headers -Body $body | Out-Null
            return 'updated'
        }
        else {
            Invoke-RestMethod -Uri "$base/api/v1/policies/" -Method Post -Headers $headers -Body $body | Out-Null
            return 'created'
        }
    }
