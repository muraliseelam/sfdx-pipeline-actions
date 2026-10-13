#!/usr/bin/env python3
"""profiles.py — full vs incremental Profile / Permission Set deployment (REQ-006).

Salesforce Profiles and Permission Sets are "container" metadata: a single file
aggregates hundreds of independent permission entries (field-level security,
object permissions, record-type visibilities, Apex/page/tab/app access, user
permissions ...). Deploying the *whole* file is slow, risky, and can fail when
the target org does not have every referenced component.

Modes (driven by `profiles_mode` in pipeline.config.yml):

  full         Deploy the complete Profile/PermissionSet from source. Use for
               baseline deploys where every referenced component already exists
               in the target org.

  incremental  TRUE git self-diff (Copado-style). For every Profile/PermissionSet
               in THIS delta we compare the file at the prod baseline
               (`--baseline`, default origin/main = last successful Prod) against
               the PR/working-tree version:

                 * NEW file (absent on the baseline)  -> deploy it WHOLE
                   ("initial deploy = full").
                 * MODIFIED file                       -> rewrite it in place so
                   it keeps ONLY the permission entries that were ADDED or
                   CHANGED vs the baseline. Everything unchanged is stripped, so
                   a single flipped FLS / record-type / class-access line deploys
                   exactly that one entry -- independent of whether the
                   referenced component itself is in the delta.

               Profile-level singletons (custom, userLicense, description, ...)
               are kept only when they changed.

               Decomposed Permission Sets (sfdx-project.json
               `sourceBehaviorOptions: decomposePermissionSetBeta2`) are stored
               as a directory of per-object / per-class subfiles. Each subfile
               is self-diffed the same way; subfiles with no changed entries are
               deleted so the CLI recomposes a component carrying only the
               added/changed entries. This yields per-object git granularity
               (far fewer permission-set merge conflicts) AND per-entry deploys.

The deploy step resolves the manifest from the sfdx source directory, so the
in-place rewrite here is exactly what gets deployed. The CI checkout is
ephemeral, so mutating the working tree is safe.

Usage:
  profiles.py --package .sgd/package/package.xml --source-root force-app \
              --mode incremental --baseline origin/main [--summary-out FILE]
"""
import argparse
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

NS = "http://soap.sforce.com/2006/04/metadata"
ET.register_namespace("", NS)


def q(tag):
    return f"{{{NS}}}{tag}"


def localname(el):
    return el.tag.split("}", 1)[-1]


# Child-permission element tag -> identifier child tag that makes it unique.
# Anything with an identifier is treated as a "keyed" permission entry that we
# diff individually. Elements not listed and lacking a known id child are
# treated as profile-level singletons (kept only if they changed).
# Top-level singleton fields the platform REQUIRES on every deploy of a given
# metadata type, even when they are unchanged vs the baseline. A trimmed
# PermissionSet with no <label> is rejected ("Required field is missing: label"),
# so these are always retained (but not counted as "changed" entries).
REQUIRED_SINGLETONS = {
    "PermissionSet": {"label"},
}


ID_TAG = {
    "fieldPermissions": "field",
    "objectPermissions": "object",
    "classAccesses": "apexClass",
    "pageAccesses": "apexPage",
    "tabVisibilities": "tab",
    "tabSettings": "tab",
    "recordTypeVisibilities": "recordType",
    "applicationVisibilities": "application",
    "layoutAssignments": "layout",
    "customPermissions": "name",
    "flowAccesses": "flow",
    "externalDataSourceAccesses": "externalDataSource",
    "customMetadataTypeAccesses": "name",
    "customSettingAccesses": "name",
    "userPermissions": "name",
    "categoryGroupVisibilities": "dataCategoryGroup",
}


def parse_package(path):
    """Return {typeName: set(members)} from a package.xml."""
    out = {}
    if not os.path.isfile(path):
        return out
    tree = ET.parse(path)
    for t in tree.getroot().findall(q("types")):
        name_el = t.find(q("name"))
        if name_el is None or not name_el.text:
            continue
        name = name_el.text.strip()
        members = {m.text.strip() for m in t.findall(q("members")) if m.text}
        out.setdefault(name, set()).update(members)
    return out


def meta_paths(source_root, meta_type):
    sub = "profiles" if meta_type == "Profile" else "permissionsets"
    ext = "profile" if meta_type == "Profile" else "permissionset"
    return sub, ext


def source_file(source_root, meta_type, member):
    sub, ext = meta_paths(source_root, meta_type)
    return os.path.join(source_root, "main", "default", sub, f"{member}.{ext}-meta.xml")


def resolve_target(source_root, meta_type, member):
    """Locate a Profile/PermissionSet in the source tree.

    Returns (kind, path):
      ("dir",  <dir>)   decomposed component (sourceBehaviorOptions), e.g.
                        permissionsets/<member>/ with per-object/-class subfiles.
      ("file", <file>)  monolithic <member>.<ext>-meta.xml.
      (None,   None)    not found.
    Profiles are never decomposed by the CLI, so they always resolve to a file.
    """
    sub, ext = meta_paths(source_root, meta_type)
    base = os.path.join(source_root, "main", "default", sub)
    flat = os.path.join(base, f"{member}.{ext}-meta.xml")
    decomposed_dir = os.path.join(base, member)
    if os.path.isdir(decomposed_dir):
        return ("dir", decomposed_dir)
    if os.path.isfile(flat):
        return ("file", flat)
    return (None, None)


def git_show(ref, path):
    """Return the file contents at <ref>:<path>, or None if it does not exist."""
    try:
        out = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    if out.returncode != 0:
        return None
    return out.stdout


def normalize(el):
    """Canonical string for an element, ignoring incidental whitespace so two
    logically-identical entries compare equal."""
    clone = ET.fromstring(ET.tostring(el, encoding="unicode"))
    for node in clone.iter():
        if node.text and not node.text.strip():
            node.text = None
        if node.tail and not node.tail.strip():
            node.tail = None
        if node.text:
            node.text = node.text.strip()
    return ET.tostring(clone, encoding="unicode")


def child_by_local(el, tag):
    """Find a direct child by local name, ignoring XML namespace. Decomposed
    subfiles are emitted WITHOUT the metadata namespace, while the parent file
    carries it, so namespaced lookups must not be assumed."""
    for c in el:
        if localname(c) == tag:
            return c
    return None


def entry_key(child):
    """Stable identity for a permission entry: (tag, identifier). For keyed
    entries the identifier is the id child's text; otherwise the tag alone
    (profile-level singleton)."""
    tag = localname(child)
    id_tag = ID_TAG.get(tag)
    if id_tag:
        id_el = child_by_local(child, id_tag)
        ident = (id_el.text or "").strip() if id_el is not None else ""
        return (tag, ident)
    return (tag, None)


def index_children(root):
    """Map entry_key -> normalized_string for every child."""
    idx = {}
    for child in list(root):
        idx[entry_key(child)] = normalize(child)
    return idx


def trim_children(head_root, base_root, required):
    """Remove children of head_root that are unchanged vs base_root.

    Keeps children that are added or changed, plus unchanged platform-required
    singletons (e.g. PermissionSet <label>). Mutates head_root in place and
    returns (added, changed, dropped, kept_required, details).
    """
    base_idx = index_children(base_root)
    added, changed, dropped, kept_required, details = 0, 0, 0, 0, []
    for child in list(head_root):
        key = entry_key(child)
        head_norm = normalize(child)
        base_norm = base_idx.get(key)
        if base_norm is None:
            added += 1
            details.append(f"+{key[0]}:{key[1] or ''}")
        elif base_norm != head_norm:
            changed += 1
            details.append(f"~{key[0]}:{key[1] or ''}")
        elif key[1] is None and key[0] in required:
            kept_required += 1
        else:
            head_root.remove(child)
            dropped += 1
    return added, changed, dropped, kept_required, details


def write_xml(tree, path):
    ET.indent(tree, space="    ")
    body = ET.tostring(tree.getroot(), encoding="unicode")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        fh.write(body)
        if not body.endswith("\n"):
            fh.write("\n")


def has_entries(root):
    """True if the element has at least one child (a permission/setting entry)."""
    return len(list(root)) > 0


def selfdiff_file(path, baseline_ref):
    """Rewrite a monolithic Profile/PermissionSet in place, keeping only entries
    added/changed vs baseline.

    Returns a dict describing the outcome:
      {"status": "full"|"trim", "added": n, "changed": n, "kept": n,
       "dropped": n, "details": [..]}
    """
    base_src = git_show(baseline_ref, path)
    if base_src is None:
        # New file on this branch -- initial deploy is full.
        return {"status": "full", "added": 0, "changed": 0,
                "kept": 0, "dropped": 0, "details": []}

    head_tree = ET.parse(path)
    head_root = head_tree.getroot()
    base_root = ET.fromstring(base_src)
    required = REQUIRED_SINGLETONS.get(localname(head_root), set())

    added, changed, dropped, _kept_req, details = trim_children(
        head_root, base_root, required)

    kept = added + changed
    write_xml(head_tree, path)
    return {"status": "trim", "added": added, "changed": changed,
            "kept": kept, "dropped": dropped, "details": details}


def selfdiff_decomposed(dir_path, source_root, baseline_ref):
    """Trim a DECOMPOSED Profile/PermissionSet directory in place.

    Each decomposed subfile (parent <name>.<ext>-meta.xml, <name>.classAccess-
    meta.xml, objectSettings/<Object>.objectSettings-meta.xml, ...) is a small
    XML fragment. We self-diff each subfile vs the same path at `baseline_ref`:
      * subfile absent on baseline           -> NEW, keep whole (initial).
      * subfile present                      -> drop unchanged entries.
    A subfile that ends up with no permission entries is deleted so it is
    excluded when the CLI recomposes the component for --manifest deploy. The
    parent file is always retained (its <label> is required for a valid
    PermissionSet), but its unchanged singletons are still stripped.

    Because Salesforce deploys Profile/PermissionSet entries additively, the
    recomposed component carries only the added/changed entries -- exactly the
    Copado-style per-entry deploy, now with per-object/-class git granularity.

    Returns the same stats dict shape as selfdiff_file.
    """
    subfiles = sorted(
        os.path.join(root, f)
        for root, _dirs, files in os.walk(dir_path)
        for f in files if f.endswith("-meta.xml")
    )
    if not subfiles:
        return {"status": "full", "added": 0, "changed": 0,
                "kept": 0, "dropped": 0, "details": []}

    parent_suffix = ".permissionset-meta.xml"
    total_added = total_changed = total_dropped = 0
    details = []
    all_new = True

    for sub in subfiles:
        base_src = git_show(baseline_ref, sub)
        is_parent = sub.endswith(parent_suffix)
        if base_src is None:
            # New subfile on this branch -- keep it whole (initial).
            continue
        all_new = False
        head_tree = ET.parse(sub)
        head_root = head_tree.getroot()
        base_root = ET.fromstring(base_src)
        required = REQUIRED_SINGLETONS.get(localname(head_root), set()) if is_parent else set()
        added, changed, dropped, kept_required, det = trim_children(
            head_root, base_root, required)
        total_added += added
        total_changed += changed
        total_dropped += dropped
        details.extend(det)
        if not is_parent and not has_entries(head_root):
            # Nothing changed in this fragment -> exclude it from recomposition.
            os.remove(sub)
            _prune_empty_dirs(os.path.dirname(sub), dir_path)
        else:
            write_xml(head_tree, sub)

    if all_new:
        return {"status": "full", "added": 0, "changed": 0,
                "kept": 0, "dropped": 0, "details": []}
    kept = total_added + total_changed
    return {"status": "trim", "added": total_added, "changed": total_changed,
            "kept": kept, "dropped": total_dropped, "details": details}


def _prune_empty_dirs(path, stop_at):
    """Remove now-empty intermediate dirs (e.g. objectSettings/) up to stop_at."""
    while path and os.path.abspath(path) != os.path.abspath(stop_at):
        try:
            if not os.listdir(path):
                os.rmdir(path)
            else:
                break
        except OSError:
            break
        path = os.path.dirname(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", default=".sgd/package/package.xml")
    ap.add_argument("--source-root", default="force-app")
    ap.add_argument("--mode", default="full", choices=["full", "incremental"])
    ap.add_argument("--baseline", default="origin/main",
                    help="git ref for the last successful Prod baseline")
    ap.add_argument("--summary-out", default="")
    args = ap.parse_args()

    pkg = parse_package(args.package)
    targets = []
    for mt in ("Profile", "PermissionSet"):
        for m in sorted(pkg.get(mt, set())):
            targets.append((mt, m))

    lines = []
    if not targets:
        print("No Profiles/Permission Sets in the delta -- nothing to do.")
    elif args.mode == "full":
        names = ", ".join(f"{t}:{m}" for t, m in targets)
        print(f"profiles_mode=full -- deploying COMPLETE {names} from source.")
        lines.append(f"**Profiles/Permission Sets ({len(targets)})** deployed in **full** mode.")
    else:
        lines.append(f"**Profiles/Permission Sets** deployed in **incremental** mode "
                     f"(git self-diff vs `{args.baseline}` -- only added/changed entries):")
        for mt, m in targets:
            kind, path = resolve_target(args.source_root, mt, m)
            if kind is None:
                print(f"::warning::{mt} {m} listed in delta but source not found "
                      f"under {args.source_root} (checked file and decomposed dir)")
                lines.append(f"- `{mt}:{m}` -- source not found, left as-is")
                continue
            if kind == "dir":
                res = selfdiff_decomposed(path, args.source_root, args.baseline)
                shape = "decomposed"
            else:
                res = selfdiff_file(path, args.baseline)
                shape = "monolithic"
            if res["status"] == "full":
                print(f"{mt} {m}: NEW on this branch -- deploying in FULL (initial).")
                lines.append(f"- `{mt}:{m}` -- **new**, deployed in full (initial)")
            else:
                detail = ", ".join(res["details"]) if res["details"] else "none"
                unit = "y" if res["kept"] == 1 else "ies"
                print(f"{mt} {m} ({shape}): kept {res['kept']} changed entr{unit} "
                      f"(+{res['added']} added, ~{res['changed']} changed), "
                      f"dropped {res['dropped']} unchanged: {detail}")
                lines.append(
                    f"- `{mt}:{m}` ({shape}) -- kept **{res['kept']}** changed "
                    f"(+{res['added']} / ~{res['changed']}), "
                    f"stripped {res['dropped']} unchanged")

    if args.summary_out and lines:
        os.makedirs(os.path.dirname(args.summary_out) or ".", exist_ok=True)
        with open(args.summary_out, "w", encoding="utf-8") as fh:
            fh.write("### Profiles / Permission Sets\n\n" + "\n".join(lines) + "\n")
    gh = os.environ.get("GITHUB_STEP_SUMMARY")
    if gh and lines:
        with open(gh, "a", encoding="utf-8") as fh:
            fh.write("\n### Profiles / Permission Sets\n\n" + "\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
