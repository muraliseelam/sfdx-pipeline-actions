#!/usr/bin/env python3
"""
test_deploy_engine.py — offline checks for the deploy engine's scripts.

The deploy action talks to Salesforce, so it can't run here, but the scripts
that decide what reaches the org and what the report says can:

  * prune_destructive.py — drops ONLY deletions it has confirmed the org lacks
  * deploy_report.py     — the job id, mode-aware wording, skipped deletions
  * coverage-gate.py     — fails a low coverage; records it without failing
                           when Salesforce had already rejected the deploy
  * manifest_to_md.py    — member names cannot break the table

Run:  python3 tests/test_deploy_engine.py
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "sf-pipeline", "scripts")
COVERAGE_GATE = os.path.join(ROOT, "sf-pipeline", "coverage", "coverage-gate.py")
NS = "{http://soap.sforce.com/2006/04/metadata}"

FAILURES = []
CHECKS = 0


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    if actual != expected:
        FAILURES.append(f"{label}\n      expected: {expected!r}\n      actual:   {actual!r}")
        print(f"  \033[31m✗\033[0m {label}")
    else:
        print(f"  \033[32m✓\033[0m {label}")


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(SCRIPTS, f"{name}.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def manifest(types):
    """A destructiveChanges.xml with {type: [members]}."""
    body = "".join(
        "<types>" + "".join(f"<members>{m}</members>" for m in members)
        + f"<name>{t}</name></types>"
        for t, members in types.items()
    )
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<Package xmlns="http://soap.sforce.com/2006/04/metadata">'
            f"{body}<version>66.0</version></Package>\n")


def members_of(path):
    out = {}
    for t in ET.parse(path).getroot().findall(f"{NS}types"):
        out[t.find(f"{NS}name").text] = [m.text for m in t.findall(f"{NS}members")]
    return out


def run(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          env=dict(os.environ, **(env or {})))


# ---------------------------------------------------------------------------
def test_prune():
    print("\n\033[1mprune_destructive.py — skip deletions the org no longer has\033[0m")
    pd = load("prune_destructive")
    calls = []

    def lister(org_names):
        def _l(mtype):
            calls.append(mtype)
            names = org_names.get(mtype)
            return None if names is None else {pd.norm(n) for n in names}
        return _l

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "destructiveChanges.xml")

        # The live failure: the label was already gone from the stage org.
        open(path, "w", encoding="utf-8").write(manifest({"CustomLabel": ["E2E_Recovery"]}))
        skipped = pd.prune(path, lister({"CustomLabel": ["E2E_Weekday"]}))
        check("an absent label is skipped", skipped, [("CustomLabel", "E2E_Recovery")])
        check("and its now-empty type block is removed", members_of(path), {})
        check("the rewritten manifest keeps its version",
              ET.parse(path).getroot().find(f"{NS}version").text, "66.0")

        # Present members stay; only the absent one goes.
        open(path, "w", encoding="utf-8").write(manifest(
            {"CustomField": ["Account.Keep__c", "Account.Gone__c"]}))
        skipped = pd.prune(path, lister({"CustomField": ["Account.Keep__c"]}))
        check("only the absent field is skipped", skipped, [("CustomField", "Account.Gone__c")])
        check("the present field is still deleted", members_of(path), {"CustomField": ["Account.Keep__c"]})

        # A listing that fails must never drop anything.
        open(path, "w", encoding="utf-8").write(manifest({"ApexClass": ["Foo"]}))
        before = open(path, encoding="utf-8").read()
        check("an unlistable type keeps every deletion", pd.prune(path, lister({})), [])
        check("and the manifest is left byte-for-byte", open(path, encoding="utf-8").read(), before)

        # Folder-based types are never listed, so never pruned.
        calls.clear()
        open(path, "w", encoding="utf-8").write(manifest({"Report": ["Sales/Pipeline"]}))
        check("a folder-based type is kept", pd.prune(path, lister({"Report": []})), [])
        check("without even listing it", calls, [])

        # Encoding and case differences keep the deletion (the safe direction).
        open(path, "w", encoding="utf-8").write(manifest({
            "Layout": ["Account-Account %28Marketing%29 Layout"],
            "CustomLabel": ["e2e_RECOVERY"],
        }))
        skipped = pd.prune(path, lister({
            "Layout": ["Account-Account (Marketing) Layout"],
            "CustomLabel": ["E2E_Recovery"],
        }))
        check("URL-encoded and case-different names match", skipped, [])

        # Spaces and XML-escaped characters survive the rewrite.
        open(path, "w", encoding="utf-8").write(manifest({
            "CustomLabel": ["Keep", "R&amp;D Label"],
            "Layout": ["Account-Account Layout"],
        }))
        skipped = pd.prune(path, lister({"CustomLabel": ["Keep"], "Layout": []}))
        check("special characters and spaces are skipped by name",
              sorted(skipped), [("CustomLabel", "R&D Label"), ("Layout", "Account-Account Layout")])
        check("the rewrite is still valid XML with the kept member",
              members_of(path), {"CustomLabel": ["Keep"]})

        # A wildcard is never pruned.
        open(path, "w", encoding="utf-8").write(manifest({"CustomTab": ["*"]}))
        check("a wildcard member is kept", pd.prune(path, lister({"CustomTab": []})), [])

    check("listing as a single object is accepted",
          pd.names_from_listing({"fullName": "Only"}), {"only"})
    check("namespaced components match with and without the prefix",
          pd.names_from_listing([{"fullName": "Thing", "namespacePrefix": "ns"}]), {"thing", "ns__thing"})
    check("an unexpected listing shape is 'unknown', not 'empty'",
          pd.names_from_listing(None), None)

    class Proc:
        def __init__(self, rc, out):
            self.returncode, self.stdout, self.stderr = rc, out, ""

    real = pd.subprocess.run
    try:
        pd.subprocess.run = lambda *a, **k: Proc(1, '{"status":1,"message":"INVALID_TYPE"}')
        check("a failing `sf org list metadata` is 'unknown'", pd.list_org("qa", "Foo"), None)
        pd.subprocess.run = lambda *a, **k: Proc(0, "not json")
        check("unparseable output is 'unknown'", pd.list_org("qa", "Foo"), None)
        pd.subprocess.run = lambda *a, **k: Proc(0, '{"status":0,"result":[]}')
        check("an empty listing means the org has none", pd.list_org("qa", "CustomLabel"), set())
        seen = {}
        pd.subprocess.run = lambda cmd, **k: (seen.setdefault("cmd", cmd), Proc(0, '{"status":0,"result":[{"fullName":"A"}]}'))[1]
        check("a listing returns its names", pd.list_org("qa", "CustomLabel", "66.0"), {"a"})
        check("and asks the right org, type and API version",
              seen["cmd"], ["sf", "org", "list", "metadata", "--metadata-type", "CustomLabel",
                            "--target-org", "qa", "--json", "--api-version", "66.0"])

        # End to end, as the deploy action calls it.
        with tempfile.TemporaryDirectory() as tmp:
            os.makedirs(os.path.join(tmp, ".sgd", "destructiveChanges"))
            dstr = os.path.join(tmp, ".sgd", "destructiveChanges", "destructiveChanges.xml")
            open(dstr, "w", encoding="utf-8").write(manifest({"CustomLabel": ["E2E_Recovery", "Keep"]}))
            pd.subprocess.run = lambda *a, **k: Proc(0, '{"status":0,"result":[{"fullName":"Keep"}]}')
            cwd, argv = os.getcwd(), sys.argv
            summary = os.environ.pop("GITHUB_STEP_SUMMARY", None)  # keep CI's summary clean
            try:
                os.chdir(tmp)
                sys.argv = ["prune_destructive.py", "--target-org", "stage", "--api-version", "66.0"]
                rc = pd.main()
            finally:
                os.chdir(cwd)
                sys.argv = argv
                if summary is not None:
                    os.environ["GITHUB_STEP_SUMMARY"] = summary
            check("main() exits 0", rc, 0)
            check("main() records the skipped deletion for the report",
                  json.load(open(os.path.join(tmp, ".deploy", "skipped-deletions.json"))),
                  [{"type": "CustomLabel", "member": "E2E_Recovery"}])
            check("main() rewrites the manifest in place", members_of(dstr), {"CustomLabel": ["Keep"]})
    finally:
        pd.subprocess.run = real


# ---------------------------------------------------------------------------
def report(tmp, result, *args, skipped=None, coverage=None):
    os.makedirs(os.path.join(tmp, ".deploy"), exist_ok=True)
    json.dump(result, open(os.path.join(tmp, "deploy-result.json"), "w"))
    for name, data in (("skipped-deletions.json", skipped), ("coverage.json", coverage)):
        p = os.path.join(tmp, ".deploy", name)
        if data is None:
            if os.path.exists(p):
                os.remove(p)
        else:
            json.dump(data, open(p, "w"))
    proc = run([sys.executable, os.path.join(SCRIPTS, "deploy_report.py"),
                "--environment", "qa", *args], tmp, {"GITHUB_STEP_SUMMARY": ""})
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return (open(os.path.join(tmp, ".deploy", "errors.md"), encoding="utf-8").read(),
            json.load(open(os.path.join(tmp, ".deploy", "report.json"), encoding="utf-8")))


def test_deploy_report():
    print("\n\033[1mdeploy_report.py — what the PR and the job summary are told\033[0m")
    dr = load("deploy_report")
    # sf lists `details` (whose components carry ids) before the job's own id.
    ok = {"status": 0, "result": {"details": {"componentSuccesses": [{"id": "01pCOMPONENT"}]},
                                  "id": "0AfJOB"}}
    check("the job id, not a component id", dr.job_id(ok), "0AfJOB")
    check("the job id from the error envelope", dr.job_id({"status": 1, "data": {"id": "0AfERR"}}), "0AfERR")
    check("no id is blank, not a guess", dr.job_id({"result": {"details": {"x": [{"id": "01p"}]}}}), "")

    with tempfile.TemporaryDirectory() as tmp:
        md, rep = report(tmp, ok, "--status", "success", "--check-only", "false")
        check("report.json carries the job id", rep["deploy_id"], "0AfJOB")

        fail = {"status": 1, "message": "Deploy failed.", "data": {"id": "0AfV"}}
        md, rep = report(tmp, fail, "--status", "failure", "--check-only", "false", "--mode", "gated",
                         "--has-apex", "true", "--test-level", "RunSpecifiedTests",
                         coverage={"threshold": 85, "percent": 81.82, "measured": True,
                                   "per_class": [{"name": "E2eLowCoverage", "percent": 81.82}]})
        check("a gated failure says nothing was deployed", "Nothing was deployed to `qa`" in md, True)
        check("and shows the coverage that stopped it", "81.82%" in md, True)
        check("report.json records the Apex and the test level",
              (rep["has_apex"], rep["test_level"], rep["mode"]), (True, "RunSpecifiedTests", "gated"))

        md, rep = report(tmp, {"status": 0, "result": {"id": "", "skipped": "nothing-left"}},
                         "--status", "success", "--check-only", "false", "--mode", "nothing-left",
                         skipped=[{"type": "CustomLabel", "member": "E2E_Recovery"}])
        check("nothing left to deploy is reported as such", "Nothing to deploy to `qa`" in md, True)
        check("the skipped deletion is listed", "| CustomLabel | E2E_Recovery |" in md, True)
        check("and handed to the PR report", rep["skipped_deletions"],
              [{"type": "CustomLabel", "member": "E2E_Recovery"}])

        md, _ = report(tmp, {"status": 0, "result": {"id": ""}}, "--status", "success",
                       "--check-only", "true", "--mode", "dry-run")
        check("a no-test validation says it was check-only", "check-only; no Apex tests to run" in md, True)


# ---------------------------------------------------------------------------
def test_coverage_gate():
    print("\n\033[1mcoverage-gate.py — gate, or record only\033[0m")
    result = {"status": 0, "result": {"details": {"runTestResult": {"codeCoverage": [
        {"name": "E2eLowCoverage", "numLocations": 11, "numLocationsNotCovered": 2}]}}}}
    with tempfile.TemporaryDirectory() as tmp:
        json.dump(result, open(os.path.join(tmp, "deploy-result.json"), "w"))
        env = {"COVERAGE_THRESHOLD": "85", "ENVIRONMENT": "qa", "GITHUB_STEP_SUMMARY": ""}
        p = run([sys.executable, COVERAGE_GATE], tmp, env)
        check("81.82% fails an 85% gate", p.returncode, 1)
        p = run([sys.executable, COVERAGE_GATE], tmp, dict(env, COVERAGE_MEASURE_ONLY="true"))
        check("but only records it once Salesforce rejected the deploy", p.returncode, 0)
        cov = json.load(open(os.path.join(tmp, ".deploy", "coverage.json")))
        check("the measurement still reaches the report", (cov["measured"], cov["percent"]), (True, 81.82))


# ---------------------------------------------------------------------------
def test_manifest_to_md():
    print("\n\033[1mmanifest_to_md.py — member names\033[0m")
    mm = load("manifest_to_md")
    md = mm.render_table([("Layout", "A|B Layout")], "delete")
    check("a '|' in a member name is escaped", "| A\\|B Layout |" in md, True)


def main():
    print("Deploy engine suite")
    for fn in (test_prune, test_deploy_report, test_coverage_gate, test_manifest_to_md):
        fn()
    print()
    if FAILURES:
        print(f"\033[31m{len(FAILURES)} of {CHECKS} checks FAILED\033[0m\n")
        for f in FAILURES:
            print(f"  ✗ {f}\n")
        return 1
    print(f"\033[32mAll {CHECKS} checks passed.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
