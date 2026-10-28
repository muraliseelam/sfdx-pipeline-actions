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
  * specified_tests.py   — which tests run; a name that is not Apex is refused
  * the deploy action    — its run: blocks take every value through env:, so a
                           test class name like Mk$(cmd)Test stays a string
  * audit.py             — the audit record names the deployed commit

Run:  python3 tests/test_deploy_engine.py
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "sf-pipeline", "scripts")
COVERAGE_GATE = os.path.join(ROOT, "sf-pipeline", "coverage", "coverage-gate.py")
DEPLOY_ACTION = os.path.join(ROOT, "sf-pipeline", "deploy", "action.yml")
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
        open(path, "w", encoding="utf-8").write(manifest({"Report": ["Sales/Pipeline"],
                                                          "Dashboard": ["Sales/Overview"]}))
        check("a folder-based type is kept", pd.prune(path, lister({"Report": [], "Dashboard": []})), [])
        check("without even listing it", calls, [])
        check("Report and Dashboard deletions both stay",
              members_of(path), {"Report": ["Sales/Pipeline"], "Dashboard": ["Sales/Overview"]})

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
        open(path, "w", encoding="utf-8").write(manifest({"CustomLabel": ["My%5FLabel", "my_label", "Absent"]}))
        skipped = pd.prune(path, lister({"CustomLabel": ["My_Label"]}))
        check("%5F and case variants of a present name are deleted; only the absent one is skipped",
              (skipped, members_of(path)), ([("CustomLabel", "Absent")], {"CustomLabel": ["My%5FLabel", "my_label"]}))

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

        # The boundary: exactly the threshold passes (17 of 20 lines = 85.00%).
        for covered, rc in ((17, 0), (16, 1)):
            json.dump({"status": 0, "result": {"details": {"runTestResult": {"codeCoverage": [
                {"name": "PROJ104", "numLocations": 20, "numLocationsNotCovered": 20 - covered}]}}}},
                open(os.path.join(tmp, "deploy-result.json"), "w"))
            p = run([sys.executable, COVERAGE_GATE], tmp, env)
            check(f"{covered * 5}.00% against an 85% gate exits {rc}", p.returncode, rc)


# ---------------------------------------------------------------------------
def test_manifest_to_md():
    print("\n\033[1mmanifest_to_md.py — member names\033[0m")
    mm = load("manifest_to_md")
    md = mm.render_table([("Layout", "A|B Layout")], "delete")
    check("a '|' in a member name is escaped", "| A\\|B Layout |" in md, True)


# ---------------------------------------------------------------------------
def specified(tmp, changed, classes, *extra):
    """Run specified_tests.py on a delta of {type: [members]} and a source tree of
    {class name: body}; return (rc, GITHUB_OUTPUT dict, stderr)."""
    shutil.rmtree(os.path.join(tmp, ".sgd"), ignore_errors=True)
    shutil.rmtree(os.path.join(tmp, "force-app"), ignore_errors=True)
    os.makedirs(os.path.join(tmp, ".sgd", "package"))
    open(os.path.join(tmp, ".sgd", "package", "package.xml"), "w", encoding="utf-8").write(manifest(changed))
    cdir = os.path.join(tmp, "force-app", "main", "default", "classes")
    os.makedirs(cdir)
    for name, body in classes.items():
        open(os.path.join(cdir, name + ".cls"), "w", encoding="utf-8").write(body)
    out = os.path.join(tmp, "gh_output")
    open(out, "w").close()
    p = run([sys.executable, os.path.join(SCRIPTS, "specified_tests.py"), "--dir", ".sgd",
             "--source-root", "force-app", *extra], tmp, {"GITHUB_OUTPUT": out})
    vals = dict(line.split("=", 1) for line in open(out, encoding="utf-8").read().splitlines() if "=" in line)
    return p.returncode, vals, p.stderr


def test_specified_tests():
    print("\n\033[1mspecified_tests.py — the tests a delta runs\033[0m")
    test = "@isTest private class X { @isTest static void t() {} }"
    with tempfile.TemporaryDirectory() as tmp:
        rc, o, _ = specified(tmp, {"ApexClass": ["Foo"]}, {"Foo": "public class Foo {}", "FooTest": test})
        check("a changed class runs its <Class>Test", (rc, o["tests"], o["test_level"]),
              (0, "FooTest", "RunSpecifiedTests"))
        rc, o, _ = specified(tmp, {"ApexClass": ["Foo"]},
                             {"Foo": "", "Foo_Test": test, "TestFoo": test, "FooTests": test})
        check("Test<Class>, <Class>_Test and <Class>Tests are accepted too",
              o["tests"], "FooTests,Foo_Test,TestFoo")
        rc, o, _ = specified(tmp, {"ApexTrigger": ["AccountTrigger"]},
                             {"AccountTriggerTest": "@IsTest class AccountTriggerTest {}"})
        check("a trigger runs its test", o["tests"], "AccountTriggerTest")
        rc, o, _ = specified(tmp, {"ApexClass": ["BarTest"]}, {"BarTest": "class B { static testMethod void t() {} }"})
        check("a changed test class runs itself (legacy testMethod)", o["tests"], "BarTest")
        rc, o, _ = specified(tmp, {"ApexClass": ["TestDataFactory"]}, {"TestDataFactory": "public class TestDataFactory {}"})
        check("a class only NAMED like a test is not run", (o["tests"], o["untested"]), ("", "TestDataFactory"))
        rc, o, _ = specified(tmp, {"ApexClass": ["Baz"]}, {"Baz": "", "BazTest": "public class BazTest {}"})
        check("a candidate without @isTest is not a test", (o["tests"], o["untested"]), ("", "Baz"))
        rc, o, _ = specified(tmp, {"ApexClass": ["Lonely"]}, {"Lonely": ""})
        check("no matching test: untested listed, RunLocalTests for the caller to refuse",
              (rc, o["tests"], o["untested"], o["test_level"]), (0, "", "Lonely", "RunLocalTests"))
        rc, o, _ = specified(tmp, {"CustomField": ["Account.F__c"]}, {})
        check("a delta without Apex resolves nothing", (rc, o["tests"], o["untested"]), (0, "", ""))

        # A file name is not an Apex name: refused before it reaches the shell.
        for bad in ("Mk$(touch INJECTED)Test", "Mk`id`Test", "Mk Test", "Mk;idTest", "1Foo"):
            body = test.replace("X", "MkTest")
            rc, o, err = specified(tmp, {"ApexClass": [bad]}, {bad: body}, "--result", "deploy-result.json")
            check(f"refuses the class name {bad!r}", (rc, o), (1, {}))
        check("and says why", "Not a valid Apex class or trigger name" in err, True)
        res = os.path.join(tmp, "deploy-result.json")
        msg = json.load(open(res, encoding="utf-8"))["message"] if os.path.exists(res) else ""
        check("in the result the PR report renders", "'1Foo'" in msg, True)
        rc, o, _ = specified(tmp, {"ApexClass": ["Foo", "Mk$(id)"]}, {"Foo": "", "FooTest": test})
        check("one bad name refuses the whole delta", (rc, o), (1, {}))


def deploy_steps():
    import yaml
    return yaml.safe_load(open(DEPLOY_ACTION, encoding="utf-8"))["runs"]["steps"]


# What the "Deploy / validate / quick-deploy" step is given, by the expression the
# runner evaluates for it.
STEP_INPUTS = {
    "ALIAS": "inputs.environment", "CHECK": "inputs.check_only", "QUICK_ID": "inputs.quick_deploy_id",
    "REQUESTED_LEVEL": "inputs.test_level", "COVERAGE_GATE": "inputs.coverage_gate",
    "BYPASS": "inputs.bypass", "CANARY_TESTS": "inputs.destructive_test_names",
    "TESTS": "steps.tests.outputs.tests", "UNTESTED": "steps.tests.outputs.untested",
    "HAS_APEX": "steps.scope.outputs.has_apex", "APEX_DESTRUCTIVE": "steps.scope.outputs.apex_destructive",
}


def render(text, values):
    """The runner's ${{ }} substitution: textual, before bash ever sees the script."""
    return re.sub(r"\$\{\{\s*(.*?)\s*\}\}", lambda m: values.get(m.group(1), ""), str(text))


def run_deploy_step(tmp, pkg, dstr, **given):
    """Run the deploy action's "Deploy / validate / quick-deploy" script as the runner
    would (expressions substituted into env: and run:) with a fake `sf` that records
    its arguments. Returns (rc, sf args or None, GITHUB_OUTPUT text)."""
    step = next(s for s in deploy_steps() if s.get("id") == "run")
    given = dict({"REQUESTED_LEVEL": "RunSpecifiedTests", "CHECK": "false", "COVERAGE_GATE": "false",
                  "BYPASS": "false"}, **given)
    values = {STEP_INPUTS[k]: v for k, v in given.items()}
    base = {k: render(v, values) for k, v in (step.get("env") or {}).items()}
    for d in (".sgd", "bin"):
        shutil.rmtree(os.path.join(tmp, d), ignore_errors=True)
    for f in ("INJECTED", "POC", "deploy-result.json"):
        if os.path.exists(os.path.join(tmp, f)):
            os.remove(os.path.join(tmp, f))
    os.makedirs(os.path.join(tmp, ".sgd", "package"))
    os.makedirs(os.path.join(tmp, ".sgd", "destructiveChanges"))
    open(os.path.join(tmp, ".sgd", "package", "package.xml"), "w", encoding="utf-8").write(manifest(pkg))
    if dstr:
        open(os.path.join(tmp, ".sgd", "destructiveChanges", "destructiveChanges.xml"), "w",
             encoding="utf-8").write(manifest(dstr))
    os.makedirs(os.path.join(tmp, "bin"))
    args_file = os.path.join(tmp, "sf-args")
    if os.path.exists(args_file):
        os.remove(args_file)
    sf = os.path.join(tmp, "bin", "sf")
    with open(sf, "w", newline="\n") as fh:
        fh.write('#!/usr/bin/env bash\nprintf \'%s\\n\' "$@" > "$SF_ARGS"\n'
                 'echo \'{"status":0,"result":{"id":"0AfFAKE","success":true}}\'\n')
    os.chmod(sf, 0o755)
    script = os.path.join(tmp, "step.sh")
    with open(script, "w", newline="\n", encoding="utf-8") as fh:
        fh.write(render(step["run"], values))
    out = os.path.join(tmp, "gh_output")
    open(out, "w").close()
    p = run(["bash", "step.sh"], tmp, dict(base, GITHUB_OUTPUT=out, SF_ARGS=args_file,
                                            PATH=os.path.join(tmp, "bin") + os.pathsep + os.environ["PATH"]))
    sf_args = open(args_file, encoding="utf-8").read().splitlines() if os.path.exists(args_file) else None
    return p.returncode, sf_args, open(out, encoding="utf-8").read()


def flag_value(args, flag):
    args = args or []
    return [args[i + 1] for i, a in enumerate(args) if a == flag]


def test_deploy_action():
    print("\n\033[1mdeploy action — values reach the shell as data\033[0m")
    leaks = [f"{s.get('name')}: {s['run'].strip().splitlines()[0][:60]}"
             for s in deploy_steps() if "run" in s and "${{" in s["run"]]
    check("no run: block interpolates ${{ }} (everything goes through env:)", leaks, [])
    audit = [s for s in deploy_steps() if "AUDIT_SHA" in (s.get("env") or {})]
    check("the audit trail never records github.sha (a PR run's merge commit)",
          [s["env"]["AUDIT_SHA"] for s in audit if "github.sha" in s["env"]["AUDIT_SHA"]], [])
    check("both audit steps record the deployed commit", len(audit), 2)
    if not shutil.which("bash"):
        print("  (run-block checks skipped — bash is not available)")
        return

    apex = {"ApexClass": ["Mk"]}
    with tempfile.TemporaryDirectory() as tmp:
        hostile = "Mk$(touch INJECTED)Test"
        rc, args, out = run_deploy_step(tmp, apex, None, ALIAS="int", CHECK="true", HAS_APEX="true",
                                        TESTS=hostile + ",Other`touch INJECTED`Test")
        check("a test name with $(...) runs nothing", os.path.exists(os.path.join(tmp, "INJECTED")), False)
        check("and reaches sf as the literal string",
              (rc, flag_value(args or [], "--tests")), (0, [hostile, "Other`touch INJECTED`Test"]))
        check("as a validation with RunSpecifiedTests", "mode=validate" in out and "level=RunSpecifiedTests" in out, True)

        rc, args, _ = run_deploy_step(tmp, apex, None, ALIAS="int", CHECK="true", HAS_APEX="true",
                                      UNTESTED="Mk$(touch INJECTED)")
        res = os.path.join(tmp, "deploy-result.json")
        res = json.load(open(res, encoding="utf-8")) if os.path.exists(res) else {}
        check("an untested name with $(...) runs nothing either",
              (rc, args, os.path.exists(os.path.join(tmp, "INJECTED"))), (1, None, False))
        check("and is quoted in the failure",
              "No Apex test could be resolved for: Mk$(touch INJECTED)." in res.get("message", ""), True)

        destr = {"ApexClass": ["OldClass"]}
        rc, args, out = run_deploy_step(tmp, {}, destr, ALIAS="prod", CHECK="true", APEX_DESTRUCTIVE="true",
                                        CANARY_TESTS="Canary$(touch POC)Test")
        check("a production.destructive_tests value with $(...) runs nothing",
              os.path.exists(os.path.join(tmp, "POC")), False)
        check("the canary runs as given, RunSpecifiedTests",
              (rc, flag_value(args, "--tests"), flag_value(args, "--test-level")),
              (0, ["Canary$(touch POC)Test"], ["RunSpecifiedTests"]))

        for check_only in ("true", "false"):
            rc, args, _ = run_deploy_step(tmp, {}, destr, ALIAS="prod", CHECK=check_only, APEX_DESTRUCTIVE="true")
            check(f"an Apex deletion to prod without a canary runs RunLocalTests (check_only={check_only})",
                  (rc, flag_value(args, "--test-level"), "--post-destructive-changes" in (args or [])),
                  (0, ["RunLocalTests"], True))
            rc, args, _ = run_deploy_step(tmp, {}, destr, ALIAS="prod", CHECK=check_only, APEX_DESTRUCTIVE="true",
                                          CANARY_TESTS="CanaryTest")
            check(f"with a canary it runs RunSpecifiedTests, never NoTestRun (check_only={check_only})",
                  (flag_value(args, "--test-level"), flag_value(args, "--tests")),
                  (["RunSpecifiedTests"], ["CanaryTest"]))
        rc, args, _ = run_deploy_step(tmp, {}, destr, ALIAS="qa", APEX_DESTRUCTIVE="true")
        check("a sandbox Apex deletion may skip tests", flag_value(args, "--test-level"), ["NoTestRun"])

        rc, args, out = run_deploy_step(tmp, {}, None, ALIAS="int")
        check("nothing left after pruning: success, sf never called", (rc, args), (0, None))
        check("reported as nothing-left", "mode=nothing-left" in out, True)

        rc, args, out = run_deploy_step(tmp, apex, None, ALIAS="int", HAS_APEX="true", TESTS="MkTest",
                                        COVERAGE_GATE="true")
        check("a real Apex deploy behind the coverage gate validates first (gated)",
              (rc, (args or [])[:3], "mode=gated" in out, "quick_job=0AfFAKE" in out),
              (0, ["project", "deploy", "validate"], True, True))
        quick = next(s for s in deploy_steps() if s.get("id") == "quick")
        check("and is quick-deployed only after the gate passed",
              "steps.coverage.outcome == 'success'" in quick["if"], True)

        # The report step: a refused quick deploy is not reported as a coverage stop.
        step = next(s for s in deploy_steps() if s.get("id") == "report")
        with open(os.path.join(tmp, "report.sh"), "w", newline="\n", encoding="utf-8") as fh:
            fh.write(step["run"])
        json.dump({"status": 1, "message": "Job ID can't be used for quick deployment."},
                  open(os.path.join(tmp, "deploy-result.json"), "w"))
        for cov, quick_outcome, want in (("success", "failure", "refused the quick deploy"),
                                         ("failure", "skipped", "coverage-gated before it is deployed")):
            open(os.path.join(tmp, "gh_output"), "w").close()
            p = run(["bash", "report.sh"], tmp, {
                "GITHUB_ACTION_PATH": os.path.join(ROOT, "sf-pipeline", "deploy"),
                "GITHUB_OUTPUT": os.path.join(tmp, "gh_output"), "GITHUB_STEP_SUMMARY": "",
                "QUICK_ID": "", "HAS_CHANGES": "true", "RUN_OUTCOME": "success", "COVERAGE_OUTCOME": cov,
                "QUICK_JOB": "0AfV", "QUICK_OUTCOME": quick_outcome, "ENVIRONMENT": "int",
                "CHECK_ONLY": "false", "MODE": "gated", "HAS_APEX": "true", "LEVEL": "RunSpecifiedTests"})
            md = open(os.path.join(tmp, ".deploy", "errors.md"), encoding="utf-8").read()
            check(f"gated, gate {cov}, quick deploy {quick_outcome}: the report says so",
                  (p.returncode, "status=failure" in open(os.path.join(tmp, "gh_output")).read(), want in md),
                  (0, True, True))


def git(cwd, *args):
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args], cwd=cwd,
                          input="", capture_output=True, text=True, check=True).stdout.strip()


def delta(repo, write=True, **env):
    """Run delta.sh in repo with a fake `sf sgd` (writes a package.xml unless
    write=False). Returns (rc, GITHUB_OUTPUT dict, stdout+stderr)."""
    bindir = os.path.join(os.path.dirname(repo), "bin")
    os.makedirs(bindir, exist_ok=True)
    with open(os.path.join(bindir, "sf"), "w", newline="\n") as fh:
        fh.write('#!/usr/bin/env bash\nwhile [ $# -gt 0 ]; do [ "$1" = "--output-dir" ] && OUT="$2"; shift; done\n'
                 '[ "$SF_WRITE" = "true" ] || exit 0\nmkdir -p "$OUT/package"\n'
                 "printf '%s' '" + manifest({"ApexClass": ["Foo"]}) + "' > \"$OUT/package/package.xml\"\n")
    os.chmod(os.path.join(bindir, "sf"), 0o755)
    out = os.path.join(os.path.dirname(repo), "gh_output")
    open(out, "w").close()
    p = run(["bash", os.path.join(SCRIPTS, "delta.sh").replace("\\", "/")], repo,
            dict(env, GITHUB_OUTPUT=out, SF_WRITE="true" if write else "false",
                 PATH=bindir + os.pathsep + os.environ["PATH"]))
    vals = dict(line.split("=", 1) for line in open(out, encoding="utf-8").read().splitlines() if "=" in line)
    return p.returncode, vals, p.stdout + p.stderr


def test_delta():
    print("\n\033[1mdelta.sh — never a silent 'no changes'; names the commit it diffed to\033[0m")
    if not shutil.which("bash") or not shutil.which("git"):
        print("  (skipped — bash or git is not available)")
        return
    with tempfile.TemporaryDirectory() as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(repo)
        git(repo, "init", "-q")
        git(repo, "checkout", "-q", "-b", "main")
        open(os.path.join(repo, "a.txt"), "w").write("a")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")
        git(repo, "update-ref", "refs/remotes/origin/main", base)
        git(repo, "checkout", "-q", "-b", "feature")
        open(os.path.join(repo, "b.txt"), "w").write("b")
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "feature")
        head = git(repo, "rev-parse", "HEAD")
        orphan = git(repo, "commit-tree", git(repo, "mktree"), "-m", "unrelated")
        git(repo, "update-ref", "refs/remotes/origin/other", orphan)

        rc, o, _ = delta(repo, DELTA_FROM="origin/main", DELTA_MERGE_BASE="true", DELTA_OUTPUT=".sgd")
        check("three-dot: from the merge-base", (rc, o.get("from_ref"), o.get("has_changes")), (0, base, "true"))
        check("to_sha is the commit diffed to (what gets deployed)", o.get("to_sha"), head)
        rc, o, log = delta(repo, DELTA_FROM="origin/other", DELTA_MERGE_BASE="true", DELTA_OUTPUT=".sgd")
        check("no merge-base (shallow / unrelated): the run fails",
              (rc, "Refusing a two-dot diff" in log, o.get("has_changes")), (1, True, None))
        rc, o, log = delta(repo, write=False, DELTA_FROM="origin/main", DELTA_MERGE_BASE="true", DELTA_OUTPUT=".sgd")
        check("sgd writes no package.xml: the run fails, not 'no changes'",
              (rc, "wrote no" in log, o.get("has_changes")), (1, True, None))


def profile(a, b):
    fp = "".join(f"<fieldPermissions><editable>{e}</editable><field>Account.{f}__c</field>"
                 "<readable>true</readable></fieldPermissions>" for f, e in (("A", a), ("B", b)))
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<Profile xmlns="http://soap.sforce.com/2006/04/metadata">{fp}<custom>false</custom></Profile>\n')


def test_profiles_incremental():
    print("\n\033[1mprofiles.py incremental — self-diff against the delta's own from_ref\033[0m")
    if not shutil.which("git"):
        print("  (skipped — git is not available)")
        return
    with tempfile.TemporaryDirectory() as repo:
        rel = "force-app/main/default/profiles/Admin.profile-meta.xml"
        path = os.path.join(repo, *rel.split("/"))
        os.makedirs(os.path.dirname(path))
        git(repo, "init", "-q")
        git(repo, "checkout", "-q", "-b", "main")
        open(path, "w", encoding="utf-8").write(profile("false", "false"))
        git(repo, "add", ".")
        git(repo, "commit", "-q", "-m", "base")
        base = git(repo, "rev-parse", "HEAD")
        # Another story already changed B on the target env branch.
        git(repo, "checkout", "-q", "-b", "int")
        open(path, "w", encoding="utf-8").write(profile("false", "true"))
        git(repo, "commit", "-q", "-am", "other story: B editable")
        git(repo, "update-ref", "refs/remotes/origin/int", "HEAD")
        # This branch changes only A.
        git(repo, "checkout", "-q", "-b", "feature", base)
        open(path, "w", encoding="utf-8").write(profile("true", "false"))
        git(repo, "commit", "-q", "-am", "this story: A editable")
        os.makedirs(os.path.join(repo, ".sgd", "package"))
        open(os.path.join(repo, ".sgd", "package", "package.xml"), "w", encoding="utf-8").write(
            manifest({"Profile": ["Admin"]}))

        def kept(baseline):
            git(repo, "checkout", "-q", "--", rel)
            p = run([sys.executable, os.path.join(SCRIPTS, "profiles.py"), "--mode", "incremental",
                     "--baseline", baseline], repo, {"GITHUB_STEP_SUMMARY": ""})
            root = ET.parse(path).getroot()
            return p.returncode, sorted(fp.find(f"{NS}field").text for fp in root.findall(f"{NS}fieldPermissions"))

        merge_base = git(repo, "merge-base", "origin/int", "HEAD")
        check("against the merge-base (from_ref): only this branch's entry is deployed",
              kept(merge_base), (0, ["Account.A__c"]))
        check("against the target's tip it would deploy B back to its old value",
              kept("origin/int"), (0, ["Account.A__c", "Account.B__c"]))


def test_audit():
    print("\n\033[1maudit.py — the record names the deployed commit\033[0m")
    head = "d75fccf392d6a1b2c3d4e5f60718293a4b5c6d7e"
    with tempfile.TemporaryDirectory() as tmp:
        p = run([sys.executable, os.path.join(SCRIPTS, "audit.py")], tmp,
                {"AUDIT_SHA": head, "AUDIT_REF": head, "AUDIT_ENVIRONMENT": "int",
                 "AUDIT_STATUS": "success", "GITHUB_STEP_SUMMARY": ""})
        rec = json.load(open(os.path.join(tmp, ".deploy", "audit-record.json"), encoding="utf-8"))
        check("sha is the deployed commit, equal to ref", (p.returncode, rec["sha"], rec["ref"]), (0, head, head))
        check("short_sha abbreviates it", rec["short_sha"], head[:8])


def main():
    print("Deploy engine suite")
    for fn in (test_prune, test_deploy_report, test_coverage_gate, test_manifest_to_md,
               test_specified_tests, test_deploy_action, test_delta, test_profiles_incremental,
               test_audit):
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
