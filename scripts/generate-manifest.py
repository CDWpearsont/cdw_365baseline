#!/usr/bin/env python3
"""
Generate manifest.base.json from the CDW 365 Baseline policy library.

Walks the policy folders, parses every JSON, and derives everything that can be
derived mechanically. Human judgement lives in manifest.overlay.json, which is
keyed by the same stable IDs and merged in to produce manifest.json.

Run:  python3 scripts/generate-manifest.py [--repo .] [--check]

--check exits non-zero if manifest.json is out of date (for PR validation).
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

SCHEMA_VERSION = 1

# ── Repo layout ────────────────────────────────────────────────
# Folders are DISCOVERED, not listed: any directory containing .json files is
# treated as a policy folder. This means new workloads and categories appear in
# the manifest automatically, without editing this script.
#
#   <TopLevelFolder>/<Category>/...   ->  workload = mapped from top level
#                                         category = remaining path segments
#
# Only the workload mapping below needs maintaining, and an unmapped top-level
# folder is reported as a warning rather than silently skipped.

SKIP_DIRS = {
    '.git', '.github', 'docs', 'scripts', 'runner', 'site',
    'node_modules', 'templates', '.vscode', 'swa-db-connections',
}

WORKLOADS = {
    'intuneconfig':      ('intune',      'graph'),
    'entraconfig':       ('entra',       'graph'),
    'defenderconfig':    ('defender',    'graph'),
    'defenderendpoint':  ('defender',    'graph'),
    'defendercloudapps': ('mdca',        'powershell'),
    'defenderoffice':    ('mdo',         'powershell'),
    'exchangeonline':    ('exchange',    'powershell'),
    'exchangeconfig':    ('exchange',    'powershell'),
    'sharepointconfig':  ('sharepoint',  'powershell'),
    'onedriveconfig':    ('onedrive',    'powershell'),
    'teamsconfig':       ('teams',       'powershell'),
    'purviewconfig':     ('purview',     'powershell'),
}


def classify(top):
    """Map a top-level folder to (workload, deployVia). None if unknown."""
    return WORKLOADS.get(top.lower().replace('-', '').replace('_', ''))


def discover(repo):
    """Yield (relpath, workload, category, deployVia) for every policy JSON."""
    unknown = set()
    for dirpath, dirnames, filenames in os.walk(repo):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith('.')]
        rel = os.path.relpath(dirpath, repo)
        if rel == '.':
            continue
        jsons = [f for f in filenames if f.lower().endswith('.json')]
        if not jsons:
            continue
        parts = rel.replace('\\', '/').split('/')
        hit = classify(parts[0])
        if not hit:
            unknown.add(parts[0])
            continue
        workload, deploy_via = hit
        category = '/'.join(parts[1:]) if len(parts) > 1 else parts[0]
        for fn in sorted(jsons):
            yield (os.path.join(rel, fn).replace('\\', '/'),
                   workload, category, deploy_via)
    for u in sorted(unknown):
        print('WARNING: unmapped top-level folder %r \u2014 add it to WORKLOADS '
              'in scripts/generate-manifest.py' % u)


PLATFORM_MAP = {
    "windows10": "windows", "windows81": "windows", "windows": "windows",
    "macos": "macos", "ios": "ios", "ipados": "ios",
    "android": "android", "androidenterprise": "android",
    "androidforwork": "android", "androidworkprofile": "android",
    "linux": "linux",
}

# Version suffix: "- v4.0.0", "- v3_1_1", "v1.0 (TEST)"
VERSION_RE = re.compile(
    r"[-\s_]+v(\d+(?:[._]\d+)*)\s*(?:\((?:TEST|DRAFT|PREVIEW)\))?\s*$", re.I
)
TEST_RE = re.compile(r"\((?:TEST|DRAFT|PREVIEW)\)", re.I)
CIS_RE = re.compile(r"\bCIS[\s_-]*L([12])\b", re.I)
# Older naming used a bare "(L2)" suffix rather than "CIS L2"
CIS_ALT_RE = re.compile(r"\(\s*L([12])\s*\)", re.I)
# Policies the library explicitly marks as not-for-assignment
NO_ASSIGN_RE = re.compile(r"DO\s*NOT\s*ASSIGN", re.I)
AUDIT_RE = re.compile(r"\(\s*Audit(?:\s*Mode)?\s*\)", re.I)
# Platform prefix on the display name, e.g. "Win - ", "MacOS - ", "iOS - "
NAME_PREFIX_RE = re.compile(r"^(Win|Windows|MacOS|iOS|iPadOS|Android|Linux)\s*-\s*", re.I)
PREFIX_PLATFORM = {
    "win": "windows", "windows": "windows", "macos": "macos",
    "ios": "ios", "ipados": "ios", "android": "android", "linux": "linux",
}
# Scope token: " - D - " (device) or " - U - " (user)
SCOPE_RE = re.compile(r"\s-\s([DU])\s-\s")
# Entra CA numbering: "CDW001: ..."
CA_NUM_RE = re.compile(r"^(CDW\d+)\s*:")


def slugify(text):
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"^-+|-+$", "", s)


def strip_version(name):
    return VERSION_RE.sub("", TEST_RE.sub("", name)).strip(" -_")


def derive_version(name, filename):
    for src in (name, filename):
        m = VERSION_RE.search(strip_ext(src).replace("_", "."))
        if m:
            return m.group(1).replace("_", ".")
    return None


def strip_ext(f):
    return f[:-5] if f.lower().endswith(".json") else f


def normalise_platforms(raw):
    out = []
    for part in re.split(r"[,\s]+", str(raw or "")):
        p = PLATFORM_MAP.get(part.strip().lower())
        if p and p not in out:
            out.append(p)
    return out


def derive_object_type(doc, category):
    """Determine the Graph object type from the JSON body, not the folder."""
    if "@odata.type" in doc:
        return str(doc["@odata.type"]).lstrip("#").split(".")[-1]
    if "conditions" in doc and "grantControls" in doc:
        return "conditionalAccessPolicy"
    if "membershipRule" in doc or ("groupTypes" in doc and "displayName" in doc):
        return "group"
    if "rule" in doc and "platform" in doc:
        return "assignmentFilter"
    tref = doc.get("templateReference") or {}
    fam = tref.get("templateFamily")
    if fam and fam != "none":
        return fam  # e.g. endpointSecurityAttackSurfaceReduction
    if "settings" in doc and "platforms" in doc:
        return "configurationPolicy"
    if "scheduledActionsForRule" in doc:
        return "deviceCompliancePolicy"
    return category[0].lower() + category[1:]


def count_settings(doc):
    s = doc.get("settings")
    if isinstance(s, list):
        return len(s)
    return None


def build_entry(repo, relpath, workload, category, deploy_via):
    full = os.path.join(repo, relpath)
    with open(full, "rb") as fh:
        raw = fh.read()
    sha = hashlib.sha256(raw).hexdigest()[:16]

    try:
        doc = json.loads(raw.decode("utf-8-sig"))
    except Exception as e:
        return None, "%s: invalid JSON (%s)" % (relpath, e)

    filename = os.path.basename(relpath)
    display = doc.get("name") or doc.get("displayName") or strip_ext(filename).replace("_", " ")
    display = str(display).strip()

    base_name = strip_version(display)

    # Platform: JSON field first, then the display-name prefix as a fallback.
    platforms = normalise_platforms(doc.get("platforms"))
    if not platforms:
        m = NAME_PREFIX_RE.match(display)
        if m:
            p = PREFIX_PLATFORM.get(m.group(1).lower())
            if p:
                platforms = [p]

    tier = None
    m = CIS_RE.search(display) or CIS_ALT_RE.search(display)
    if m:
        tier = "cis-l" + m.group(1)

    scope = None
    m = SCOPE_RE.search(display)
    if m:
        scope = "device" if m.group(1).upper() == "D" else "user"

    # ── Stable ID ────────────────────────────────────────────────────────
    # Deliberately excludes the version so that a v3.7 -> v4.0.0 bump keeps
    # the same ID and the overlay enrichment survives. Uses the display name
    # with the version stripped, NOT the filename, because filenames change
    # more often than policy identities do.
    ident = "%s.%s.%s" % (workload, slugify(category), slugify(base_name))

    entry = {
        "id": ident,
        "file": relpath,
        "sha": sha,
        "workload": workload,
        "category": category,
        "objectType": derive_object_type(doc, category),
        "displayName": display,
        "baseName": base_name,
        "description": (doc.get("description") or "").strip() or None,
        "platforms": platforms,
        "tier": tier,
        "scope": scope,
        "version": derive_version(display, filename),
        "settingCount": count_settings(doc),
        "deployVia": deploy_via,
        "isDraft": bool(TEST_RE.search(display) or TEST_RE.search(filename)),
    }

    # ── Flags ────────────────────────────────────────────────────────────
    # Signals a consultant must not miss, lifted out of free text so the UI
    # can act on them rather than relying on someone reading the description.
    flags = []
    if NO_ASSIGN_RE.search(entry["description"] or ""):
        flags.append("do-not-assign")
    if AUDIT_RE.search(display):
        flags.append("audit-mode")
    if entry["isDraft"]:
        flags.append("draft")
    if entry.get("scope") is None and entry["objectType"] != "conditionalAccessPolicy":
        flags.append("scope-unknown")
    if flags:
        entry["flags"] = flags

    # Conditional Access extras — state matters a great deal for safety.
    if entry["objectType"] == "conditionalAccessPolicy":
        entry["caState"] = doc.get("state")
        m = CA_NUM_RE.match(display)
        if m:
            entry["caNumber"] = m.group(1)

    tref = doc.get("templateReference") or {}
    if tref.get("templateId"):
        entry["templateId"] = tref["templateId"]

    return {k: v for k, v in entry.items() if v is not None}, None


def scan(repo):
    entries, warnings = [], []
    for relpath, workload, category, deploy_via in discover(repo):
        entry, err = build_entry(repo, relpath, workload, category, deploy_via)
        if err:
            warnings.append(err)
        else:
            entries.append(entry)
    entries.sort(key=lambda e: e['id'])
    return entries, warnings


def check_duplicates(entries):
    seen, dupes = {}, []
    for e in entries:
        if e["id"] in seen:
            dupes.append((e["id"], seen[e["id"]], e["file"]))
        else:
            seen[e["id"]] = e["file"]
    return dupes


OVERLAY_FIELDS = [
    "blocks", "requires", "conflictsWith", "supersedes",
    "assignTo", "licence", "lldSection", "lldSummary", "notes",
]


def merge(base_entries, overlay):
    """Overlay wins for its own fields; base always wins for derived fields."""
    by_id = {o["id"]: o for o in overlay.get("entries", [])}
    merged, unenriched = [], []
    for e in base_entries:
        o = by_id.get(e["id"])
        row = dict(e)
        if o:
            for f in OVERLAY_FIELDS:
                if f in o and o[f] not in (None, "", [], {}):
                    row[f] = o[f]
        if not row.get("blocks"):
            unenriched.append(e["id"])
        merged.append(row)
    orphans = [i for i in by_id if i not in {e["id"] for e in base_entries}]
    return merged, unenriched, orphans


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--check", action="store_true",
                    help="exit 1 if the committed manifest is stale")
    args = ap.parse_args()

    entries, warnings = scan(args.repo)
    if not entries:
        print("ERROR: no policy JSONs found under %s" % os.path.abspath(args.repo))
        return 1

    dupes = check_duplicates(entries)
    for did, a, b in dupes:
        print("ERROR: duplicate id %s\n         %s\n         %s" % (did, a, b))

    base = {
        "schemaVersion": SCHEMA_VERSION,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "count": len(entries),
        "entries": entries,
    }

    overlay_path = os.path.join(args.repo, "manifest.overlay.json")
    overlay = {"entries": []}
    if os.path.exists(overlay_path):
        with open(overlay_path, encoding="utf-8") as fh:
            overlay = json.load(fh)

    merged, unenriched, orphans = merge(entries, overlay)
    full = {
        "schemaVersion": SCHEMA_VERSION,
        "generated": base["generated"],
        "count": len(merged),
        "enrichedCount": len(merged) - len(unenriched),
        "entries": merged,
    }

    def write(path, obj, stable_key="generated"):
        """Write, but treat a generated-timestamp-only diff as no change."""
        new = json.dumps(obj, indent=2, ensure_ascii=False, sort_keys=False)
        p = os.path.join(args.repo, path)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as fh:
                old = fh.read()
            try:
                a, b = json.loads(old), json.loads(new)
                a.pop(stable_key, None); b.pop(stable_key, None)
                if a == b:
                    return False
            except Exception:
                pass
        if not args.check:
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(new + "\n")
        return True

    changed_base = write("manifest.base.json", base)
    changed_full = write("manifest.json", full)

    print("Scanned  : %d objects across %d workloads"
          % (len(entries), len({e['workload'] for e in entries})))
    print("Enriched : %d / %d  (%d awaiting overlay)"
          % (full["enrichedCount"], len(merged), len(unenriched)))
    if warnings:
        print("Warnings :")
        for w in warnings:
            print("   -", w)
    if orphans:
        print("Orphaned overlay entries (id no longer in library):")
        for o in orphans:
            print("   -", o)

    if dupes:
        return 1
    if args.check and (changed_base or changed_full):
        print("\nERROR: manifest is out of date. Run scripts/generate-manifest.py "
              "and commit the result.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
