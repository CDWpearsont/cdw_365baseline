#!/usr/bin/env python3
"""
CDW 365 Baseline - catalog builder.

Walks the policy store, derives taxonomy for every policy from its filename,
folder and body, merges any human/LLM-authored sidecar metadata, resolves
bundle membership, and emits a single catalog.json for the SPA to consume.

Derived attributes are recomputed on every run and are never persisted to
sidecars. Sidecars hold only what a human or the enrichment step authored:
overrides, enrichment text and free tags. This means a change to the
derivation rules takes effect everywhere immediately, with no drift.

Usage:
    python tools/build_catalog.py --root . --out catalog.json
    python tools/build_catalog.py --root . --report      # human-readable summary
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

SCHEMA_VERSION = 1

# --------------------------------------------------------------------------
# Vocabulary. Everything the deriver recognises lives here so it is the only
# place to edit when a new platform, policy type or framework is introduced.
# --------------------------------------------------------------------------

PLATFORM_TOKENS = {
    "Win": "Windows",
    "Windows": "Windows",
    "MacOS": "macOS",
    "macOS": "macOS",
    "iOS": "iOS",
    "Android": "Android",
}

# Values seen in the `platforms` field of a Settings Catalog / Endpoint
# Security policy body, used as a fallback when the filename is silent.
PLATFORM_BODY = {
    "windows10": "Windows",
    "windows10AndLater": "Windows",
    "macOS": "macOS",
    "iOS": "iOS",
    "android": "Android",
    "androidEnterprise": "Android",
}

POLICY_TYPE_TOKENS = {
    "SC": "Settings Catalog",
    "ES": "Endpoint Security",
    "CP": "Compliance Policy",
    "Compliance": "Compliance Policy",
    "CA": "Conditional Access",
    "MAM": "App Protection",
    "App Protection": "App Protection",
    "WUfB": "Update Ring",
    "Update Ring": "Update Ring",
    "Enrolment Restriction": "Enrolment Restriction",
    "Enrollment Restriction": "Enrolment Restriction",
    "DirectorySetting": "Directory Setting",
}

# Where a policy type has an unambiguous scope, the filename need not say so.
DEFAULT_SCOPE_BY_TYPE = {
    "Conditional Access": "User",
    "App Protection": "User",
    "Compliance Policy": "Device",
    "Update Ring": "Device",
    "Enrolment Restriction": "Device",
    "Directory Setting": "Tenant",
    "Tenant Policy": "Tenant",
}

# Body @odata.type fragment -> policy type. Checked case-insensitively.
ODATA_TYPE_HINTS = {
    "managedappprotection": "App Protection",
    "compliancepolicy": "Compliance Policy",
    "windowsupdateforbusiness": "Update Ring",
    "deviceenrollment": "Enrolment Restriction",
}

FRAMEWORK_TOKENS = {
    "CIS L1": "CIS L1",
    "CIS L2": "CIS L2",
    "CIS Level 1": "CIS L1",
    "CIS Level 2": "CIS L2",
}

SCOPE_TOKENS = {"D": "Device", "U": "User"}

# Noise segments that carry no taxonomy meaning.
IGNORED_TOKENS = {"CDW Baseline", "CDW", "Baseline", "TEST"}

VERSION_RE = re.compile(r"^v\s?(\d+(?:[._ ]\d+)*)$", re.IGNORECASE)
UNDERSCORE_NAME_RE = re.compile(r"^CDW Baseline (.+?) (v\d+(?:[. ]\d+)*)$", re.IGNORECASE)

# Folder (relative to repo root) -> workload. Longest prefix wins.
FOLDER_WORKLOAD = {
    "IntuneConfig": "Intune",
    "EntraConfig": "Entra",
    "DefenderConfig": "Defender",
    "ExchangeConfig": "Exchange Online",
    "SharePointConfig": "SharePoint",
    "TeamsConfig": "Teams",
    "PurviewConfig": "Purview",
}

# Conditional Access grant controls and conditions that make a policy
# device-centric rather than identity-centric.
CA_ENDPOINT_CONTROLS = {
    "compliantdevice", "domainjoineddevice", "compliantapplication",
    "approvedapplication", "unknownfuturevalue_compliantdevice",
}

# Sidecar tags that denote framework membership, folded into `frameworks`.
FRAMEWORK_TAGS = {
    "cis-l1": "CIS L1",
    "cis-l2": "CIS L2",
    "zero-trust": "Zero Trust",
}

# Areas that are written inconsistently across the store. Canonical form on
# the right. Extend as they surface; the report flags near-duplicates.
AREA_ALIASES = {
    "Delivery Optimization": "Delivery Optimisation",
    "Windows Update For Business": "Windows Update for Business",
    "Defender Antivirus": "Defender Antivirus",
    "Microsoft Defender Antivirus": "Defender Antivirus",
}


# --------------------------------------------------------------------------
# Filename tokenisation
# --------------------------------------------------------------------------

def split_segments(stem: str) -> list[str]:
    """Split a policy filename stem into taxonomy segments.

    Filenames use ' - ' as the separator; underscores in the on-disk name are
    substitutes for spaces. Trailing markers like '(TEST)' are stripped.
    """
    text = stem.replace("_", " ")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\(\s*TEST\s*\)\s*$", "", text, flags=re.IGNORECASE).strip()
    parts = [p.strip(" -") for p in text.split(" - ")]
    parts = [p for p in parts if p]
    # Entra tenant objects use underscores throughout rather than " - ", so the
    # split above yields a single blob. Re-split those on the known shape.
    if len(parts) == 1:
        m = UNDERSCORE_NAME_RE.match(parts[0])
        if m:
            head, version = m.group(1), m.group(2)
            head_parts = head.split(" ", 1)
            if head_parts[0] in POLICY_TYPE_TOKENS and len(head_parts) > 1:
                parts = ["CDW Baseline", head_parts[0], split_camel(head_parts[1]), version]
            else:
                parts = ["CDW Baseline", split_camel(head), version]
    return parts


def split_camel(text: str) -> str:
    """AdminConsentRequestPolicy -> Admin Consent Request Policy."""
    out = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    return re.sub(r"\s+", " ", out).strip()


def normalise_version(raw: str) -> str | None:
    m = VERSION_RE.match(raw.strip())
    if not m:
        return None
    return re.sub(r"[ _]", ".", m.group(1))


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower())
    return s.strip("-")


# --------------------------------------------------------------------------
# Body inspection
# --------------------------------------------------------------------------

def load_body(path: str) -> dict | None:
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def iter_setting_ids(node) -> list[str]:
    """Collect every settingDefinitionId in a policy body, including children."""
    found = []
    if isinstance(node, dict):
        val = node.get("settingDefinitionId")
        if isinstance(val, str):
            found.append(val)
        for v in node.values():
            found.extend(iter_setting_ids(v))
    elif isinstance(node, list):
        for v in node:
            found.extend(iter_setting_ids(v))
    return found


def detect_workload_from_body(body: dict) -> str | None:
    if "grantControls" in body or "sessionControls" in body:
        return "Entra"
    if "settings" in body and ("technologies" in body or "platforms" in body):
        return "Intune"
    if "scheduledActionsForRule" in body:
        return "Intune"
    return None


def detect_policy_type_from_body(body: dict) -> str | None:
    if "grantControls" in body:
        return "Conditional Access"
    odata = str(body.get("@odata.type", "")).lower()
    for fragment, ptype in ODATA_TYPE_HINTS.items():
        if fragment in odata:
            return ptype
    if "scheduledActionsForRule" in body:
        return "Compliance Policy"
    if body.get("deviceEnrollmentConfigurationType"):
        return "Enrolment Restriction"
    tmpl = body.get("templateReference") or {}
    if isinstance(tmpl, dict) and tmpl.get("templateId"):
        return "Endpoint Security"
    if "settings" in body:
        return "Settings Catalog"
    return None


def detect_scope_from_body(setting_ids: list[str]) -> str | None:
    prefixes = Counter(
        "User" if sid.startswith("user_") else "Device" if sid.startswith("device_") else None
        for sid in setting_ids
    )
    prefixes.pop(None, None)
    if not prefixes:
        return None
    return prefixes.most_common(1)[0][0]


def detect_security_domain(body: dict) -> str | None:
    """Endpoint vs Identity for a Conditional Access policy.

    Device posture, app protection and platform conditions make a policy
    endpoint-centric. Everything else is identity-centric.
    """
    if "grantControls" not in body and "conditions" not in body:
        return None
    grant = body.get("grantControls") or {}
    conditions = body.get("conditions") or {}
    controls = {str(c).lower() for c in (grant.get("builtInControls") or [])}
    if controls & CA_ENDPOINT_CONTROLS:
        return "Endpoint Security"
    if conditions.get("devices") or conditions.get("platforms"):
        return "Endpoint Security"
    return "Identity Security"


def file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


# --------------------------------------------------------------------------
# Derivation
# --------------------------------------------------------------------------

def workload_from_path(rel_path: str) -> str | None:
    parts = rel_path.replace("\\", "/").split("/")
    for part in parts:
        if part in FOLDER_WORKLOAD:
            return FOLDER_WORKLOAD[part]
    return None


def derive(rel_path: str, abs_path: str) -> dict:
    """Derive the full taxonomy for one policy file."""
    filename = os.path.basename(rel_path)
    stem = filename[:-5] if filename.lower().endswith(".json") else filename
    segments = split_segments(stem)
    body = load_body(abs_path) or {}
    setting_ids = iter_setting_ids(body)

    rec: dict = {
        "workload": None,
        "platform": None,
        "policyType": None,
        "framework": None,
        "scope": None,
        "version": None,
        "area": None,
        "subArea": None,
        "securityDomain": None,
    }
    source: dict[str, str] = {}
    leftovers: list[str] = []

    # Pass 1 - filename tokens.
    for seg in segments:
        if seg in IGNORED_TOKENS:
            continue
        if seg in PLATFORM_TOKENS and not rec["platform"]:
            rec["platform"] = PLATFORM_TOKENS[seg]
            source["platform"] = "filename"
        elif seg in POLICY_TYPE_TOKENS and not rec["policyType"]:
            rec["policyType"] = POLICY_TYPE_TOKENS[seg]
            source["policyType"] = "filename"
        elif seg in FRAMEWORK_TOKENS and not rec["framework"]:
            rec["framework"] = FRAMEWORK_TOKENS[seg]
            source["framework"] = "filename"
        elif seg in SCOPE_TOKENS and not rec["scope"]:
            rec["scope"] = SCOPE_TOKENS[seg]
            source["scope"] = "filename"
        elif normalise_version(seg) and not rec["version"]:
            rec["version"] = normalise_version(seg)
            source["version"] = "filename"
        else:
            leftovers.append(seg)

    # Pass 2 - folder.
    wl = workload_from_path(rel_path)
    if wl:
        rec["workload"], source["workload"] = wl, "folder"

    # Pass 3 - body fallbacks.
    if not rec["workload"]:
        wl = detect_workload_from_body(body)
        if wl:
            rec["workload"], source["workload"] = wl, "body"
    if not rec["platform"]:
        plat = PLATFORM_BODY.get(str(body.get("platforms", "")).strip())
        if plat:
            rec["platform"], source["platform"] = plat, "body"
        elif rec["workload"] in ("Entra", "Exchange Online", "SharePoint", "Teams", "Purview"):
            rec["platform"], source["platform"] = "Cross-platform", "inferred"
    if not rec["policyType"]:
        pt = detect_policy_type_from_body(body)
        if pt:
            rec["policyType"], source["policyType"] = pt, "body"
    if not rec["scope"]:
        sc = detect_scope_from_body(setting_ids)
        if sc:
            rec["scope"], source["scope"] = sc, "body"
        elif rec["policyType"] in DEFAULT_SCOPE_BY_TYPE:
            rec["scope"], source["scope"] = DEFAULT_SCOPE_BY_TYPE[rec["policyType"]], "inferred"

    if not rec["policyType"] and rec["workload"] == "Entra":
        rec["policyType"], source["policyType"] = "Tenant Policy", "inferred"
        if not rec["scope"]:
            rec["scope"], source["scope"] = "Tenant", "inferred"

    # Area and sub-area come from whatever the token pass did not claim.
    if leftovers:
        rec["area"] = AREA_ALIASES.get(leftovers[0], leftovers[0])
        source["area"] = "filename"
        if len(leftovers) > 1:
            rec["subArea"] = " - ".join(leftovers[1:])
            source["subArea"] = "filename"
    elif body.get("displayName") or body.get("name"):
        rec["area"] = str(body.get("displayName") or body.get("name"))[:80]
        source["area"] = "body"

    if rec["policyType"] == "Conditional Access":
        domain = detect_security_domain(body)
        if domain:
            rec["securityDomain"], source["securityDomain"] = domain, "body"

    unresolved = [k for k in ("workload", "platform", "policyType", "scope", "area") if not rec[k]]
    if not unresolved:
        confidence = "high" if all(
            source.get(k) in ("filename", "folder") for k in ("platform", "policyType", "scope")
        ) else "medium"
    else:
        confidence = "low"

    # Identity deliberately excludes version so that a version bump does not
    # break curated bundle membership or stored engagement selections.
    id_parts = [rec["workload"], rec["platform"], rec["policyType"], rec["framework"],
                rec["area"], rec["subArea"], rec["scope"]]
    policy_id = slugify(" ".join(p for p in id_parts if p)) or slugify(stem)

    return {
        "id": policy_id,
        "path": rel_path.replace("\\", "/"),
        "fileName": filename,
        "displayName": body.get("name") or body.get("displayName") or stem.replace("_", " "),
        **rec,
        "settingCount": len(setting_ids),
        "policyHash": file_hash(abs_path),
        "derivedFrom": source,
        "unresolved": unresolved,
        "confidence": confidence,
    }


# --------------------------------------------------------------------------
# Sidecar metadata
# --------------------------------------------------------------------------

def sidecar_path(root: str, rel_path: str, metadata_dir: str) -> str:
    return os.path.join(root, metadata_dir, rel_path.replace("\\", "/") + ".meta.json")


def load_sidecar(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    data = load_body(path)
    return data or {}


def apply_sidecar(policy: dict, meta: dict) -> dict:
    overrides = meta.get("overrides") or {}
    for key, value in overrides.items():
        if key in policy and value not in (None, ""):
            policy[key] = value
            policy["derivedFrom"][key] = "override"
    policy["unresolved"] = [k for k in policy["unresolved"] if k not in overrides]
    if not policy["unresolved"] and policy["confidence"] == "low":
        policy["confidence"] = "medium"

    enrichment = meta.get("enrichment") or {}
    if enrichment:
        stale = enrichment.get("sourceHash") not in (None, policy["policyHash"])
        enrichment = {**enrichment, "stale": stale}
    policy["enrichment"] = enrichment or None
    policy["tags"] = meta.get("tags") or []
    frameworks = [policy["framework"]] if policy.get("framework") else []
    for tag in policy["tags"]:
        name = FRAMEWORK_TAGS.get(tag)
        if name and name not in frameworks:
            frameworks.append(name)
    policy["frameworks"] = frameworks
    policy["notes"] = meta.get("notes")
    return policy


# --------------------------------------------------------------------------
# Bundles
# --------------------------------------------------------------------------

def rule_matches(rule: dict, policy: dict) -> bool:
    """AND across keys, OR within a key. Null rule never matches."""
    if not rule:
        return False
    for key, expected in rule.items():
        actual = policy.get(key)
        wanted = expected if isinstance(expected, list) else [expected]
        if isinstance(actual, list):
            if not set(wanted) & set(actual):
                return False
        elif actual not in wanted:
            return False
    return True


def resolve_bundles(policies: list[dict], bundles: list[dict]) -> list[dict]:
    by_id = {p["id"]: p for p in policies}
    for p in policies:
        p["bundles"] = []

    for bundle in bundles:
        members: list[str] = []
        for p in policies:
            if rule_matches(bundle.get("rule") or {}, p):
                members.append(p["id"])
        for pid in bundle.get("include") or []:
            if pid in by_id and pid not in members:
                members.append(pid)
            elif pid not in by_id:
                bundle.setdefault("missing", []).append(pid)
        excluded = set(bundle.get("exclude") or [])
        members = [m for m in members if m not in excluded]
        bundle["members"] = sorted(members)
        bundle["memberCount"] = len(members)
        for pid in members:
            by_id[pid]["bundles"].append(bundle["id"])
    return bundles


# --------------------------------------------------------------------------
# Build
# --------------------------------------------------------------------------

def discover(root: str, policy_dirs: list[str], metadata_dir: str) -> list[str]:
    found = []
    search_roots = [os.path.join(root, d) for d in policy_dirs] if policy_dirs else [root]
    for base in search_roots:
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames
                           if not d.startswith(".") and d not in {"node_modules", metadata_dir}]
            for fn in filenames:
                if fn.lower().endswith(".json") and not fn.lower().endswith(".meta.json"):
                    found.append(os.path.relpath(os.path.join(dirpath, fn), root))
    return sorted(found)


def build(root: str, policy_dirs: list[str], metadata_dir: str, bundles_file: str) -> dict:
    issues: list[dict] = []
    rel_paths = discover(root, policy_dirs, metadata_dir)

    policies = []
    for rel in rel_paths:
        abs_path = os.path.join(root, rel)
        if load_body(abs_path) is None:
            issues.append({"policyId": None, "path": rel, "type": "unreadable",
                           "detail": "File is not valid JSON object"})
            continue
        policy = derive(rel, abs_path)
        policy = apply_sidecar(policy, load_sidecar(sidecar_path(root, rel, metadata_dir)))
        policies.append(policy)

    # Identity collisions would silently merge two distinct policies.
    seen: dict[str, str] = {}
    for p in policies:
        if p["id"] in seen:
            issues.append({"policyId": p["id"], "path": p["path"], "type": "duplicateId",
                           "detail": f"Shares derived id with {seen[p['id']]}"})
            p["id"] = f"{p['id']}-{slugify(p['version'] or 'x')}"
        seen[p["id"]] = p["path"]

    for p in policies:
        for field in p["unresolved"]:
            issues.append({"policyId": p["id"], "path": p["path"], "type": "unresolved",
                           "detail": f"Could not derive '{field}'"})
        if p.get("enrichment") is None:
            issues.append({"policyId": p["id"], "path": p["path"], "type": "missingEnrichment",
                           "detail": "No enrichment authored"})
        elif p["enrichment"].get("stale"):
            issues.append({"policyId": p["id"], "path": p["path"], "type": "staleEnrichment",
                           "detail": "Policy changed since enrichment was generated"})

    bundles_abs = os.path.join(root, bundles_file)
    bundle_doc = load_body(bundles_abs) or {"bundles": []}
    bundles = resolve_bundles(policies, bundle_doc.get("bundles", []))

    # Presentation metadata for the Building Blocks wizard. Passed through
    # untouched: the builder resolves membership, it does not author outcomes.
    towers = bundle_doc.get("towers", [])
    goals = bundle_doc.get("goals", [])
    known = {b["id"] for b in bundles}
    for g in goals:
        for opt in g.get("options", []):
            for bid in opt.get("bundles", []):
                if bid not in known:
                    issues.append({"policyId": None, "path": None, "type": "danglingGoalBundle",
                                   "detail": f"Goal '{g.get('id')}' references unknown bundle '{bid}'"})

    for b in bundles:
        if b.get("missing"):
            for pid in b["missing"]:
                issues.append({"policyId": pid, "path": None, "type": "danglingBundleMember",
                               "detail": f"Bundle '{b['id']}' references unknown policy"})
        if b["memberCount"] == 0:
            issues.append({"policyId": None, "path": None, "type": "emptyBundle",
                           "detail": f"Bundle '{b['id']}' matched no policies"})
    for p in policies:
        if not p["bundles"]:
            issues.append({"policyId": p["id"], "path": p["path"], "type": "orphan",
                           "detail": "Policy is in no bundle"})

    def distinct(key):
        return sorted({p[key] for p in policies if p.get(key)})

    return {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sourceCommit": os.environ.get("GITHUB_SHA"),
        "counts": {
            "policies": len(policies),
            "bundles": len(bundles),
            "issues": len(issues),
            "byConfidence": dict(Counter(p["confidence"] for p in policies)),
        },
        "taxonomy": {
            "workloads": distinct("workload"),
            "platforms": distinct("platform"),
            "policyTypes": distinct("policyType"),
            "frameworks": sorted({f for p in policies for f in (p.get("frameworks") or [])}),
            "securityDomains": distinct("securityDomain"),
            "scopes": distinct("scope"),
            "areas": distinct("area"),
        },
        "towers": towers,
        "goals": goals,
        "policies": sorted(policies, key=lambda p: p["path"]),
        "bundles": bundles,
        "issues": issues,
    }


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def report(catalog: dict) -> None:
    c = catalog["counts"]
    print(f"Policies: {c['policies']}   Bundles: {c['bundles']}   "
          f"Towers: {len(catalog.get('towers', []))}   Goals: {len(catalog.get('goals', []))}   "
          f"Issues: {c['issues']}")
    print(f"Confidence: {c['byConfidence']}\n")

    tax = catalog["taxonomy"]
    for key in ("workloads", "platforms", "policyTypes", "frameworks", "scopes"):
        print(f"{key:12} {', '.join(tax[key]) or '(none)'}")
    print(f"{'areas':12} {len(tax['areas'])} distinct\n")

    grouped = defaultdict(list)
    for i in catalog["issues"]:
        grouped[i["type"]].append(i)
    print("Issues by type:")
    for t, items in sorted(grouped.items(), key=lambda kv: -len(kv[1])):
        print(f"  {t:22} {len(items)}")

    print("\nBundle membership:")
    for b in catalog["bundles"]:
        print(f"  {b['memberCount']:>4}  {b['id']:<34} {b['name']}")

    low = [p for p in catalog["policies"] if p["confidence"] == "low"]
    if low:
        print(f"\nNeeds attention ({len(low)}):")
        for p in low[:20]:
            print(f"  {p['fileName'][:66]:<66} missing: {', '.join(p['unresolved'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Build the CDW baseline policy catalog.")
    ap.add_argument("--root", default=".", help="Repository root")
    ap.add_argument("--out", default="catalog.json", help="Output path, relative to root")
    ap.add_argument("--metadata-dir", default="metadata", help="Sidecar metadata tree")
    ap.add_argument("--bundles", default="bundles.json", help="Bundle definitions, relative to root")
    ap.add_argument("--policy-dirs", nargs="*", default=[],
                    help="Restrict discovery to these top-level folders")
    ap.add_argument("--report", action="store_true", help="Print a summary instead of writing")
    ap.add_argument("--fail-on", nargs="*", default=[],
                    help="Issue types that should exit non-zero, e.g. duplicateId unreadable")
    args = ap.parse_args()

    catalog = build(args.root, args.policy_dirs, args.metadata_dir, args.bundles)

    if args.report:
        report(catalog)
    else:
        out_path = os.path.join(args.root, args.out)
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(catalog, fh, indent=2, ensure_ascii=False)
        print(f"Wrote {out_path}: {catalog['counts']['policies']} policies, "
              f"{catalog['counts']['bundles']} bundles, {catalog['counts']['issues']} issues")

    blocking = [i for i in catalog["issues"] if i["type"] in set(args.fail_on)]
    if blocking:
        print(f"\nBlocking issues ({len(blocking)}):", file=sys.stderr)
        for i in blocking:
            print(f"  {i['type']}: {i['detail']} ({i['path']})", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
