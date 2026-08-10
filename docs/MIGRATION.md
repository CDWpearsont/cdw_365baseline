# SWA migration runbook — as built

The CDW 365 Baseline Deployer runs on Azure Static Web Apps (Standard) with an
Entra sign-in gate restricted to the CDW tenant. This records the final
configuration and the traps encountered getting there.

Completed 10 August 2026.

## Reference

| | |
|---|---|
| Static Web App | `cdw365baseline`, RG `cdw-baseline`, West Europe, Standard |
| Subscription | Visual Studio Enterprise MPN `b592ed28-fe88-4cdf-9098-cb5c73aa7614` |
| Production URL | `https://gray-moss-014b92b03.7.azurestaticapps.net` |
| Test URL | `https://gray-moss-014b92b03-test.westeurope.7.azurestaticapps.net` |
| CDW tenant | `de9231de-45f4-4325-ae07-8ae72052517e` |
| Hosting tenant | `9de7e408-a1b6-4747-a22e-616b584bbaaa` |
| Dev tenant (app registrations, policy source) | `41366425-7a25-414f-9554-f0928745d0cb` |
| Site Access app (SWA gate) | `1a5d3073-984c-4811-83f7-373949dd8aea` |
| Deploy app (MSAL / Graph) | `c89cd498-eba7-43de-9a70-2a59d6feb655` |

Both app registrations live in the **dev tenant**, not the hosting tenant. See
Risks.

## Repository layout

```
cdw_365baseline/
├── site/                          <- SWA app root (app_location: site)
│   ├── index.html
│   ├── auth-redirect.html         <- MSAL landing page, anonymous route
│   ├── staticwebapp.config.json   <- must sit in site/, not repo root
│   └── templates/LLD.docx
├── .github/workflows/swa-deploy.yml
├── IntuneConfig/ EntraConfig/ DefenderCloudApps/ ExchangeOnline/ SharePointConfig/
├── scripts/generate-manifest.py
├── docs/
└── manifest*.json
```

Branches: `main` -> production, `test` -> preview environment. `main` is
protected: pull request required, zero approvals, force pushes blocked.

## Site Access app registration

- Multi-tenant ("Multiple Entra ID tenants")
- **Allow only certain tenants** -> CDW tenant GUID
- Redirect URIs under the **Web** platform (not SPA):
  - `https://gray-moss-014b92b03.7.azurestaticapps.net/.auth/login/aad/callback`
  - `https://gray-moss-014b92b03-test.westeurope.7.azurestaticapps.net/.auth/login/aad/callback`
- Authentication -> Settings -> Implicit grant: **ID tokens ticked**
- Client secret, 24 months

## Deploy app registration

Redirect URIs under the **Single-page application** platform:

- `https://gray-moss-014b92b03.7.azurestaticapps.net/auth-redirect.html`
- `https://gray-moss-014b92b03-test.westeurope.7.azurestaticapps.net/auth-redirect.html`
- `http://localhost:4280/auth-redirect.html`

## App settings

```powershell
az staticwebapp appsettings set --name cdw365baseline --resource-group cdw-baseline `
  --setting-names AZURE_CLIENT_ID="1a5d3073-984c-4811-83f7-373949dd8aea" `
                  AZURE_CLIENT_SECRET="<secret>"
```

Verify with `appsettings list`, which returns real values — the `set` response
shows null and is not a useful check. Settings are shared across environments
and are visible only to the auth layer, never to client-side JavaScript.

## Workflow

Key points in `.github/workflows/swa-deploy.yml`:

- Triggers on `main` and `test`, filtered to `paths: site/**`, so policy and
  manifest commits do not redeploy the site
- Stamps `BUILD.env` and `BUILD.date` into `site/index.html` via `sed` before
  deploying. The pattern depends on the exact spacing in the `BUILD` block; a
  trailing `grep` prints the result so the log shows what was set
- `skip_app_build: true` — nothing to compile, upload `site/` as-is
- `deployment_environment` routes non-main branches to preview environments

## Traps

Five independent faults, each masking the next. All produced silent failures or
redirect loops rather than useful errors.

**1. `openIdIssuer` must be a tenant GUID.**
`https://login.microsoftonline.com/organizations/v2.0` fails: SWA validates the
`iss` claim in the returned token, and a token issued through `/organizations/`
carries the concrete tenant GUID, which never matches. Symptom: sign-in loop,
no `StaticWebAppsAuthCookie`, `/.auth/me` returns `clientPrincipal: null`.

Correct form: `https://login.microsoftonline.com/<tenant-guid>/v2.0`

**2. Implicit ID tokens must be enabled.**
SWA uses `response_type=code+id_token`. Without the grant, Entra rejects with
**AADSTS700054** — "response_type 'id_token' is not enabled for the
application". Only visible in the Entra sign-in logs.

**3. `clientIdSettingName` / `clientSecretSettingName` take setting NAMES.**
They must read `AZURE_CLIENT_ID` and `AZURE_CLIENT_SECRET`, not the values.
Putting values there makes SWA look for app settings with those names, find
nothing, and silently drop the provider — `/.auth/login/aad` returns 404.

Nothing in `staticwebapp.config.json` is secret; it ships in the deployment
artifact. A client secret was committed this way and had to be rotated.

**4. MSAL needs a dedicated anonymously-routed redirect page.**
SWA's auth cookie is `SameSite=Strict`, so the cross-site return from Entra
arrives with no cookie. The gate 401s, the 401 override 302s — and URL
fragments do not survive a 302, so `#code=` is destroyed before any script
runs. `handleRedirectPromise()` then finds nothing and fails silently.

Fix: land MSAL on `/auth-redirect.html`, routed `anonymous`, which consumes the
fragment then navigates to `/` — a same-site request that carries the cookie
normally. In `index.html`:

```javascript
redirectUri: window.location.origin + '/auth-redirect.html',
```

**5. Empty strings are falsy in GitHub Actions expressions.**
`${{ github.ref_name == 'main' && '' || github.ref_name }}` evaluates to
`main`, not the empty string meaning production — so `main` deployed to a
preview environment called "main" while production kept serving a stale build.
Use the inverted form:

```yaml
deployment_environment: ${{ github.ref_name != 'main' && github.ref_name || '' }}
```

**Diagnostic order for any future auth problem:** Entra sign-in logs first,
checking both the User and Service principal tabs. They named the cause in one
sentence after several rounds of inferring from redirect chains. Second port of
call is a HAR captured with "Preserve log" enabled, started from the app rather
than the login page.

## Routine tasks

**Promote test to production:** pull request from `test` to `main`, merge. The
build stamp flips to `prod` automatically.

**Update the LLD template:** replace `site/templates/LLD.docx`, commit, push.
Deploys in about two minutes.

**Local development:** `swa start site` serves the app with an auth emulator on
`http://localhost:4280`, already registered as a redirect URI. Avoids the
deploy cycle and the AdGuard sinkhole on `*.web.core.windows.net`.

**Rotate the client secret:** create a new one in the Site Access app
registration, update `AZURE_CLIENT_SECRET` via `appsettings set`, delete the
old secret.

## Outstanding

**Old blob site still live.** `https://cdw365baseline.z6.web.core.windows.net`
remains as rollback. Once production has been used in anger for a week:

```powershell
az storage blob service-properties update --account-name <acct> `
  --static-website false --auth-mode login
```

Then update the SharePoint documentation, change the AdGuard allow-list from
`*.web.core.windows.net` to `*.azurestaticapps.net`, and remove the old
redirect URI from the deploy app registration.

**AI function key is still in client-side JavaScript** in `index.html` and has
been publicly readable. `/api/` is free (no managed functions), so the Function
App can be linked as a backend:

```powershell
az staticwebapp backends link --name cdw365baseline --resource-group cdw-baseline `
  --backend-resource-id <function-app-resource-id> --backend-region northcentralus
```

`AI_FUNCTION_URL` then becomes `/api/generate-rationale`. **Rotate the key when
this is done.**

**Manifest generator folder names.** `scripts/generate-manifest.py` lists
`DefenderConfig/DefenderOffice`, `ExchangeConfig`, `TeamsConfig` and
`PurviewConfig`, but the repo has `DefenderCloudApps`, `ExchangeOnline` and
`SharePointConfig`. Unknown folders are skipped silently, so their contents are
absent from `manifest.json`.

**Delete the stray `main` preview environment** created by trap 5:

```powershell
az staticwebapp environment delete --name cdw365baseline --resource-group cdw-baseline `
  --environment-name main
```

## Risks

**Both app registrations live in a personal dev tenant.** If that tenant lapses
or becomes inaccessible, deploy mode breaks in every consented customer tenant
simultaneously, with no remediation short of a new registration and re-consent
everywhere. Registrations cannot be moved between tenants — it means new app
IDs and every customer consenting again, far cheaper now than after wider
rollout. Should be resolved before Building Blocks ships to the team.

**Allowed tenants is a Preview feature.** If withdrawn, the fallback is a roles
function filtering on the `tid` claim, with `allowedRoles: ["cdw"]` on the
wildcard route. Note that managed functions would preclude linking the Function
App as a backend — SWA supports one or the other, not both.

**Standard plan is required.** Downgrading to Free silently disables custom
authentication and the site becomes publicly accessible.

**Accounts in the hosting tenant can sign in** — an app registration's own
tenant is always implicitly allowed, in addition to the allowed-tenants list.
The tenant-pinned `openIdIssuer` is what actually restricts this to CDW.

## Eventual move to the CDW tenant

Re-register both apps in CDW, update `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`,
and switch the Site Access app to single-tenant — the multi-tenant and
allowed-tenants machinery becomes unnecessary. The site, repository and
deployment pipeline are unaffected.
