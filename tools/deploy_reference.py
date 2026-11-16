"""Create CDW Baseline policies from repo JSON in the reference tenant.

Run by .github/workflows/deploy-reference.yml. Create-only: a policy whose name
already exists in the tenant is skipped, never overwritten. To change a deployed
policy, bump its version in the name (the usual convention) or edit it in the
tenant and let the nightly export pick it up.

Supported folders (everything else is reported as skipped - deploy those from
the test site):
  IntuneConfig/SettingsCatalog, IntuneConfig/EndpointSecurity -> configurationPolicies
  IntuneConfig/CompliancePolicies                             -> deviceCompliancePolicies
  IntuneConfig/DeviceConfigurations                           -> deviceConfigurations
  EntraConfig/ConditionalAccess                               -> conditionalAccess/policies (always created disabled)

Usage:
  python tools/deploy_reference.py [--dry-run] FILE [FILE ...]
Env: TENANT_ID, DEPLOY_CLIENT_ID (+ GitHub OIDC), or GRAPH_TOKEN for local testing.
"""
import argparse
import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from cdw_graph import Graph, GraphError, token_federated  # noqa: E402

READ_ONLY = {
    "id", "createdDateTime", "lastModifiedDateTime", "modifiedDateTime", "deletedDateTime",
    "version", "settingCount", "settingsCount", "isAssigned", "assignments", "roleScopeTagIds",
    "deviceStatusOverview", "userStatusOverview", "deviceCompliancePolicyScript", "templateId",
    "creationSource", "priorityMetaData", "_comment",
    "@odata.context", "@odata.etag", "@microsoft.graph.tips",
}

KINDS = {
    "IntuneConfig/SettingsCatalog/":      ("configurationPolicy", "intune"),
    "IntuneConfig/EndpointSecurity/":     ("configurationPolicy", "intune"),
    "IntuneConfig/CompliancePolicies/":   ("compliancePolicy", "intune"),
    "IntuneConfig/DeviceConfigurations/": ("deviceConfiguration", "intune"),
    "EntraConfig/ConditionalAccess/":     ("conditionalAccess", "entra"),
}

ENDPOINTS = {
    "configurationPolicy": ("/beta/deviceManagement/configurationPolicies", "name"),
    "compliancePolicy":    ("/beta/deviceManagement/deviceCompliancePolicies", "displayName"),
    "deviceConfiguration": ("/beta/deviceManagement/deviceConfigurations", "displayName"),
    "conditionalAccess":   ("/v1.0/identity/conditionalAccess/policies", "displayName"),
}

CA_ARRAYS = {
    "conditions": ["clientAppTypes", "userRiskLevels", "signInRiskLevels",
                   "servicePrincipalRiskLevels", "insiderRiskLevels"],
    "conditions.applications": ["includeApplications", "excludeApplications",
                                "includeUserActions", "includeAuthenticationContextClassReferences"],
    "conditions.users": ["includeUsers", "excludeUsers", "includeGroups", "excludeGroups",
                         "includeRoles", "excludeRoles"],
    "conditions.platforms": ["includePlatforms", "excludePlatforms"],
    "conditions.locations": ["includeLocations", "excludeLocations"],
}


def kind_for(path):
    norm = path.replace("\\", "/")
    for prefix, kind in KINDS.items():
        if norm.startswith(prefix) and norm.endswith(".json"):
            return kind
    return None, None


def strip_nulls(o):
    if isinstance(o, dict):
        return {k: strip_nulls(v) for k, v in o.items() if v is not None}
    if isinstance(o, list):
        return [strip_nulls(v) for v in o]
    return o


def strip_read_only(o):
    return {k: v for k, v in o.items() if k not in READ_ONLY}


def build_body(kind, src):
    body = strip_read_only(copy.deepcopy(src))

    if kind == "configurationPolicy":
        keep = {"name", "description", "platforms", "technologies", "settings", "templateReference"}
        body = {k: v for k, v in body.items() if k in keep}
        body.setdefault("description", "")
        tr = body.get("templateReference") or {}
        if not tr.get("templateId"):
            body.pop("templateReference", None)
        else:
            body["templateReference"] = {"templateId": tr["templateId"]}
        body["settings"] = [{k: v for k, v in s.items() if k != "id"} for s in body.get("settings", [])]

    elif kind == "compliancePolicy":
        body = strip_nulls(body)
        if not body.get("scheduledActionsForRule"):
            # Same default the deployer site uses: block immediately on non-compliance.
            body["scheduledActionsForRule"] = [{
                "ruleName": "PasswordRequired",
                "scheduledActionConfigurations": [{
                    "actionType": "block", "gracePeriodHours": 0,
                    "notificationTemplateId": "", "notificationMessageCCList": [],
                }],
            }]

    elif kind == "deviceConfiguration":
        body = strip_nulls(body)

    elif kind == "conditionalAccess":
        body["state"] = "disabled"          # never enforce from automation
        body = strip_nulls(body)
        if not body.get("sessionControls"):
            body.pop("sessionControls", None)
        gc = body.get("grantControls") or {}
        gc.pop("authenticationStrength@odata.context", None)
        if isinstance(gc.get("authenticationStrength"), dict) and gc["authenticationStrength"].get("id"):
            # Exports carry the whole strength object; Graph only needs its id
            gc["authenticationStrength"] = {"id": gc["authenticationStrength"]["id"]}
        cond = body.get("conditions") or {}
        for key in ("servicePrincipalRiskLevels", "insiderRiskLevels"):
            if cond.get(key) == []:
                cond.pop(key)
        for dotted, keys in CA_ARRAYS.items():
            node = body
            for part in dotted.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if isinstance(node, dict):
                for k in keys:
                    if k in node and not isinstance(node[k], list):
                        node[k] = [node[k]]
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("files", nargs="*")
    args = ap.parse_args()

    files = sorted({f.strip() for f in args.files if f.strip()})
    if not files:
        print("No policy files to deploy.")
        return 0

    token = os.environ.get("GRAPH_TOKEN") or token_federated(os.environ["TENANT_ID"], os.environ["DEPLOY_CLIENT_ID"])
    graph = Graph(token)

    existing = {}
    rows, failed, workloads = [], 0, set()

    for path in files:
        kind, workload = kind_for(path)
        if not kind:
            rows.append((path, "skipped", "Folder not supported by automation - deploy from the test site"))
            continue
        if not os.path.exists(path):
            rows.append((path, "skipped", "File deleted on this branch"))
            continue
        with open(path, encoding="utf-8-sig") as fh:
            src = json.load(fh)

        endpoint, name_key = ENDPOINTS[kind]
        name = src.get(name_key)
        if not name:
            rows.append((path, "failed", f"No '{name_key}' in file")); failed += 1
            continue

        if kind not in existing:
            existing[kind] = {p.get(name_key) for p in graph.get_all(f"{endpoint}?$select=id,{name_key}")}
        if name in existing[kind]:
            rows.append((path, "exists", f"'{name}' already in tenant - not changed"))
            continue

        body = build_body(kind, src)
        if args.dry_run:
            rows.append((path, "dry run", f"Would create '{name}'"))
            continue
        try:
            created = graph.post(endpoint, body)
            existing[kind].add(name)
            workloads.add(workload)
            note = f"Created '{name}' ({created.get('id')})"
            if kind == "conditionalAccess":
                note += " - state: disabled"
            rows.append((path, "created", note))
        except GraphError as e:
            failed += 1
            rows.append((path, "failed", str(e)))
            print(f"::error file={path}::{e}")
            print(f"Request body:\n{json.dumps(body, indent=2)[:4000]}", file=sys.stderr)

    lines = ["## Reference tenant deployment" + (" (dry run)" if args.dry_run else ""), "",
             "| File | Result | Detail |", "|---|---|---|"]
    lines += [f"| `{p}` | {r} | {d.replace('|', '/')} |" for p, r, d in rows]
    report = "\n".join(lines)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(report + "\n")
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"intune={'true' if 'intune' in workloads else 'false'}\n")
            fh.write(f"entra={'true' if 'entra' in workloads else 'false'}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
