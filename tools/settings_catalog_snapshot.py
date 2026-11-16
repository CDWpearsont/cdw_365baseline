"""Snapshot the Intune Settings Catalog and report what changed.

Run weekly by .github/workflows/settings-catalog-snapshot.yml with the read-only
backup app. Writes into OUT_DIR (the 'settings-catalog' branch checkout):

  settings.ndjson    one setting definition per line, sorted by id (trimmed to the
                     fields needed to author policy JSON)
  categories.ndjson  settings catalog categories (for the portal path of a setting)
  templates.ndjson   Endpoint Security / baseline templates
  CHANGES.md         new, removed and changed settings since the previous snapshot

Usage:
  python tools/settings_catalog_snapshot.py fetch OUT_DIR
  python tools/settings_catalog_snapshot.py diff  OLD_DIR NEW_DIR   (writes NEW_DIR/CHANGES.md)
Env for fetch: TENANT_ID, CLIENT_ID, CLIENT_SECRET (or GRAPH_TOKEN).
"""
import datetime
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from cdw_graph import Graph, token_client_secret  # noqa: E402

BASE = "/beta/deviceManagement"
SETTING_FIELDS = ("id", "name", "displayName", "description", "keywords", "categoryId",
                  "version", "settingUsage", "visibility", "infoUrls")


def trim_setting(d):
    out = {k: d.get(k) for k in SETTING_FIELDS if d.get(k) not in (None, [], "")}
    out["type"] = (d.get("@odata.type") or "").replace("#microsoft.graph.deviceManagementConfiguration", "")
    app = d.get("applicability") or {}
    out["platform"] = app.get("platform")
    out["technologies"] = app.get("technologies")
    if app.get("windowsSkus"):
        out["windowsSkus"] = app["windowsSkus"]
    if d.get("options"):
        out["options"] = [{"itemId": o.get("itemId"), "displayName": o.get("displayName")} for o in d["options"]]
    vd = d.get("valueDefinition") or {}
    if vd:
        out["valueType"] = (vd.get("@odata.type") or "").replace("#microsoft.graph.deviceManagementConfiguration", "")
        for k in ("minimumValue", "maximumValue", "minimumLength", "maximumLength", "isSecret"):
            if vd.get(k) is not None:
                out[k] = vd[k]
    if d.get("defaultOptionId"):
        out["defaultOptionId"] = d["defaultOptionId"]
    return out


def write_ndjson(path, rows):
    rows = sorted(rows, key=lambda r: r["id"])
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def read_ndjson(path):
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        return {r["id"]: r for r in map(json.loads, filter(None, map(str.strip, fh)))}


def fetch(out_dir):
    token = os.environ.get("GRAPH_TOKEN") or token_client_secret(
        os.environ["TENANT_ID"], os.environ["CLIENT_ID"], os.environ["CLIENT_SECRET"])
    g = Graph(token)
    os.makedirs(out_dir, exist_ok=True)

    settings = [trim_setting(d) for d in g.get_all(f"{BASE}/configurationSettings")]
    n = write_ndjson(os.path.join(out_dir, "settings.ndjson"), settings)
    print(f"settings: {n}")

    cats = [{k: c.get(k) for k in ("id", "displayName", "parentCategoryId", "rootCategoryId",
                                    "platforms", "technologies")}
            for c in g.get_all(f"{BASE}/configurationCategories")]
    print(f"categories: {write_ndjson(os.path.join(out_dir, 'categories.ndjson'), cats)}")

    tpls = [{k: t.get(k) for k in ("id", "baseId", "version", "displayName", "description",
                                    "templateFamily", "templateDisplayName", "templateDisplayVersion",
                                    "platforms", "technologies", "lifecycleState")}
            for t in g.get_all(f"{BASE}/configurationPolicyTemplates")]
    print(f"templates: {write_ndjson(os.path.join(out_dir, 'templates.ndjson'), tpls)}")


def _label(r, cats):
    path = []
    cid = r.get("categoryId")
    while cid and cid in cats and len(path) < 6:
        path.insert(0, cats[cid].get("displayName") or "?")
        cid = cats[cid].get("parentCategoryId")
    where = " > ".join(path)
    plat = r.get("platform") or ""
    return f"**{r.get('displayName') or r.get('name')}** ({plat}{' - ' + where if where else ''})<br>`{r['id']}`"


def diff(old_dir, new_dir):
    old = read_ndjson(os.path.join(old_dir, "settings.ndjson"))
    new = read_ndjson(os.path.join(new_dir, "settings.ndjson"))
    cats = read_ndjson(os.path.join(new_dir, "categories.ndjson"))
    old_t = read_ndjson(os.path.join(old_dir, "templates.ndjson"))
    new_t = read_ndjson(os.path.join(new_dir, "templates.ndjson"))
    today = datetime.date.today().isoformat()

    added = [new[i] for i in sorted(set(new) - set(old))]
    removed = [old[i] for i in sorted(set(old) - set(new))]
    changed = []
    for i in sorted(set(old) & set(new)):
        o, n = old[i], new[i]
        notes = []
        if o.get("displayName") != n.get("displayName"):
            notes.append(f"renamed from '{o.get('displayName')}'")
        oo = {x["itemId"] for x in o.get("options") or []}
        no = {x["itemId"] for x in n.get("options") or []}
        if no - oo:
            notes.append("new options: " + ", ".join(f"`{x}`" for x in sorted(no - oo)))
        if oo - no:
            notes.append("options removed: " + ", ".join(f"`{x}`" for x in sorted(oo - no)))
        if (o.get("settingUsage"), o.get("visibility")) != (n.get("settingUsage"), n.get("visibility")):
            notes.append(f"usage/visibility now {n.get('settingUsage')}/{n.get('visibility')}")
        if notes:
            changed.append((n, notes))
    t_added = [new_t[i] for i in sorted(set(new_t) - set(old_t))]
    t_removed = [old_t[i] for i in sorted(set(old_t) - set(new_t))]

    if not old:
        body = (f"# Settings Catalog snapshot - {today}\n\nFirst snapshot: {len(new)} settings, "
                f"{len(new_t)} templates. Changes will be reported from next week.\n")
        has_changes = False
    else:
        has_changes = bool(added or removed or changed or t_added or t_removed)
        parts = [f"# Settings Catalog changes - {today}", "",
                 f"{len(added)} new, {len(removed)} removed, {len(changed)} changed settings; "
                 f"{len(t_added)} new and {len(t_removed)} removed templates. Total now {len(new)} settings.", ""]

        def section(title, items, fmt, limit=150):
            if not items:
                return
            parts.extend([f"## {title} ({len(items)})", ""])
            by_plat = {}
            for it in items:
                key = (it[0] if isinstance(it, tuple) else it).get("platform") or "other"
                by_plat.setdefault(key, []).append(it)
            shown = 0
            for plat in sorted(by_plat):
                parts.append(f"### {plat}")
                for it in by_plat[plat]:
                    if shown >= limit:
                        break
                    parts.append(fmt(it))
                    shown += 1
                parts.append("")
            if len(items) > limit:
                parts.append(f"_...and {len(items) - limit} more - see CHANGES.md on the settings-catalog branch._\n")

        section("New settings", added, lambda r: f"- [ ] {_label(r, cats)}")
        section("Changed settings", changed, lambda t: f"- {_label(t[0], cats)} - {'; '.join(t[1])}")
        section("Removed settings", removed, lambda r: f"- {_label(r, cats)}")
        if t_added or t_removed:
            parts += ["## Templates", ""]
            parts += [f"- New: **{t.get('displayName')}** {t.get('templateDisplayVersion') or ''} "
                      f"({t.get('templateFamily')}) `{t['id']}`" for t in t_added]
            parts += [f"- Removed: **{t.get('displayName')}** ({t.get('templateFamily')}) `{t['id']}`"
                      for t in t_removed]
            parts.append("")
        body = "\n".join(parts)

    with open(os.path.join(new_dir, "CHANGES.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(body)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            fh.write(f"has_changes={'true' if has_changes else 'false'}\n")
    print(body[:3000])


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "fetch" and len(sys.argv) == 3:
        fetch(sys.argv[2])
    elif cmd == "diff" and len(sys.argv) == 4:
        diff(sys.argv[2], sys.argv[3])
    else:
        sys.exit(__doc__)
