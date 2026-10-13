#!/usr/bin/env python3
"""Destructive-delete dependency lint.

Salesforce (and this pipeline) will happily let you delete a component that
something else still references — a class deleted out from under a dependent
class, a field still used by a validation rule / flow / Apex, or a picklist
value removed while a record type still assigns it. Under NoTestRun those
deletes are neither blocked at PR validation nor at the real deploy, so broken
metadata lands silently.

This lint runs on the computed delta (during PR validation AND the real deploy)
and FAILS EARLY when a to-be-deleted component is still referenced in the
remaining source tree, with an actionable message telling the developer to
either also delete the dependent or keep the component.

It intentionally only flags HIGH-CONFIDENCE, Salesforce-erroring dependencies.
Cases Salesforce auto-heals (a deleted field on a page layout) are reported as
informational notes, never as failures.
"""
import argparse
import os
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

NS = "{http://soap.sforce.com/2006/04/metadata}"


def _members(xml_path):
    """Return list of (type_name, member) from a package/destructive manifest."""
    out = []
    if not os.path.isfile(xml_path):
        return out
    try:
        root = ET.parse(xml_path).getroot()
    except ET.ParseError:
        return out
    for t in root.findall(f"{NS}types"):
        name_el = t.find(f"{NS}name")
        if name_el is None or not name_el.text:
            continue
        tname = name_el.text.strip()
        for m in t.findall(f"{NS}members"):
            if m.text and m.text.strip():
                out.append((tname, m.text.strip()))
    return out


def _iter_source_files(source_root):
    exts = (".cls", ".trigger", ".xml")
    for dirpath, _dirs, files in os.walk(source_root):
        for f in files:
            if f.endswith(exts):
                yield os.path.join(dirpath, f)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            return fh.read()
    except OSError:
        return ""


_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT = re.compile(r"//[^\n]*")


def _strip_apex_comments(text):
    """Drop // and /* */ comments so a class name mentioned only in a comment
    doesn't count as a real reference."""
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", text))


def _git_show(ref, path):
    try:
        return subprocess.check_output(
            ["git", "show", f"{ref}:{path}"],
            stderr=subprocess.DEVNULL, text=True,
        )
    except subprocess.CalledProcessError:
        return ""


def _picklist_values(xml_text):
    """Return set of picklist value fullNames declared in a field-meta xml."""
    vals = set()
    if not xml_text.strip():
        return vals
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return vals
    for vs in root.iter():
        local = vs.tag.split("}")[-1]
        # Fields declare picklist entries as <value>; record types assign them
        # as <values> (plural). Match both.
        if local in ("value", "values"):
            fn = next((c for c in vs if c.tag.split("}")[-1] == "fullName"), None)
            if fn is not None and fn.text:
                vals.add(fn.text.strip())
    return vals


def check_destructive(dstr_xml, source_root):
    """Flag deleted Apex/fields/objects/recordTypes still referenced in source."""
    findings = []  # (severity, message)
    deletes = _members(dstr_xml)
    if not deletes:
        return findings

    # Build search targets: token -> (kind, human, severity_map_by_filetype)
    apex_names = []      # class / trigger names
    field_targets = []   # (object, field_api)
    object_names = []
    recordtypes = []     # (object, rt)

    for tname, member in deletes:
        if tname in ("ApexClass", "ApexTrigger"):
            apex_names.append(member)
        elif tname == "CustomField" and "." in member:
            obj, fld = member.split(".", 1)
            field_targets.append((obj, fld))
        elif tname == "CustomObject":
            object_names.append(member)
        elif tname == "RecordType" and "." in member:
            obj, rt = member.split(".", 1)
            recordtypes.append((obj, rt))

    if not any([apex_names, field_targets, object_names, recordtypes]):
        return findings

    apex_re = {n: re.compile(r"\b" + re.escape(n) + r"\b") for n in apex_names}
    field_re = {f: re.compile(r"\b" + re.escape(f) + r"\b") for _o, f in field_targets}

    for path in _iter_source_files(source_root):
        rel = os.path.relpath(path, source_root)
        low = rel.lower()
        text = _read(path)
        if not text:
            continue
        is_apex = path.endswith((".cls", ".trigger"))
        if is_apex:
            text = _strip_apex_comments(text)
        is_layout = ".layout-meta.xml" in low or "/layouts/" in low
        is_vr = ".validationrule-meta.xml" in low or "/validationrules/" in low
        is_flow = ".flow-meta.xml" in low or "/flows/" in low
        is_field = ".field-meta.xml" in low
        is_permset = ".permissionset-meta.xml" in low or "/permissionsets/" in low
        is_profile = ".profile-meta.xml" in low or "/profiles/" in low

        # Deleted Apex referenced by remaining Apex → hard compile break.
        if is_apex:
            for name, rx in apex_re.items():
                if rx.search(text):
                    findings.append((
                        "ERROR",
                        f"Apex '{name}' is being DELETED but is still referenced by {rel}. "
                        f"Delete/rename the reference (or keep '{name}')."
                    ))

        # Deleted field referenced by an erroring metadata type.
        for (obj, fld), rx in ((t, field_re[t[1]]) for t in field_targets):
            if not rx.search(text):
                continue
            if is_apex:
                sev, why = "ERROR", "referenced by Apex (compile break)"
            elif is_vr:
                sev, why = "ERROR", "used by a validation rule"
            elif is_flow:
                sev, why = "ERROR", "used by a flow"
            elif is_field and rel.rsplit("/", 1)[-1] != f"{fld}.field-meta.xml":
                sev, why = "ERROR", "used by another field's formula/definition"
            elif is_permset or is_profile:
                sev, why = "WARN", "still granted FLS (permission set / profile)"
            elif is_layout:
                sev, why = "NOTE", "still on a page layout (Salesforce auto-removes)"
            else:
                sev, why = "WARN", "still referenced"
            findings.append((
                sev,
                f"Field '{obj}.{fld}' is being DELETED but is {why} in {rel}."
            ))

        # Deleted record type referenced by profile / apex / flow.
        for obj, rt in recordtypes:
            token = f"{obj}.{rt}"
            if token in text and (is_profile or is_apex or is_flow):
                findings.append((
                    "ERROR",
                    f"Record type '{token}' is being DELETED but is still referenced in {rel}."
                ))

        # Deleted object referenced by remaining Apex.
        if is_apex:
            for obj in object_names:
                if re.search(r"\b" + re.escape(obj) + r"\b", text):
                    findings.append((
                        "ERROR",
                        f"Object '{obj}' is being DELETED but is still referenced by {rel}."
                    ))

    return findings


def check_picklist_values(pkg_xml, source_root, baseline):
    """Flag picklist VALUES removed from a field while a record type still uses them.

    This is the classic 'deleted the picklist value but forgot the record type'
    case. The field itself is modified (not destructively deleted), so it lives
    in the additive package; we diff its value set against the baseline branch to
    find removed values, then look for record types on the same object that still
    assign them.
    """
    findings = []
    if not baseline:
        return findings
    for tname, member in _members(pkg_xml):
        if tname != "CustomField" or "." not in member:
            continue
        obj, fld = member.split(".", 1)
        rel = f"{source_root}/objects/{obj}/fields/{fld}.field-meta.xml"
        head_vals = _picklist_values(_read(rel))
        base_vals = _picklist_values(_git_show(baseline, rel))
        removed = base_vals - head_vals
        if not removed:
            continue
        rt_dir = f"{source_root}/objects/{obj}/recordTypes"
        if not os.path.isdir(rt_dir):
            continue
        for rtf in os.listdir(rt_dir):
            if not rtf.endswith(".recordType-meta.xml"):
                continue
            rt_text = _read(os.path.join(rt_dir, rtf))
            rt_vals = _picklist_values(rt_text)
            still = removed & rt_vals
            if still:
                rt_name = rtf.replace(".recordType-meta.xml", "")
                findings.append((
                    "ERROR",
                    f"Picklist value(s) {sorted(still)} removed from '{obj}.{fld}' "
                    f"but record type '{obj}.{rt_name}' still assigns them. "
                    f"Remove the value(s) from the record type too, or keep them on the field."
                ))
    return findings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".sgd", help="sfdx-git-delta output dir")
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--baseline", default="", help="git ref of the target branch (for value diff)")
    args = ap.parse_args()

    dstr = os.path.join(args.dir, "destructiveChanges", "destructiveChanges.xml")
    pkg = os.path.join(args.dir, "package", "package.xml")
    src = args.source_root.rstrip("/")

    findings = []
    findings += check_destructive(dstr, src)
    findings += check_picklist_values(pkg, src, args.baseline)

    if not findings:
        print("Dependency lint: no orphaned references from deleted/changed components. ✅")
        return 0

    errors = [m for s, m in findings if s == "ERROR"]
    warns = [m for s, m in findings if s == "WARN"]
    notes = [m for s, m in findings if s == "NOTE"]

    for m in errors:
        print(f"::error::[dependency-lint] {m}")
    for m in warns:
        print(f"::warning::[dependency-lint] {m}")
    for m in notes:
        print(f"[dependency-lint][note] {m}")

    if errors:
        print(f"\nDependency lint FAILED: {len(errors)} orphaned reference(s) to deleted/changed metadata.")
        return 1
    print("\nDependency lint: only warnings/notes — passing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
