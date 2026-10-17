#!/usr/bin/env python3
"""Finish destructiveChanges.xml against the target org before a deploy.

With --merge-from, deletions from a second destructiveChanges.xml are added
first. The deploy action uses it for changes built on top of an environment
branch (a merge, or a rollback cut from that branch): what the change removed
from the branch is deleted from the org too, even when it never reached main.

Then every deletion the org no longer has is dropped.

Deleting a component that isn't in the org fails a deploy with "No ApexClass
named: X found" (a warning, which fails unless --ignore-warnings). That is
common: environment orgs lag behind each other, a component was removed by
hand, or a validated release was already applied. The intent of the delete is
already met, so the member is dropped and listed instead of failing the run.

Each metadata type is checked with `sf org list metadata`. A type the org can't
list, or a folder-based type (reports, dashboards, documents, email templates
need a folder to list), keeps all its members: only deletions proven to be
absent are dropped.

Usage
  destructive_prune.py --destructive FILE --org ALIAS [--merge-from FILE] [--notes FILE]
Exit 0; prints how many members were dropped. The file is rewritten in place.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from pathlib import Path
from typing import Callable

NS = "http://soap.sforce.com/2006/04/metadata"
FOLDER_TYPES = {"Report", "Dashboard", "Document", "EmailTemplate"}

Lister = Callable[[str], "set[str] | None"]


def sf_lister(org: str) -> Lister:
    sf = shutil.which("sf") or "sf"  # sf.cmd on Windows

    def list_members(md_type: str) -> set[str] | None:
        proc = subprocess.run(
            [sf, "org", "list", "metadata", "--metadata-type", md_type, "--target-org", org, "--json"],
            capture_output=True, text=True, encoding="utf-8",
        )
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return None
        if proc.returncode != 0 or data.get("status", 1) != 0:
            return None
        result = data.get("result") or []
        if isinstance(result, dict):
            result = [result]
        return {item["fullName"] for item in result if isinstance(item, dict) and "fullName" in item}

    return list_members


def read_package(path: Path) -> tuple[dict[str, list[str]], str | None]:
    root = ET.parse(path).getroot()
    types: dict[str, list[str]] = {}
    for t in root.findall(f"{{{NS}}}types"):
        name = t.findtext(f"{{{NS}}}name")
        if name:
            types.setdefault(name, []).extend(m.text for m in t.findall(f"{{{NS}}}members") if m.text)
    return types, root.findtext(f"{{{NS}}}version")


def write_package(path: Path, types: dict[str, list[str]], version: str | None) -> None:
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', f'<Package xmlns="{NS}">']
    for name in sorted(types):
        lines.append("    <types>")
        lines += [f"        <members>{escape(m)}</members>" for m in sorted(types[name])]
        lines += [f"        <name>{name}</name>", "    </types>"]
    if version:
        lines.append(f"    <version>{version}</version>")
    lines.append("</Package>")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def merge(path: Path, extra: Path) -> list[tuple[str, str]]:
    """Add the members of `extra` to `path` (created if missing); return the (type, member) pairs added."""
    extra_types, extra_version = read_package(extra)
    types, version = read_package(path) if path.is_file() else ({}, None)
    added: list[tuple[str, str]] = []
    for md_type, members in extra_types.items():
        have = types.setdefault(md_type, [])
        for m in members:
            if m not in have:
                have.append(m)
                added.append((md_type, m))
    if added or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        write_package(path, {t: ms for t, ms in types.items() if ms}, version or extra_version)
    return added


def prune(path: Path, list_members: Lister) -> list[tuple[str, str]]:
    """Rewrite `path` without members the org doesn't have; return the dropped (type, member) pairs."""
    types, version = read_package(path)
    dropped: list[tuple[str, str]] = []
    kept: dict[str, list[str]] = {}
    for md_type, members in types.items():
        existing = None if md_type in FOLDER_TYPES else list_members(md_type)
        if existing is None:
            kept[md_type] = members
            continue
        for m in members:
            if m in existing:
                kept.setdefault(md_type, []).append(m)
            else:
                dropped.append((md_type, m))
    if dropped:
        write_package(path, kept, version)
    return dropped


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--destructive", required=True)
    ap.add_argument("--org", required=True)
    ap.add_argument("--merge-from", help="another destructiveChanges.xml whose deletions are added first")
    ap.add_argument("--notes", help="append a Markdown note about added and dropped deletions here")
    args = ap.parse_args(argv)

    path = Path(args.destructive)
    if args.merge_from and Path(args.merge_from).is_file():
        added = merge(path, Path(args.merge_from))
        for md_type, member in added:
            print(f"::notice title=Also deleted::{md_type} {member} was removed by this change; deleting it from {args.org}")
        if added and args.notes:
            with open(args.notes, "a", encoding="utf-8") as fh:
                fh.write("### Also deleted (removed by this change from the environment branch)\n\n")
                fh.writelines(f"- **{t}**: `{m}`\n" for t, m in added)
                fh.write("\n")
    if not path.is_file():
        return 0
    dropped = prune(path, sf_lister(args.org))
    for md_type, member in dropped:
        print(f"::notice title=Already deleted::{md_type} {member} is not in org {args.org}; skipping its delete")
    print(f"destructive prune: {len(dropped)} deletion(s) already done in {args.org}")
    if dropped and args.notes:
        with open(args.notes, "a", encoding="utf-8") as fh:
            fh.write(f"### Deletions skipped (already gone from `{args.org}`)\n\n")
            fh.writelines(f"- **{t}**: `{m}`\n" for t, m in dropped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
