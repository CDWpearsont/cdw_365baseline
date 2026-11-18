# Policy catalog schema

Three files, one build step.

| File | Owner | Purpose |
|---|---|---|
| `metadata/**/<policy>.json.meta.json` | AI enrichment workflow, reviewed in a PR | Overrides, enrichment, tags. One per policy. |
| `bundles.json` | Human | Building Block definitions. |
| `catalog.json` | Build script | Compiled output. The only file the SPA fetches. |

`catalog.json` is a build artefact. Never hand-edit it, and never store derived
attributes in a sidecar: the builder recomputes derivation on every run, so a
change to the rules propagates everywhere at once with no drift.

---

## Derivation

Every attribute is derived from the filename, the folder, or the policy body,
in that order. Nothing is derived from an LLM.

| Attribute | Primary source | Fallback |
|---|---|---|
| `workload` | Folder (`IntuneConfig` → Intune) | Body shape (`grantControls` → Entra) |
| `platform` | Filename token (`Win`, `MacOS`, `iOS`, `Android`) | `platforms` field in body |
| `policyType` | Filename token (`SC`, `ES`, `CP`, `CA`) | `templateReference` present → Endpoint Security |
| `framework` | Filename token (`CIS L1`, `CIS L2`) | none; null means CDW standard |
| `scope` | Filename token (`D`, `U`) | `settingDefinitionId` prefix `device_` / `user_` |
| `version` | Filename token (`v4_0_0`) | none |
| `area` / `subArea` | Remaining filename segments | `name` in body |

Tokens are matched by value, not position, so a filename missing its scope
segment still resolves everything else. Vocabulary lives at the top of
`tools/build_catalog.py` and is the only place to edit when a new platform,
policy type or framework appears.

### Confidence

- **high** — platform, policy type and scope all came from filename or folder.
- **medium** — one or more fell back to the body, or an override supplied it.
- **low** — something is still unresolved. The AI enrichment workflow proposes
  values for these (from the catalog's own vocabulary) in its pull request.

### Identity

`id` is a slug of workload, platform, policy type, framework, area, sub-area and
scope. **Version is deliberately excluded** so that a version bump does not break
curated bundle membership or saved engagement selections. Collisions are
reported as a `duplicateId` issue and disambiguated with the version suffix
rather than silently merged.

---

## Sidecar

```jsonc
{
  "schemaVersion": 1,
  "policyPath": "IntuneConfig/SettingsCatalog/....json",
  "overrides": { "area": "Defender Antivirus" },
  "tags": ["autopilot-mvp"],
  "enrichment": {
    "summary": "...",              // one-paragraph purpose, customer-facing
    "rationale": "...",            // why it matters; feeds the LLD
    "prerequisites": ["..."],
    "userImpact": "...",
    "licensing": "...",
    "generatedBy": "gpt-4o",
    "generatedAt": "2026-09-03T09:00:00+00:00",
    "sourceHash": "sha256:...",    // policy hash at generation time
    "reviewedBy": null,
    "reviewedAt": null,
    "locked": false,               // true = the AI never rewrites this block
    "taxonomyCheckedHash": "sha256:..."
  },
  "overrideSources": { "platform": "ai" },   // which overrides the AI proposed
  "notes": "free text, never shown to customers"
}
```

`sourceHash` is the staleness mechanism. It is the policy's `contentHash`: a hash
of what the policy configures, ignoring object ids, timestamps and the
version-stamped name, so a nightly re-export or a version bump in the name does
not mark the text stale. When the configuration changes the hash stops matching,
the catalog marks that enrichment `stale: true`, and the next enrichment run
redrafts it. (A `sourceHash` equal to the older whole-file `policyHash` is also
accepted.)

### AI enrichment workflow

`.github/workflows/enrich-policies.yml` runs daily (and on demand) and calls
`tools/enrich_policies.py`, which:

1. builds the catalog and finds policies whose enrichment is missing or stale,
   plus any with unresolved taxonomy;
2. sends each one's configuration, with Intune portal setting names, to Azure
   OpenAI and writes `summary`, `rationale`, `userImpact`, `prerequisites` and
   `licensing` into the sidecar (customer-facing, British English, no customer
   or internal names; checked and retried once if it slips);
3. proposes overrides only for unresolved attributes, only from the catalog's
   vocabulary;
4. suggests Building Blocks for policies in no bundle, in the PR description
   only — `bundles.json` is never edited;
5. opens one pull request (`ai-enrichment/<base>`). The next run waits until it
   is merged or closed.

Review the PR, edit any sidecar text in it, and merge. To stop the AI touching a
sidecar you edited, set `"locked": true` in its `enrichment`; if that policy
later changes, the PR lists it under "Locked but out of date".

The LLD uses, per policy: `manifest.overlay.json` `lldSummary` if present, else
this enrichment's `summary` + `rationale`, else an AI rationale at export time.

Secrets: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT`.

`overrides` accepts any derived attribute key and always wins over derivation.
Edit it in the sidecar (by hand or in the enrichment PR) to correct a derived
value.

---

## Bundles

```jsonc
{
  "id": "intune-win-cis-l1",
  "name": "Windows - CIS Level 1",
  "description": "...",
  "path": ["Intune", "Windows", "Security Baselines", "CIS L1"],
  "rule": { "workload": "Intune", "platform": "Windows", "framework": "CIS L1" },
  "include": [],
  "exclude": [],
  "order": 10
}
```

Membership is `(rule matches ∪ include) − exclude`.

- **Rule bundles** compute membership from derived attributes. Drop forty new
  CIS L1 files into the repo and they join on the next build with no edit here.
  This is the zero-touch path.
- **Curated bundles** set `rule: null` and list members explicitly. Use for
  opinionated sets such as Autopilot MVP, where "minimum viable" is a consulting
  judgement rather than an attribute.
- A bundle can be both: a rule plus pinned additions and exclusions.

Rule semantics are AND across keys, OR within a key. A value of `null` matches
policies where that attribute is absent, which is how the CDW Standard bundle
captures everything not tagged to an external framework. `tags` matches on
intersection with the sidecar's tag list.

`path` drives the Building Blocks wizard drill-down. Keep the arrays consistent
so the tree renders sensibly.

---

## catalog.json

```jsonc
{
  "schemaVersion": 1,
  "generatedAt": "...",
  "sourceCommit": "<GITHUB_SHA>",
  "counts": { "policies": 139, "bundles": 7, "issues": 3, "byConfidence": {...} },
  "taxonomy": {
    "workloads": [...], "platforms": [...], "policyTypes": [...],
    "frameworks": [...], "scopes": [...], "areas": [...]
  },
  "policies": [ { /* derived + sidecar + resolved bundles */ } ],
  "bundles":  [ { /* definition + members + memberCount */ } ],
  "issues":   [ { "policyId", "path", "type", "detail" } ]
}
```

`taxonomy` is pre-computed so consumers (the site, the enrichment script) can
read the vocabulary without scanning the policy array.

### Issue types

| Type | Meaning | Action |
|---|---|---|
| `unresolved` | An attribute could not be derived | Set an override |
| `duplicateId` | Two policies derived the same identity | Rename one file |
| `orphan` | Policy is in no bundle | Add a rule or accept it |
| `emptyBundle` | Bundle matched nothing | Fix the rule |
| `danglingBundleMember` | Curated bundle names a policy that no longer exists | Prune it |
| `missingEnrichment` | No enrichment authored | Run the enrichment job |
| `staleEnrichment` | Policy changed after enrichment was generated | Regenerate |
| `unreadable` | Not valid JSON | Fix the file |

Use `--fail-on` in CI to make the structural ones blocking. `duplicateId`,
`unreadable` and `danglingBundleMember` are worth gating on. The enrichment ones
are not, or every new policy would break the build.

---

## Running it

```powershell
python tools\build_catalog.py --root . --policy-dirs IntuneConfig EntraConfig --report
python tools\build_catalog.py --root . --policy-dirs IntuneConfig EntraConfig --out catalog.json
python tools\build_catalog.py --root . --policy-dirs IntuneConfig EntraConfig --out catalog.json --fail-on duplicateId unreadable danglingBundleMember
```
