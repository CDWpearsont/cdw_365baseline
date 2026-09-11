#!/usr/bin/env python3
"""
Remove superseded duplicate policy files from the repo.

Uses build_catalog.py's own identity derivation, so "duplicate" means exactly what the
catalog build means by duplicateId: policy files that derive the same id (the id ignores
the version). For each duplicate group the highest version is kept and the older files
are removed. Nothing in any tenant is touched.

Dry run by default. Groups where the versions tie or can't be read are reported and
left alone.

Usage (repo root):
    python tools/dedupe_policies.py                 # dry run
    python tools/dedupe_policies.py --apply         # git rm the older files

Note: while the older policies still exist in the reference tenant, the nightly backup
will export them again. Retire them from the tenant (Retire-CdwCisV4.ps1) to make this
permanent.
"""
import argparse
import os
import subprocess
import sys
from collections import defaultdict

sys.dont_write_bytecode = True  # don't leave tools/__pycache__ in the repo
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import build_catalog as bc  # noqa: E402


def version_key(v):
    if not v:
        return None
    try:
        return tuple(int(x) for x in str(v).replace("_", ".").split("."))
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=".")
    ap.add_argument("--policy-dirs", nargs="+", default=["IntuneConfig", "EntraConfig"])
    ap.add_argument("--metadata-dir", default="metadata")
    ap.add_argument("--apply", action="store_true", help="Remove the older files (git rm when in a git repo)")
    args = ap.parse_args()

    root = os.path.abspath(args.root)
    groups = defaultdict(list)
    for rel in bc.discover(root, args.policy_dirs, args.metadata_dir):
        abs_path = os.path.join(root, rel)
        if bc.load_body(abs_path) is None:
            continue
        p = bc.derive(rel, abs_path)
        p = bc.apply_sidecar(p, bc.load_sidecar(bc.sidecar_path(root, rel, args.metadata_dir)))
        groups[p["id"]].append((rel, p.get("version")))

    remove, manual = [], []
    for pid, members in sorted(groups.items()):
        if len(members) < 2:
            continue
        keyed = [(version_key(v), rel, v) for rel, v in members]
        if any(k is None for k, _, _ in keyed):
            manual.append((pid, members, "unreadable version"))
            continue
        keyed.sort(reverse=True)
        if keyed[0][0] == keyed[1][0]:
            manual.append((pid, members, "same version"))
            continue
        keep = keyed[0]
        print(f"\n{pid}\n  keep    v{keep[2]}  {keep[1]}")
        for _, rel, v in keyed[1:]:
            print(f"  remove  v{v}  {rel}")
            side = bc.sidecar_path(root, rel, args.metadata_dir)
            if os.path.exists(side):
                print(f"          note: sidecar {os.path.relpath(side, root)} not removed — merge any enrichment into the kept policy")
            remove.append(rel)

    for pid, members, why in manual:
        print(f"\n{pid}  ({why} — left alone, resolve by hand)")
        for rel, v in members:
            print(f"  v{v}  {rel}")

    print(f"\n{len(remove)} file(s) to remove, {len(manual)} group(s) need manual review.")
    if not remove:
        return 0
    if not args.apply:
        print("Dry run — re-run with --apply to remove them.")
        return 0

    in_git = os.path.isdir(os.path.join(root, ".git"))
    for rel in remove:
        if in_git:
            subprocess.run(["git", "-C", root, "rm", "--quiet", "--", rel], check=True)
        else:
            os.remove(os.path.join(root, rel))
    print(f"Removed {len(remove)} file(s){' (staged with git rm)' if in_git else ''}. Review, commit and push.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
