#!/usr/bin/env python3
"""AI enrichment for the policy library.

Writes the enrichment block of each policy's sidecar (metadata/<path>.meta.json)
with Azure OpenAI, for policies whose enrichment is missing or stale. Also fills
taxonomy attributes the catalog builder could not derive, and suggests Building
Block membership for policies in no bundle (suggestions go in the report only;
bundles.json is never edited).

Rules
- Derived attributes are never written; overrides only for attributes listed as
  unresolved, and only from the catalog's own vocabulary.
- An enrichment with "locked": true is never touched, even when stale. Set it in
  the sidecar when you have edited the text by hand.
- Staleness uses the policy's content hash (ids, timestamps and the versioned
  name are ignored), so re-exports and renames do not trigger regeneration.
- Output is reviewed in a pull request; nothing here is applied to a tenant.

Environment
  AZURE_OPENAI_ENDPOINT     https://<resource>.openai.azure.com/
  AZURE_OPENAI_KEY          API key
  AZURE_OPENAI_DEPLOYMENT   chat model deployment name
  AZURE_OPENAI_API_VERSION  optional, default 2025-01-01-preview

    python tools/enrich_policies.py --root . --max 40 --report enrichment-report.md
    python tools/enrich_policies.py --root . --dry-run          # list what would run
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_catalog as bc  # noqa: E402

POLICY_DIRS = ["IntuneConfig", "EntraConfig", "SharePointConfig", "ExchangeOnline", "DefenderCloudApps"]
OVERRIDABLE = ["platform", "policyType", "scope", "area", "securityDomain"]
TEXT_LIMITS = {"summary": 600, "rationale": 600, "userImpact": 400, "licensing": 200}
# The customer must not be able to tell the document was generated, and the
# library's internal names must not leak into customer-facing prose.
BANNED = re.compile(r"CDW\s*(365\s*)?Baseline|baseline tool|this tool|\bthe library\b|reference tenant", re.I)

SYSTEM_PROMPT = """You are a senior Microsoft 365 security consultant documenting configuration policies \
for a Low Level Design. Respond ONLY with a JSON object. Use British English \
(organisation, behaviour, minimise). Write for a customer audience: plain, specific and factual. \
Never mention CDW, a baseline library, a tool, a reference tenant or how the document was produced. \
Do not name the customer; the text is reused across customers. Do not invent settings that are not listed."""


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ── Setting names ───────────────────────────────────────────────────────────

class Names:
    def __init__(self, path: str):
        self.names, self.options = {}, {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.names, self.options = data.get("names", {}), data.get("options", {})

    def setting(self, def_id: str) -> str:
        if def_id in self.names:
            return self.names[def_id]
        last = def_id.split("_")[-1]
        return bc.split_camel(last) if last else def_id

    def option(self, value: str) -> str:
        if value in self.options:
            return self.options[value]
        tail = value.split("_")[-1]
        return {"0": "Disabled", "1": "Enabled"}.get(tail, tail)


def settings_lines(node, names: Names, out: list, depth: int = 0) -> None:
    """Readable "name: value" lines for a Settings Catalog / Endpoint Security body."""
    if isinstance(node, list):
        for item in node:
            settings_lines(item, names, out, depth)
        return
    if not isinstance(node, dict):
        return
    inst = node.get("settingInstance") if "settingInstance" in node else node
    if not isinstance(inst, dict) or "settingDefinitionId" not in inst:
        for v in node.values():
            if isinstance(v, (dict, list)):
                settings_lines(v, names, out, depth)
        return
    name = names.setting(inst["settingDefinitionId"])
    pad = "  " * depth
    as_list = lambda v: v if isinstance(v, list) else [v] if isinstance(v, dict) else []  # noqa: E731
    if isinstance(inst.get("choiceSettingValue"), dict):
        cv = inst["choiceSettingValue"]
        out.append(f"{pad}- {name}: {names.option(str(cv.get('value', '')))}")
        settings_lines(as_list(cv.get("children")), names, out, depth + 1)
    elif isinstance(inst.get("simpleSettingValue"), dict):
        out.append(f"{pad}- {name}: {inst['simpleSettingValue'].get('value')}")
    elif inst.get("simpleSettingCollectionValue") is not None:
        vals = [str(v.get("value")) for v in as_list(inst["simpleSettingCollectionValue"])]
        out.append(f"{pad}- {name}: {', '.join(vals)[:300]}")
    elif inst.get("choiceSettingCollectionValue") is not None:
        vals = [names.option(str(v.get("value", ""))) for v in as_list(inst["choiceSettingCollectionValue"])]
        out.append(f"{pad}- {name}: {', '.join(vals)[:300]}")
    elif inst.get("groupSettingCollectionValue") is not None:
        out.append(f"{pad}- {name}:")
        for group in as_list(inst["groupSettingCollectionValue"]):
            settings_lines(group.get("children") or [], names, out, depth + 1)
    elif isinstance(inst.get("groupSettingValue"), dict):
        out.append(f"{pad}- {name}:")
        settings_lines(as_list(inst["groupSettingValue"].get("children")), names, out, depth + 1)


def describe_body(body: dict, names: Names, limit: int = 9000) -> str:
    if isinstance(body.get("settings"), list) and body["settings"]:
        lines: list[str] = []
        settings_lines(body["settings"], names, lines)
        text = "\n".join(lines)
    else:
        def strip(node):
            if isinstance(node, dict):
                return {k: strip(v) for k, v in node.items()
                        if k not in bc.VOLATILE_KEYS and not k.startswith("@odata") and v not in (None, [], {}, "")}
            if isinstance(node, list):
                return [strip(v) for v in node]
            return node
        text = json.dumps(strip(body), indent=1, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + "\n… (truncated)"


# ── Azure OpenAI ────────────────────────────────────────────────────────────

class AzureOpenAI:
    def __init__(self):
        self.endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/") + "/"
        self.key = os.environ.get("AZURE_OPENAI_KEY", "")
        self.deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "")
        self.version = os.environ.get("AZURE_OPENAI_API_VERSION") or "2025-01-01-preview"
        self.calls = 0
        self.tokens = 0

    def ready(self) -> bool:
        return bool(self.endpoint.strip("/") and self.key and self.deployment)

    def chat_json(self, system: str, user: str, max_tokens: int = 900) -> dict:
        url = (f"{self.endpoint}openai/deployments/{self.deployment}/chat/completions"
               f"?api-version={self.version}")
        body = json.dumps({
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "max_tokens": max_tokens, "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }).encode("utf-8")
        for attempt in range(4):
            req = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json", "api-key": self.key})
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                self.calls += 1
                self.tokens += (data.get("usage") or {}).get("total_tokens", 0)
                text = data["choices"][0]["message"]["content"]
                return json.loads(text)
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                    wait = int(e.headers.get("retry-after") or 0) or 5 * (attempt + 1)
                    time.sleep(min(wait, 60))
                    continue
                raise RuntimeError(f"Azure OpenAI HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 3:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise RuntimeError(f"Azure OpenAI unreachable: {e}")
        raise RuntimeError("Azure OpenAI: retries exhausted")


# ── Enrichment ──────────────────────────────────────────────────────────────

def enrichment_prompt(policy: dict, body: dict, names: Names, vocab: dict) -> str:
    unresolved = [k for k in policy.get("unresolved", []) if k in OVERRIDABLE]
    want_overrides = ""
    if unresolved:
        choices = "\n".join(f'  - "{k}": one of {json.dumps(vocab.get(k) or [])}' for k in unresolved if k != "area")
        if "area" in unresolved:
            choices += '\n  - "area": a short functional area name (2-4 words, Title Case)'
        want_overrides = f"""
6. "overrides": values for attributes that could not be derived. Use only these keys and values:
{choices}
   Leave a key out if the policy does not make it clear."""
    return f"""Document this Microsoft 365 policy.

Return a JSON object with:
1. "summary": what the policy configures and the security or operational outcome, 1-2 sentences, \
present tense, starting with a verb (e.g. "Enforces…", "Configures…", "Blocks…"). Customer-facing. \
Do not repeat the policy name.
2. "rationale": why an organisation would deploy it, 1-2 sentences.
3. "userImpact": what users will notice, 1 sentence. Use "No visible change for users." if none.
4. "prerequisites": array of short strings (licences excluded), e.g. "Devices enrolled in Intune". Empty array if none.
5. "licensing": the Microsoft licence that provides this capability, short, e.g. \
"Microsoft Intune Plan 1 (Microsoft 365 E3/E5)" or "Microsoft Entra ID P1".{want_overrides}

Policy: {policy.get('displayName')}
Workload: {policy.get('workload')} | Platform: {policy.get('platform')} | Type: {policy.get('policyType')} \
| Framework: {policy.get('framework') or 'none'} | Scope: {policy.get('scope')}
Description: {(body.get('description') or '').strip()[:800] or 'none'}

Configuration:
{describe_body(body, names)}"""


def clean_enrichment(raw: dict, policy: dict, vocab: dict) -> tuple[dict, dict, list[str]]:
    problems: list[str] = []
    out: dict = {}
    for key, limit in TEXT_LIMITS.items():
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            if key in ("summary", "rationale"):
                problems.append(f"missing {key}")
            continue
        val = re.sub(r"\s+", " ", val).strip()
        if BANNED.search(val):
            problems.append(f"{key} mentions internal names")
        out[key] = val[:limit]
    prereq = raw.get("prerequisites")
    out["prerequisites"] = [re.sub(r"\s+", " ", str(p)).strip()[:160] for p in prereq][:6] \
        if isinstance(prereq, list) else []
    overrides = {}
    for key, value in (raw.get("overrides") or {}).items():
        if key not in policy.get("unresolved", []) or key not in OVERRIDABLE or not isinstance(value, str):
            continue
        if key == "area":
            if 2 <= len(value) <= 40:
                overrides[key] = value.strip()
        elif value in (vocab.get(key) or []):
            overrides[key] = value
    return out, overrides, problems


def orphan_prompt(policy: dict, body: dict, names: Names, bundles: list[dict]) -> str:
    listing = "\n".join(f'- {b["id"]}: {b["name"]} — {(b.get("description") or "")[:140]}' for b in bundles)
    return f"""This policy is in no Building Block. Suggest which existing blocks it belongs in.

Return {{"bundles": [{{"id": "<bundle id>", "reason": "<one sentence>"}}]}} — at most 2, \
or an empty array if none fits well. Only use ids from the list.

Building Blocks:
{listing}

Policy: {policy.get('displayName')} ({policy.get('workload')}, {policy.get('platform')}, {policy.get('policyType')})
Description: {(body.get('description') or '').strip()[:500] or 'none'}
Configuration (excerpt):
{describe_body(body, names, 2500)}"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--policy-dirs", nargs="+", default=POLICY_DIRS)
    ap.add_argument("--metadata-dir", default="metadata")
    ap.add_argument("--bundles", default="bundles.json")
    ap.add_argument("--names", default="site/setting-names.json")
    ap.add_argument("--max", type=int, default=40, help="Most policies to enrich in one run")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="List the work without calling the AI")
    ap.add_argument("--report", help="Write a Markdown summary (used as the PR body)")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    catalog = bc.build(root, args.policy_dirs, args.metadata_dir, args.bundles)
    vocab = catalog.get("taxonomy", {})
    vocab = {"platform": vocab.get("platforms"), "policyType": vocab.get("policyTypes"),
             "scope": vocab.get("scopes"), "securityDomain": vocab.get("securityDomains")}
    names = Names(os.path.join(root, args.names))

    todo, locked_stale = [], []
    for p in catalog["policies"]:
        en = p.get("enrichment") or {}
        missing = not en.get("summary")
        stale = bool(en.get("stale"))
        unresolved = [k for k in p.get("unresolved", []) if k in OVERRIDABLE]
        if en.get("locked"):
            if stale:
                locked_stale.append(p)
            continue
        if unresolved and not (missing or stale) and en.get("taxonomyCheckedHash") == p.get("contentHash"):
            unresolved = []   # already asked for this content; needs a person or a builder rule
        if missing or stale or unresolved:
            todo.append((p, "new" if missing else "stale" if stale else "taxonomy"))
    orphans = [p for p in catalog["policies"] if not p.get("bundles")]
    # Order: new first, then stale, then taxonomy-only; stable by path.
    order = {"new": 0, "stale": 1, "taxonomy": 2}
    todo.sort(key=lambda t: (order[t[1]], t[0]["path"]))
    backlog = len(todo)
    todo = todo[: max(0, args.max)]

    print(f"Policies: {len(catalog['policies'])} | to enrich: {backlog} (this run: {len(todo)}) | "
          f"locked but stale: {len(locked_stale)} | in no block: {len(orphans)}")
    if args.dry_run:
        for p, why in todo:
            print(f"  [{why}] {p['path']}")
        return 0

    ai = AzureOpenAI()
    if not ai.ready():
        print("AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY and AZURE_OPENAI_DEPLOYMENT must be set", file=sys.stderr)
        return 2

    results, failures = [], []

    def work(item):
        p, why = item
        abs_path = os.path.join(root, p["path"])
        body = bc.load_body(abs_path) or {}
        prompt = enrichment_prompt(p, body, names, vocab)
        raw = ai.chat_json(SYSTEM_PROMPT, prompt)
        fields, overrides, problems = clean_enrichment(raw, p, vocab)
        if problems:   # one retry with the problems spelled out
            raw = ai.chat_json(SYSTEM_PROMPT, prompt + "\n\nYour previous answer had these problems: "
                               + "; ".join(problems) + ". Correct them.")
            fields, overrides, problems = clean_enrichment(raw, p, vocab)
        if problems:
            raise RuntimeError("; ".join(problems))
        return p, why, body, fields, overrides

    with ThreadPoolExecutor(max_workers=max(1, args.concurrency)) as pool:
        futures = [pool.submit(work, item) for item in todo]
        for item, fut in zip(todo, futures):
            try:
                results.append(fut.result())
            except Exception as e:  # noqa: BLE001 - one bad policy must not stop the run
                failures.append((item[0], str(e)))
                print(f"  ! {item[0]['path']}: {e}", file=sys.stderr)

    model = ai.deployment
    for p, why, body, fields, overrides in results:
        side_path = bc.sidecar_path(root, p["path"], args.metadata_dir)
        meta = bc.load_sidecar(side_path) or {}
        meta.setdefault("schemaVersion", 1)
        meta["policyPath"] = p["path"]
        if overrides:
            meta["overrides"] = {**(meta.get("overrides") or {}), **overrides}
            meta["overrideSources"] = {**(meta.get("overrideSources") or {}), **{k: "ai" for k in overrides}}
        meta["enrichment"] = {
            **fields,
            "generatedBy": model,
            "generatedAt": now(),
            "sourceHash": bc.content_hash(body),
            "reviewedBy": None,
            "reviewedAt": None,
            "locked": False,
            "taxonomyCheckedHash": bc.content_hash(body),
        }
        os.makedirs(os.path.dirname(side_path), exist_ok=True)
        with open(side_path, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(meta, fh, indent=2, ensure_ascii=False)
            fh.write("\n")

    # Building Block suggestions for policies in no block (report only).
    suggestions = []
    bundles = [b for b in catalog.get("bundles", []) if b.get("memberCount")]
    for p in (orphans[:15] if results else []):   # only alongside real changes, so idle runs cost nothing
        try:
            body = bc.load_body(os.path.join(root, p["path"])) or {}
            raw = ai.chat_json(SYSTEM_PROMPT, orphan_prompt(p, body, names, bundles), 300)
            valid = {b["id"] for b in bundles}
            picks = [s for s in (raw.get("bundles") or []) if isinstance(s, dict) and s.get("id") in valid]
            suggestions.append((p, picks))
        except Exception as e:  # noqa: BLE001
            print(f"  ! suggestion for {p['path']}: {e}", file=sys.stderr)

    print(f"Enriched {len(results)}, failed {len(failures)}, AI calls {ai.calls}, tokens {ai.tokens}")

    if args.report:
        write_report(args.report, results, failures, locked_stale, suggestions, backlog, ai)
    return 0 if results or not todo else 1


def write_report(path, results, failures, locked_stale, suggestions, backlog, ai) -> None:
    lines = ["AI-drafted enrichment for the policy library. Review the text below (or the sidecar files), "
             "edit anything that is wrong directly in this PR, then merge. Merged text is used in LLDs.", ""]
    remaining = backlog - len(results) - len(failures)
    lines.append(f"**This run:** {len(results)} enriched, {len(failures)} failed"
                 + (f", {remaining} still to do (next run)" if remaining > 0 else "") + f". "
                 f"{ai.calls} AI calls, about {ai.tokens:,} tokens.")
    lines.append("")
    lines.append("To stop the AI changing a sidecar you have edited, set `\"locked\": true` in its `enrichment`.")
    lines.append("")
    by_why = {"new": [], "stale": [], "taxonomy": []}
    for r in results:
        by_why[r[1]].append(r)
    titles = {"new": "New", "stale": "Refreshed (the policy changed)", "taxonomy": "Taxonomy only"}
    budget = 55000   # GitHub caps a PR body at 65,536 characters
    for why, rows in by_why.items():
        if not rows:
            continue
        lines += [f"### {titles[why]} ({len(rows)})", ""]
        for p, _, _, fields, overrides in rows:
            entry = f"- **{p['displayName']}** — {fields.get('summary', '')}"
            if overrides:
                entry += "  \n  _Taxonomy set:_ " + ", ".join(f"{k} = {v}" for k, v in overrides.items())
            if sum(len(x) for x in lines) + len(entry) > budget:
                lines.append("- … more in the changed files")
                break
            lines.append(entry)
        lines.append("")
    if locked_stale:
        lines += [f"### Locked but out of date ({len(locked_stale)})", "",
                  "These policies changed after their enrichment was locked. Check the text by hand:", ""]
        lines += [f"- `{p['path']}`" for p in locked_stale[:50]]
        lines.append("")
    if suggestions:
        lines += ["### Suggested Building Blocks for policies in no block", "",
                  "Suggestions only; `bundles.json` is not changed. Add any you agree with.", ""]
        for p, picks in suggestions:
            text = "; ".join(f"`{s['id']}` ({s.get('reason', '').strip()})" for s in picks) or "none suggested"
            lines.append(f"- **{p['displayName']}**: {text}")
        lines.append("")
    if failures:
        lines += [f"### Failed ({len(failures)})", ""]
        lines += [f"- `{p['path']}`: {err[:200]}" for p, err in failures[:30]]
        lines.append("")
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
