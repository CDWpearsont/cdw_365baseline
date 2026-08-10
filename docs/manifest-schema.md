# Policy manifest

The manifest is the metadata layer over the policy library. It exists so that
the tool can reason about policies — group them into blocks, resolve
dependencies, check licence fit, write LLD prose — instead of guessing from
filenames.

## Files

| File | Written by | Committed | Purpose |
|---|---|---|---|
| `manifest.base.json` | GitHub Action | Yes | Everything derivable from the policy JSONs. Never hand-edit. |
| `manifest.overlay.json` | Consultants, via the Settings page | Yes | Human judgement only. Small. |
| `manifest.json` | GitHub Action | Yes | base + overlay merged. **This is what the front end fetches.** |

Editing `manifest.base.json` or `manifest.json` by hand is pointless — the next
policy commit overwrites them.

## Stable IDs

```
<workload>.<category-slug>.<display-name-slug-without-version>
```

e.g. `intune.endpointsecurity.win-cdw-baseline-es-cis-l1-attack-surface-reduction-d-asr-rules`

**The ID deliberately excludes the version.** A v3.7 → v4.0.0 bump keeps the
same ID, so overlay enrichment survives policy updates. This is the single most
important property of the scheme; without it every version bump would orphan its
metadata and the enrichment work would need redoing.

Two consequences worth understanding:

- **Renaming a policy changes its ID** and orphans its overlay entry. The
  generator reports orphans so they can be re-pointed rather than silently lost.
- **A genuinely new policy that replaces an old one** (e.g. the v4 CIS-named
  policies alongside the older v3 ones) gets a *different* ID, because the
  display name differs. That is correct — they are different objects. Use the
  overlay's `supersedes` field to express the relationship.

## Derived fields (base — do not hand-edit)

| Field | Source |
|---|---|
| `id` | workload + category + versionless name |
| `file` | path relative to repo root |
| `sha` | SHA-256 of file bytes, truncated. Used for design-package drift detection. |
| `workload` | folder |
| `category` | folder |
| `objectType` | `@odata.type`, `templateReference.templateFamily`, or body shape |
| `displayName` | `name` or `displayName` from the JSON |
| `baseName` | display name with version stripped |
| `description` | JSON body |
| `platforms` | `platforms` field, normalised; falls back to the name prefix (`Win - `, `MacOS - `) |
| `tier` | `CIS L1` / `CIS L2` in the name, or a bare `(L1)` / `(L2)` suffix |
| `scope` | the `- D -` / `- U -` token → `device` / `user` |
| `version` | trailing version in the name or filename |
| `settingCount` | length of `settings` |
| `deployVia` | `graph` or `powershell`, from the workload |
| `isDraft` | `(TEST)` / `(DRAFT)` / `(PREVIEW)` marker |
| `templateId` | `templateReference.templateId` |
| `caState`, `caNumber` | Conditional Access only |
| `flags` | see below |

### Flags

Signals lifted out of free text so the UI can act on them rather than relying on
someone reading a description field.

| Flag | Meaning |
|---|---|
| `do-not-assign` | Description says DO NOT ASSIGN. Must never be auto-selected by a block. |
| `audit-mode` | Policy is in audit rather than block mode. |
| `draft` | Marked TEST/DRAFT/PREVIEW. |
| `scope-unknown` | No `- D -` / `- U -` token; assignment target can't be inferred. |

## Overlay fields (human judgement)

| Field | Type | Purpose |
|---|---|---|
| `blocks` | string[] | Which Building Blocks include this. Empty = not in any block. |
| `requires` | string[] | IDs that must deploy first (groups, filters). |
| `conflictsWith` | string[] | Mutually exclusive policies. |
| `supersedes` | string[] | Older IDs this replaces. |
| `assignTo` | string | Default assignment target ID. |
| `licence` | string[] | `E3`, `E5`, `BP`, `EMS-E5`… Gates block availability. |
| `lldSection` | string | Section number in the LLD template. |
| `lldSummary` | string | British English prose for the design document. |
| `notes` | string | Internal only. Never appears in customer output. |

`lldSummary` is what removes the need for a per-policy AI call at export time.
Write it once, reuse it on every engagement.

## Workflow

```
policy JSON committed
        ↓
GitHub Action: generate-manifest.py
        ↓
manifest.base.json regenerated
        ↓
merged with manifest.overlay.json
        ↓
manifest.json committed
        ↓
front end fetches manifest.json
        ↓
unenriched entries surface in the Settings page
        ↓
AI drafts overlay values → consultant approves → overlay updated
```

Pull requests run with `--check`, which fails if the committed manifest is stale
or if two policies collide on a stable ID.

## Running locally

```powershell
python3 scripts/generate-manifest.py --repo .
python3 scripts/generate-manifest.py --repo . --check   # validate only
```

No dependencies beyond the Python standard library.
