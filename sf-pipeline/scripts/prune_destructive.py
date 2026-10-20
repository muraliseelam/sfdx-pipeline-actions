#!/usr/bin/env python3
"""prune_destructive.py — skip deletions of components the target org doesn't have.

A destructive delta lists what the branch deleted relative to its baseline. When
the target org has already lost one of those components (orgs drift, a manual
delete, an earlier partial deploy, a component that never reached that org),
Salesforce fails the WHOLE deploy with "No <Type> named: <member> found".
Deleting something that is already gone changes nothing, so this script asks the
org which members of each type exist (`sf org list metadata`) and removes the
absent ones from destructiveChanges.xml before the deploy.

It only removes a deletion it has positively confirmed is absent:
  * a type whose listing fails is left untouched
  * folder-based types (Report, Dashboard, Document, EmailTemplate) and types the
    listing does not enumerate (StandardValueSet, Settings) are left untouched
  * names are compared URL-decoded and case-insensitively, so an encoding or
    case difference keeps the deletion instead of dropping it

Writes the skipped deletions to .deploy/skipped-deletions.json
([{"type": ..., "member": ...}]) for the deploy report, prints one ::warning::
per skipped member and adds a job-summary block.

Usage:
  prune_destructive.py --target-org int [--dir .sgd] [--api-version 66.0]
"""
import argparse
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from urllib.parse import unquote

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)

# Deletions of these types are always kept: folder-based types need one listing
# per folder, and the listing does not enumerate the others.
UNLISTED = {
    "Report", "Dashboard", "Document", "EmailTemplate",
    "ReportFolder", "DashboardFolder", "DocumentFolder", "EmailFolder",
    "EmailTemplateFolder", "StandardValueSet", "Settings",
}


def q(tag):
    return f"{{{NS}}}{tag}"


def norm(name):
    """Compare names the way Salesforce matches them: decoded, case-insensitive."""
    return unquote(name or "").strip().casefold()


def names_from_listing(result):
    """Normalized fullNames from an `sf org list metadata --json` result, or None
    when the result is not the expected list."""
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        return None
    names = set()
    for item in result:
        if not isinstance(item, dict):
            continue
        full = item.get("fullName") or ""
        names.add(norm(full))
        ns = item.get("namespacePrefix") or ""
        if ns and not full.startswith(ns + "__"):
            names.add(norm(f"{ns}__{full}"))
    return names


def list_org(org, mtype, api_version=""):
    """Names of every `mtype` component in `org`; None when they can't be listed."""
    cmd = ["sf", "org", "list", "metadata", "--metadata-type", mtype,
           "--target-org", org, "--json"]
    if api_version:
        cmd += ["--api-version", api_version]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"::warning::Could not list {mtype} in '{org}' ({exc}); keeping its deletions.")
        return None
    try:
        data = json.loads(proc.stdout or "")
    except ValueError:
        data = None
    names = None
    if proc.returncode == 0 and isinstance(data, dict) and data.get("status") == 0:
        names = names_from_listing(data.get("result"))
    if names is None:
        why = data.get("message") if isinstance(data, dict) else ""
        why = " ".join(str(why or "").split())  # one line: an annotation ends at a newline
        print(f"::warning::Could not list {mtype} in '{org}'"
              f"{': ' + why if why else ''}; keeping its deletions.")
    return names


def prune(path, lister):
    """Remove from the destructive manifest at `path` every member `lister` says
    the org doesn't have. `lister(type)` returns a set of normalized names, or
    None when unknown. Rewrites the file only when something was removed.
    Returns the skipped deletions as [(type, member)]."""
    tree = ET.parse(path)
    root = tree.getroot()
    skipped = []
    for types in list(root.findall(q("types"))):
        name_el = types.find(q("name"))
        mtype = (name_el.text or "").strip() if name_el is not None else ""
        members = types.findall(q("members"))
        if not mtype or not members or mtype in UNLISTED:
            continue
        if any((m.text or "").strip() == "*" for m in members):
            continue
        present = lister(mtype)
        if present is None:
            continue
        for m in members:
            member = (m.text or "").strip()
            if norm(member) not in present:
                types.remove(m)
                skipped.append((mtype, member))
        if not types.findall(q("members")):
            root.remove(types)
    if skipped:
        ET.indent(tree, space="    ")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
            fh.write(ET.tostring(root, encoding="unicode") + "\n")
        os.replace(tmp, path)
    return skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".sgd", help="sfdx-git-delta output directory")
    ap.add_argument("--target-org", required=True, help="org alias the deploy targets")
    ap.add_argument("--api-version", default="")
    ap.add_argument("--out", default=".deploy/skipped-deletions.json")
    args = ap.parse_args()

    path = os.path.join(args.dir, "destructiveChanges", "destructiveChanges.xml")
    skipped = []
    has_deletions = os.path.isfile(path) and "<members>" in open(path, encoding="utf-8").read()
    if has_deletions:
        try:
            skipped = prune(path, lambda t: list_org(args.target_org, t, args.api_version))
        except ET.ParseError as exc:
            print(f"::warning::Could not read {path} ({exc}); deploying its deletions unchanged.")

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump([{"type": t, "member": m} for t, m in skipped], fh, indent=2)

    if not has_deletions:
        print("No deletions in the delta.")
        return 0
    if not skipped:
        print(f"Every deletion in the delta is still in '{args.target_org}' (or could not be checked) — none skipped.")
        return 0
    for t, m in skipped:
        print(f"::warning::Skipped deleting {t} '{m}': it is not in the '{args.target_org}' org "
              "(already deleted, or never deployed there).")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"\n### ⏭️ Deletions skipped — not in `{args.target_org}`\n\n"
                     "| Type | Component |\n|---|---|\n")
            for t, m in skipped:
                fh.write(f"| `{t}` | {m.replace('|', chr(92) + '|')} |\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
