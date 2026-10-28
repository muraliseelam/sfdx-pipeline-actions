#!/usr/bin/env python3
"""specified_tests.py — derive the Apex tests to run for a delta deployment.

Given a sfdx-git-delta package.xml, this inspects the ApexClass and ApexTrigger
members and finds the associated test classes so we can run `RunSpecifiedTests`
instead of the whole org on the lower environments.

Heuristic for locating tests:
  * Any changed class whose name matches a test pattern (starts/ends with Test,
    contains _Test, or ends with Tests) is itself a test.
  * For each changed non-test class/trigger `Foo`, include candidate test classes
    that exist in force-app named like `FooTest`, `Foo_Test`, `TestFoo`, `FooTests`.

Outputs:
  * a comma-separated list of test class names to stdout
  * `tests=<list>` and `test_level=<level>` to $GITHUB_OUTPUT when available

If no specific tests can be resolved, emits test_level=RunLocalTests and lists the
changed Apex classes that have no matching test in `untested=` so the caller can
fail fast (a delta containing Apex requires a matching *Test class).

The names come from file names in the branch. A changed class or trigger whose
name is not an Apex identifier (letters, digits, underscores) is refused, exit 1,
before it reaches a deploy command line; --result then gets a failure for the
report, as the deploy step writes for an unresolved test.

Usage:
  python3 scripts/ci/specified_tests.py --dir .sgd [--source-root force-app] [--result deploy-result.json]
"""
import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

NS = {"sf": "http://soap.sforce.com/2006/04/metadata"}
TEST_RE = re.compile(r"(^Test)|(_?Tests?$)", re.IGNORECASE)
APEX_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def changed_members(path, type_name):
    if not os.path.isfile(path):
        return []
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        return []
    members = []
    for types in root.findall("sf:types", NS):
        name_el = types.find("sf:name", NS)
        if name_el is None or (name_el.text or "").strip() != type_name:
            continue
        for m in types.findall("sf:members", NS):
            if m.text:
                members.append(m.text.strip())
    return members


def index_existing_classes(source_root):
    paths = {}
    for dirpath, _dirs, files in os.walk(source_root):
        for f in files:
            if f.endswith(".cls"):
                paths[f[:-4]] = os.path.join(dirpath, f)
    return paths


# Class-level @isTest annotation or the legacy testMethod keyword. A class must
# actually be a test class to be runnable via RunSpecifiedTests — a name that
# merely starts/ends with "Test" (e.g. TestDataFactory, TestButtonController) is
# NOT enough and would make Salesforce reject the deploy.
IS_TEST_RE = re.compile(r"@istest|testmethod", re.IGNORECASE)


def is_test_class(paths, name):
    path = paths.get(name)
    if not path or not os.path.isfile(path):
        # Not on disk (e.g. a candidate that doesn't exist) — can't confirm.
        return False
    try:
        return bool(IS_TEST_RE.search(open(path, encoding="utf-8").read()))
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=".sgd")
    ap.add_argument("--source-root", default="force-app")
    ap.add_argument("--result", default="", help="write a failure result here for a refused name")
    args = ap.parse_args()

    pkg = os.path.join(args.dir, "package", "package.xml")
    classes = changed_members(pkg, "ApexClass")
    triggers = changed_members(pkg, "ApexTrigger")
    existing = index_existing_classes(args.source_root)

    tests = set()
    changed = classes + triggers
    bad = [n for n in changed if not APEX_NAME_RE.match(n)]
    if bad:
        msg = ("Not a valid Apex class or trigger name: " + ", ".join(repr(n) for n in bad)
               + ". Apex names are letters, digits and underscores, starting with a letter. "
               "Rename the file(s) and push again; nothing was validated or deployed.")
        print(f"::error::{msg}", file=sys.stderr)
        if args.result:
            with open(args.result, "w", encoding="utf-8") as fh:
                json.dump({"status": 1, "result": {}, "message": msg}, fh)
        return 1
    untested = []
    for name in changed:
        # A changed class is itself a runnable test only if it is named like a
        # test AND actually contains @isTest / testMethod. This prevents a
        # non-test utility such as TestDataFactory or TestButtonController from
        # being fed to RunSpecifiedTests (which Salesforce would reject).
        if name in classes and TEST_RE.search(name) and is_test_class(existing, name):
            tests.add(name)
            continue
        matched = False
        for cand in (f"{name}Test", f"{name}_Test", f"Test{name}", f"{name}Tests"):
            if cand in existing and is_test_class(existing, cand):
                tests.add(cand)
                matched = True
        if not matched:
            untested.append(name)

    tests = sorted(tests)
    if tests:
        level = "RunSpecifiedTests"
        joined = ",".join(tests)
    else:
        # Nothing resolvable. The caller decides what to do: for a delta that
        # contains Apex this is a hard error (a matching *Test class is required),
        # while a no-Apex delta legitimately runs no tests.
        level = "RunLocalTests"
        joined = ""

    sys.stdout.write(joined + "\n")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"tests={joined}\n")
            fh.write(f"test_level={level}\n")
            fh.write(f"untested={','.join(untested)}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
