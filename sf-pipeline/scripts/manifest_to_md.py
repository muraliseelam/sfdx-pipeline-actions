#!/usr/bin/env python3
"""manifest_to_md.py — render a deployment manifest as a Markdown table.

Reads a sfdx-git-delta output directory (package.xml + destructiveChanges.xml) and
prints a Markdown summary listing every component that will be deployed or deleted.

The output is written to stdout and, when running in GitHub Actions, appended to the
job summary ($GITHUB_STEP_SUMMARY). It is also saved to a file for artifact upload.

Usage:
  python3 scripts/ci/manifest_to_md.py --dir .sgd --title "Int deploy" \
      [--out manifest.md]
"""
import argparse
import os
import sys
import xml.etree.ElementTree as ET

NS = {"sf": "http://soap.sforce.com/2006/04/metadata"}


def parse_package(path):
    """Return list of (metadata_type, member) tuples from a package.xml."""
    rows = []
    if not path or not os.path.isfile(path):
        return rows
    try:
        tree = ET.parse(path)
    except ET.ParseError:
        return rows
    root = tree.getroot()
    for types in root.findall("sf:types", NS):
        name_el = types.find("sf:name", NS)
        type_name = name_el.text.strip() if name_el is not None and name_el.text else "(unknown)"
        for member in types.findall("sf:members", NS):
            value = member.text.strip() if member.text else "*"
            rows.append((type_name, value))
    return sorted(rows)


def render_table(rows, action):
    if not rows:
        return f"_No components to {action}._\n"
    out = [f"| # | Type | Component |", "|---|------|-----------|"]
    for i, (t, m) in enumerate(rows, start=1):
        out.append(f"| {i} | `{t}` | {m} |")
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".sgd", help="sfdx-git-delta output directory")
    ap.add_argument("--title", default="Deployment", help="heading for the summary")
    ap.add_argument("--out", default=None, help="optional file to also write the markdown to")
    args = ap.parse_args()

    deploy_pkg = os.path.join(args.dir, "package", "package.xml")
    destroy_pkg = os.path.join(args.dir, "destructiveChanges", "destructiveChanges.xml")

    deploy_rows = parse_package(deploy_pkg)
    destroy_rows = parse_package(destroy_pkg)

    md = []
    md.append(f"## 📦 {args.title}\n")
    md.append(f"**{len(deploy_rows)}** component(s) to deploy · **{len(destroy_rows)}** to delete\n")
    md.append("\n### Components to deploy\n")
    md.append(render_table(deploy_rows, "deploy"))
    if destroy_rows:
        md.append("\n### Components to delete (destructive)\n")
        md.append(render_table(destroy_rows, "delete"))
    text = "\n".join(md)

    sys.stdout.write(text + "\n")

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")


if __name__ == "__main__":
    main()
