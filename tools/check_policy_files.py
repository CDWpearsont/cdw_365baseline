"""Offline checks for policy JSONs changed in a PR (no Graph access needed).

Catches the things that would otherwise only show up after deployment or after
the nightly export:
  * invalid JSON
  * missing name / platforms / technologies / settings
  * names the nightly export wouldn't pick up ("CDW Baseline" for Intune, "CDW" prefix for CA)
  * file name not matching what the export would write (it would duplicate the file)
  * Conditional Access policies set to enabled
  * duplicate policy names in the same folder

Usage: python tools/check_policy_files.py FILE [FILE ...]
"""
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
from deploy_reference import ENDPOINTS, SINGLETONS, kind_for  # noqa: E402


def safe_name(name):
    return re.sub(r"[^a-zA-Z0-9-]", "_", name)


def load(path):
    with open(path, encoding="utf-8-sig") as fh:
        return json.load(fh)


def check(path):
    errors, warnings = [], []
    try:
        data = load(path)
    except Exception as e:
        return [f"Invalid JSON: {e}"], warnings

    kind, _ = kind_for(path)
    norm = path.replace("\\", "/")
    is_entra = norm.startswith("EntraConfig/")
    if not kind:
        # Naming rules for these folders differ per type; only check the JSON parses.
        warnings.append("Folder not supported by deploy-reference - deploy this one from the test site")
        return errors, warnings
    if kind in SINGLETONS:
        # Tenant-wide settings: no export naming rules, just the CDW metadata
        if not data.get("displayName"):
            errors.append("Missing 'displayName' (CDW metadata shown in the catalog)")
        if kind == "directorySetting" and not data.get("templateId"):
            errors.append("Missing 'templateId'")
        if kind == "authMethod" and not (data.get("id") or data.get("@odata.type")):
            errors.append("Missing 'id' or '@odata.type' (which authentication method)")
        return errors, warnings
    name_key = ENDPOINTS[kind][1]
    name = data.get(name_key)
    if not name:
        errors.append(f"Missing '{name_key}'")
        return errors, warnings

    if is_entra and not name.startswith("CDW"):
        errors.append("Entra policy names must start with 'CDW' or the nightly export will ignore them")
    if not is_entra and "CDW Baseline" not in name:
        errors.append("Name must contain 'CDW Baseline' or the nightly export will ignore it")

    stem = os.path.splitext(os.path.basename(path))[0]
    if stem != safe_name(name):
        errors.append(f"File name should be '{safe_name(name)}.json' to match the nightly export")

    if kind == "configurationPolicy":
        for k in ("platforms", "technologies"):
            if not data.get(k):
                errors.append(f"Missing '{k}'")
        settings = data.get("settings") or []
        if not settings:
            errors.append("No settings")
        for i, s in enumerate(settings):
            si = s.get("settingInstance") or {}
            if not si.get("settingDefinitionId") or not si.get("@odata.type"):
                errors.append(f"settings[{i}] is missing settingInstance.settingDefinitionId or @odata.type")
        if "EndpointSecurity/" in norm and not (data.get("templateReference") or {}).get("templateId"):
            errors.append("Endpoint Security policy needs templateReference.templateId")

    if kind == "conditionalAccess" and data.get("state") == "enabled":
        errors.append("CA state is 'enabled' - reference policies are kept 'disabled' (or report-only)")

    if data.get("assignments"):
        errors.append("Contains assignments - these are tenant-specific and must be removed")

    folder = os.path.dirname(path)
    for other in glob.glob(os.path.join(folder, "*.json")):
        if os.path.abspath(other) == os.path.abspath(path):
            continue
        try:
            if load(other).get(name_key) == name:
                errors.append(f"Same name as {os.path.basename(other)}")
        except Exception:
            pass
    return errors, warnings


def main(files):
    files = [f for f in files if f.endswith(".json") and os.path.exists(f)]
    if not files:
        print("No policy JSONs to check.")
        return 0
    bad = 0
    lines = ["## Policy file checks", "", "| File | Result |", "|---|---|"]
    for f in sorted(files):
        errors, warnings = check(f)
        for e in errors:
            print(f"::error file={f}::{e}")
        for w in warnings:
            print(f"::warning file={f}::{w}")
        bad += bool(errors)
        result = "<br>".join([f"❌ {e}" for e in errors] + [f"⚠️ {w}" for w in warnings]) or "✅"
        lines.append(f"| `{f}` | {result} |")
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
