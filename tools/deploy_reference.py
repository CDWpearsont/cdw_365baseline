"""Create CDW Baseline policies from repo JSON in the reference tenant.

Run by .github/workflows/deploy-reference.yml. Create-only: a policy whose name
already exists in the tenant is skipped, never overwritten. To change a deployed
policy, bump its version in the name (the usual convention) or edit it in the
tenant and let the nightly export pick it up.

Two kinds of object:

* Named policies (created once, skipped if the name already exists):
  IntuneConfig/SettingsCatalog, EndpointSecurity   -> configurationPolicies
  IntuneConfig/CompliancePolicies                  -> deviceCompliancePolicies
  IntuneConfig/DeviceConfigurations                -> deviceConfigurations
  IntuneConfig/AppProtectionPolicies               -> ios/androidManagedAppProtections (+ apps)
  IntuneConfig/AppConfigurationPolicies            -> targetedManagedAppConfigurations (+ apps)
  IntuneConfig/EnrolmentRestrictions, EnrolmentStatusPage -> deviceEnrollmentConfigurations
  IntuneConfig/AutopilotProfiles                   -> windowsAutopilotDeploymentProfiles (Entra join)
  IntuneConfig/FeatureUpdates                      -> windowsFeatureUpdateProfiles
  EntraConfig/ConditionalAccess                    -> conditionalAccess/policies (always created disabled)
  EntraConfig/NamedLocations                       -> conditionalAccess/namedLocations

* Tenant-wide settings (one object per tenant, applied every time the file changes):
  EntraConfig/AuthorizationPolicy        -> policies/authorizationPolicy
  EntraConfig/CrossTenantAccessPolicy    -> policies/crossTenantAccessPolicy/default
  EntraConfig/DeviceRegistrationPolicy   -> policies/deviceRegistrationPolicy
  EntraConfig/AppManagementPolicies      -> policies/defaultAppManagementPolicy
  EntraConfig/DirectorySettings          -> groupSettings (create or update by template)
  EntraConfig/AuthMethods                -> authenticationMethodConfigurations/{method}
  SharePointConfig/TenantSettings        -> admin/sharepoint/settings

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
    "IntuneConfig/SettingsCatalog/":          ("configurationPolicy", "intune"),
    "IntuneConfig/EndpointSecurity/":         ("configurationPolicy", "intune"),
    "IntuneConfig/CompliancePolicies/":       ("compliancePolicy", "intune"),
    "IntuneConfig/DeviceConfigurations/":     ("deviceConfiguration", "intune"),
    "IntuneConfig/AppProtectionPolicies/":    ("appProtection", "intune"),
    "IntuneConfig/AppConfigurationPolicies/": ("appConfiguration", "intune"),
    "IntuneConfig/EnrolmentRestrictions/":    ("enrolmentRestriction", "intune"),
    "IntuneConfig/EnrolmentStatusPage/":      ("enrolmentStatusPage", "intune"),
    "IntuneConfig/AutopilotProfiles/":        ("autopilotProfile", "intune"),
    "IntuneConfig/FeatureUpdates/":           ("featureUpdate", "intune"),
    "EntraConfig/ConditionalAccess/":         ("conditionalAccess", "entra"),
    "EntraConfig/NamedLocations/":            ("namedLocation", "entra"),
    "EntraConfig/AuthorizationPolicy/":       ("authorizationPolicy", None),
    "EntraConfig/CrossTenantAccessPolicy/":   ("crossTenantAccessDefault", None),
    "EntraConfig/DeviceRegistrationPolicy/":  ("deviceRegistrationPolicy", None),
    "EntraConfig/AppManagementPolicies/":     ("defaultAppManagementPolicy", None),
    "EntraConfig/DirectorySettings/":         ("directorySetting", None),
    "EntraConfig/AuthMethods/":               ("authMethod", None),
    "SharePointConfig/TenantSettings/":       ("sharePointSettings", None),
}

# Named policies: collection endpoint and the property that holds the name.
ENDPOINTS = {
    "configurationPolicy":  ("/beta/deviceManagement/configurationPolicies", "name"),
    "compliancePolicy":     ("/beta/deviceManagement/deviceCompliancePolicies", "displayName"),
    "deviceConfiguration":  ("/beta/deviceManagement/deviceConfigurations", "displayName"),
    "appProtection":        (None, "displayName"),   # endpoint depends on platform, see endpoint_for
    "appConfiguration":     ("/beta/deviceAppManagement/targetedManagedAppConfigurations", "displayName"),
    "enrolmentRestriction": ("/beta/deviceManagement/deviceEnrollmentConfigurations", "displayName"),
    "enrolmentStatusPage":  ("/beta/deviceManagement/deviceEnrollmentConfigurations", "displayName"),
    "autopilotProfile":     ("/beta/deviceManagement/windowsAutopilotDeploymentProfiles", "displayName"),
    "featureUpdate":        ("/beta/deviceManagement/windowsFeatureUpdateProfiles", "displayName"),
    "conditionalAccess":    ("/v1.0/identity/conditionalAccess/policies", "displayName"),
    "namedLocation":        ("/v1.0/identity/conditionalAccess/namedLocations", "displayName"),
}

# Tenant-wide settings: one object, updated in place. displayName/description in
# these files are CDW metadata (shown in the catalog), not Graph properties.
SINGLETONS = {
    "authorizationPolicy":        ("PATCH", "/v1.0/policies/authorizationPolicy"),
    "crossTenantAccessDefault":   ("PATCH", "/v1.0/policies/crossTenantAccessPolicy/default"),
    "deviceRegistrationPolicy":   ("PUT",   "/beta/policies/deviceRegistrationPolicy"),
    "defaultAppManagementPolicy": ("PATCH", "/v1.0/policies/defaultAppManagementPolicy"),
    "sharePointSettings":         ("PATCH", "/v1.0/admin/sharepoint/settings"),
    "authMethod":                 ("PATCH", None),   # per method, see apply_singleton
    "directorySetting":           (None, None),      # create or update, see apply_singleton
}
METADATA = {"displayName", "description", "isServiceDefault", "_comment", "_notes"}

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


def endpoint_for(kind, src, path):
    if kind == "appProtection":
        t = (src.get("@odata.type") or "").lower()
        name = (src.get("displayName") or os.path.basename(path)).lower()
        if "android" in t or name.startswith("android"):
            return "/beta/deviceAppManagement/androidManagedAppProtections"
        if "windows" in t or name.startswith("win"):
            return "/beta/deviceAppManagement/windowsManagedAppProtections"
        return "/beta/deviceAppManagement/iosManagedAppProtections"
    return ENDPOINTS[kind][0]


def build_named(kind, src):
    """Body for a named policy plus any child items to add after creation."""
    children = None
    if kind in ("configurationPolicy", "compliancePolicy", "deviceConfiguration", "conditionalAccess"):
        return build_body(kind, src), None
    body = strip_read_only(copy.deepcopy(src))
    if kind == "appProtection":
        children = ("apps", [{k: v for k, v in a.items() if k not in ("id", "version")}
                             for a in body.pop("apps", []) or []])
        for k in ("deployedAppCount", "isAssigned"):
            body.pop(k, None)
    elif kind == "appConfiguration":
        children = ("targetApps", [{k: v for k, v in a.items() if k not in ("id", "version")}
                                   for a in body.pop("apps", []) or []])
        for k in ("deployedAppCount", "isAssigned"):
            body.pop(k, None)
    elif kind in ("enrolmentRestriction", "enrolmentStatusPage"):
        body.pop("priority", None)
        if kind == "enrolmentStatusPage":
            body.pop("selectedMobileAppIds", None)   # tenant-specific app ids
            body["@odata.type"] = "#microsoft.graph.windows10EnrollmentCompletionPageConfiguration"
    elif kind == "autopilotProfile":
        for k in ("assignedDevices", "managementServiceAppId", "enrollmentStatusScreenSettings"):
            body.pop(k, None)
        body["@odata.type"] = "#microsoft.graph.azureADWindowsAutopilotDeploymentProfile"
        # Drop deprecated aliases that duplicate current property names
        for old, cur in (("outOfBoxExperienceSettings", "outOfBoxExperienceSetting"),
                         ("extractHardwareHash", "hardwareHashExtractionEnabled"),
                         ("enableWhiteGlove", "preprovisioningAllowed"), ("language", "locale")):
            if cur in body:
                body.pop(old, None)
    elif kind == "featureUpdate":
        for k in ("endOfSupportDate", "deployableContentDisplayName"):
            body.pop(k, None)
    return strip_nulls(body), children


def apply_singleton(graph, kind, src, dry_run):
    """Update a tenant-wide setting. Returns a one-line result."""
    body = {k: v for k, v in copy.deepcopy(src).items() if k not in METADATA and k not in READ_ONLY}
    if kind == "authMethod":
        method = (src.get("@odata.type") or "").replace("#microsoft.graph.", "").replace(
            "AuthenticationMethodConfiguration", "")
        if not src.get("id") and not method:
            raise ValueError("Auth method file needs 'id' (e.g. fido2) or @odata.type")
        method_id = src.get("id") or method
        url = f"/beta/policies/authenticationMethodsPolicy/authenticationMethodConfigurations/{method_id}"
        if not dry_run:
            graph.request("PATCH", url, body)
        return f"Updated authentication method '{method_id}'"
    if kind == "directorySetting":
        template = src.get("templateId")
        if not template:
            raise ValueError("Directory setting file needs 'templateId'")
        body = {"values": src.get("values", [])}
        current = [s for s in graph.get_all("/v1.0/groupSettings") if s.get("templateId") == template]
        if dry_run:
            return f"Would {'update' if current else 'create'} directory setting {template}"
        if current:
            graph.request("PATCH", f"/v1.0/groupSettings/{current[0]['id']}", body)
            return f"Updated directory setting {template}"
        graph.post("/v1.0/groupSettings", {"templateId": template, **body})
        return f"Created directory setting {template}"
    method, url = SINGLETONS[kind]
    if kind == "deviceRegistrationPolicy":
        body["id"] = "deviceRegistrationPolicy"
    if not dry_run:
        graph.request(method, url, body)
    return f"{'Would apply' if dry_run else 'Applied'} {url.rsplit('/', 1)[-1]}"


def load_library_only(root="."):
    """Policies the reference tenant can't hold (licensing). Path -> reason."""
    path = os.path.join(root, "library-only.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as fh:
        data = json.load(fh)
    return {f["path"]: f.get("reason", "") for f in data.get("files", [])}


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
    library_only = load_library_only()

    for path in files:
        if path.replace("\\", "/") in library_only:
            rows.append((path, "skipped", "Library-only: " + library_only[path.replace("\\", "/")]))
            continue
        kind, workload = kind_for(path)
        if not kind:
            rows.append((path, "skipped", "Folder not supported by automation - deploy from the test site"))
            continue
        if not os.path.exists(path):
            rows.append((path, "skipped", "File deleted on this branch"))
            continue
        with open(path, encoding="utf-8-sig") as fh:
            src = json.load(fh)

        if kind in SINGLETONS:
            try:
                note = apply_singleton(graph, kind, src, args.dry_run)
                rows.append((path, "dry run" if args.dry_run else "applied", note))
            except (GraphError, ValueError) as e:
                failed += 1
                rows.append((path, "failed", str(e)))
                print(f"::error file={path}::{e}")
            continue

        endpoint = endpoint_for(kind, src, path)
        name_key = ENDPOINTS[kind][1]
        name = src.get(name_key)
        if not name:
            rows.append((path, "failed", f"No '{name_key}' in file")); failed += 1
            continue

        if endpoint not in existing:
            # No $select: some of these collections reject it and return nothing
            existing[endpoint] = {p.get(name_key) for p in graph.get_all(endpoint)}
        if name in existing[endpoint]:
            rows.append((path, "exists", f"'{name}' already in tenant - not changed"))
            continue

        body, children = build_named(kind, src)
        if args.dry_run:
            rows.append((path, "dry run", f"Would create '{name}'"))
            continue
        try:
            created = graph.post(endpoint, body)
            existing[endpoint].add(name)
            workloads.add(workload)
            note = f"Created '{name}' ({created.get('id')})"
            if children is not None:
                # Target apps in one call. For the 'all apps' style groups the service
                # fills in the app list itself, so only the group type is sent.
                group = src.get("appGroupType", "selectedPublicApps")
                items = children[1] if group == "selectedPublicApps" else []
                graph.post(f"{endpoint}/{created['id']}/targetApps", {"apps": items, "appGroupType": group})
                note += f" targeting {group}" + (f" ({len(items)} apps)" if items else "")
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
