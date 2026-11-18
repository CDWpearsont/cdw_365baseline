#!/usr/bin/env python3
"""Builds site/setting-names.json: Settings Catalog display names for the
setting definitions and choice options used by the library's policies.

Source: settings.ndjson from the settings-catalog branch (weekly snapshot of
the reference tenant). Used by the LLD so setting names and choice values read
as they do in the Intune portal.

    python tools/build_setting_names.py --catalog settings.ndjson --out site/setting-names.json
"""
import argparse, glob, json, os

def walk(node, defs, opts):
    if isinstance(node, dict):
        d = node.get('settingDefinitionId')
        if isinstance(d, str): defs.add(d)
        cv = node.get('choiceSettingValue')
        if isinstance(cv, dict) and isinstance(cv.get('value'), str): opts.add(cv['value'])
        for c in node.get('choiceSettingCollectionValue') or []:
            if isinstance(c, dict) and isinstance(c.get('value'), str): opts.add(c['value'])
        for v in node.values(): walk(v, defs, opts)
    elif isinstance(node, list):
        for v in node: walk(v, defs, opts)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--catalog', required=True)
    ap.add_argument('--policies', default='IntuneConfig')
    ap.add_argument('--out', default='site/setting-names.json')
    a = ap.parse_args()

    defs, opts = set(), set()
    for f in glob.glob(os.path.join(a.policies, '**', '*.json'), recursive=True):
        try:
            with open(f, encoding='utf-8-sig') as fh: walk(json.load(fh), defs, opts)
        except (ValueError, OSError):
            pass
    lower_defs = {d.lower(): d for d in defs}
    lower_opts = {o.lower(): o for o in opts}

    # Synthetic group wrappers have no useful name; the site's own naming is kept.
    SKIP = {'Top Level Setting Group Collection'}
    names, paths, options = {}, {}, {}
    with open(a.catalog, encoding='utf-8') as fh:
        for line in fh:
            if not line.strip(): continue
            s = json.loads(line)
            sid = (s.get('id') or '').lower()
            # ADMX drop-down children have no display name of their own in the
            # catalog (the portal shows the list under its parent setting).
            if sid in lower_defs and not s.get('displayName') and s.get('options'):
                names[lower_defs[sid]] = 'Option'
            if sid in lower_defs and s.get('displayName') and s['displayName'].strip() not in SKIP:
                names[lower_defs[sid]] = s['displayName'].strip()
                path = next((k for k in s.get('keywords') or [] if k.startswith('\\')), '')
                paths[lower_defs[sid]] = path.rstrip('\\').split('\\')[-1] if path else ''
            # Every option of a setting the library uses (not only the option it
            # picks), so an LLD edited to another option can be deployed.
            for o in s.get('options') or []:
                oid = (o.get('itemId') or '').lower()
                if (oid in lower_opts or sid in lower_defs) and o.get('displayName'):
                    name = o['displayName'].strip()
                    # Boolean options read as the rest of the document does.
                    options[lower_opts.get(oid, o['itemId'])] = {'true': 'Enabled', 'false': 'Disabled'}.get(name.lower(), name)

    # ADMX settings repeated per area (e.g. the event log retention setting for
    # Application, Security, Setup and System) share one display name: add the
    # area so each row is distinct.
    by_name = {}
    for d, n in names.items(): by_name.setdefault(n, []).append(d)
    for n, ids in by_name.items():
        leaves = {paths.get(d) for d in ids}
        if len(ids) > 1 and len(leaves) == len(ids) and all(leaves):
            for d in ids: names[d] = f'{n} ({paths[d]})'

    out = {'names': dict(sorted(names.items())), 'options': dict(sorted(options.items()))}
    with open(a.out, 'w', encoding='utf-8', newline='\n') as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(',', ':'))
        fh.write('\n')
    print(f'{len(names)}/{len(defs)} setting names, {len(options)} options ({len(opts)} used) -> {a.out}')

if __name__ == '__main__':
    main()
