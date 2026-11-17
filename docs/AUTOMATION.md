# Policy automation

How new policy JSON gets from a pull request into the reference tenant, and how
new Settings Catalog features are spotted.

## Flow

1. **PR into `test`** with new or changed JSON under `IntuneConfig/` or `EntraConfig/`.
2. **Validate policy JSON** runs on the PR:
   - offline checks (`tools/check_policy_files.py`): name, file name, required fields, CA state
   - Settings Catalog / Endpoint Security files are checked against the reference tenant
     with `tools/Test-CdwPolicyJson.ps1` (read-only, backup app)
3. Merge the PR into `test`.
4. Run **Deploy to reference tenant** from the `test` branch (Actions → Deploy to reference tenant →
   Run workflow → branch `test`). It waits for approval on the `reference-tenant` environment, then
   creates every policy JSON that differs between `test` and `main`.
5. The deploy then runs **Intune Backup** / **Entra Backup** on `main`, so the tenant's own
   version of each new policy lands in `main` within minutes.

## Deploy rules

- **Create only.** A policy whose name already exists in the tenant is reported as `exists`
  and left alone. Ship changes as a new version (`v1.0` → `v1.1`) or edit in the tenant.
- **Conditional Access is always created `disabled`.**
- **EDR onboarding can't be templated**: the onboarding package from the Defender connector is tenant-specific, so create that policy in each tenant.
- **Named policies** (created once, skipped if the name exists): Settings Catalog, Endpoint Security,
  compliance, device configurations, app protection, app configuration (MAM), enrolment restrictions,
  enrolment status page, Autopilot profiles (Entra join), feature update profiles, Conditional Access,
  named locations.
- **Tenant-wide settings** (one object per tenant, applied whenever the file changes): authorization
  policy, cross-tenant access default, device registration policy, default app management policy,
  directory settings (by template), authentication methods, SharePoint/OneDrive tenant settings.
  These aren't exported back from the tenant: the repo file is the source of truth.
- Admin consent request policy isn't deployed automatically yet: its reviewers are tenant-specific.
- Names must contain `CDW Baseline` (Intune) or start with `CDW` (CA), and the file name must be
  the name with every character outside `A-Z a-z 0-9 -` replaced by `_`. That's what the export
  writes, so the file isn't duplicated.

## Building block checks

`tools/build_catalog.py` compares every Settings Catalog and Endpoint Security setting across
the library. Any two policies that configure one setting with different values are listed in
`catalog.json` under `conflicts`, and the build fails (`conflictingSettings`) if a building block,
a goal option or a scenario would deploy both. Exceptions:

- `"allowConflicts": true` on a block whose members are alternatives assigned to different groups
  (update rings).
- `"knownConflicts": [setting ids]` for settings reviewed and accepted.

The wizard's review step shows the same conflicts for whatever the consultant selected, plus the
`licensing`, `prerequisites` and `notes` declared on each block.

## Settings Catalog snapshot

**Settings Catalog snapshot** runs every Monday at 05:30 UTC (or on demand). It writes every
setting definition and policy template to the `settings-catalog` branch and opens a
`settings-catalog` issue listing new, changed and removed settings since the previous week.

## Setup

| Item | Where | Used by |
|---|---|---|
| `CLIENT_ID`, `CLIENT_SECRET`, `TENANT_ID` | Repo secrets (existing backup app, read-only) | Validate, snapshot, exports |
| `reference-tenant` environment | Settings → Environments; required reviewer; deployment branch `test` only | Deploy |
| `DEPLOY_CLIENT_ID` | Variable on the `reference-tenant` environment | Deploy |
| Federated credential | Deploy app → Certificates & secrets; entity type Environment, name `reference-tenant` | Deploy |

Deploy app permissions (Application, admin consented, reference tenant only):
`DeviceManagementConfiguration.ReadWrite.All`, `DeviceManagementApps.ReadWrite.All`,
`DeviceManagementServiceConfig.ReadWrite.All`, `Policy.ReadWrite.ConditionalAccess`, `Policy.Read.All`,
`Application.Read.All` (CA policies that include or exclude specific apps),
`Policy.ReadWrite.Authorization`, `Policy.ReadWrite.CrossTenantAccess`,
`Policy.ReadWrite.ApplicationConfiguration`, `Policy.ReadWrite.AuthenticationMethod`,
`Policy.ReadWrite.DeviceConfiguration`, `SharePointTenantSettings.ReadWrite.All`,
`Directory.ReadWrite.All` (directory settings).

The deployer site's app registration needs the same permissions as **delegated** permissions; the
site asks consultants to consent to them at sign-in.

If the federated sign-in fails, the run log prints the subject GitHub presented. The credential's
Subject identifier must match it exactly.
