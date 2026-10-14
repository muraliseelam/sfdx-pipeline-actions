"""Unit tests for the sf-pipeline helper scripts.

Run from the repo root:
    python -m unittest discover -s sf-pipeline/tests -v
Needs only PyYAML (sf-pipeline/scripts/requirements.txt) and git.
"""
from __future__ import annotations

import contextlib
import copy
import http.server
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from argparse import Namespace
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bootstrap  # noqa: E402
import branch_protection  # noqa: E402
import code_analysis  # noqa: E402
import deploy_report  # noqa: E402
import gh_helpers  # noqa: E402
import jira  # noqa: E402
import pipeline_config  # noqa: E402
import preflight  # noqa: E402
import specified_tests  # noqa: E402

CFG = pipeline_config.load()
NS = "http://soap.sforce.com/2006/04/metadata"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def write(path: Path, text: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text), encoding="utf-8")
    return path


def package_xml(path: Path, types: dict[str, list[str]]) -> Path:
    body = "".join(
        "<types>" + "".join(f"<members>{m}</members>" for m in members) + f"<name>{name}</name></types>"
        for name, members in types.items()
    )
    return write(path, f'<?xml version="1.0" encoding="UTF-8"?><Package xmlns="{NS}">{body}<version>64.0</version></Package>')


# ---------------------------------------------------------------------- config


class PipelineConfigTests(unittest.TestCase):
    def test_repo_config_is_valid(self):
        self.assertEqual(pipeline_config.validate(CFG), [])
        self.assertEqual(CFG["promotion"]["chain"], ["dev", "int", "qa", "stage", "prod"])

    def test_validation_reports_every_problem(self):
        bad = copy.deepcopy(CFG)
        bad["quality"]["coverage_threshold"] = 120
        bad["environments"]["qa"]["test_policy"] = "canary"
        bad["environments"]["int"]["branch"] = "dev"
        bad["environments"]["stage"]["auth_method"] = "password"
        errs = "\n".join(pipeline_config.validate(bad))
        self.assertIn("coverage_threshold", errs)
        self.assertIn("canary_tests needs at least one", errs)
        self.assertIn("duplicates environments.dev", errs)
        self.assertIn("environments.stage.auth_method", errs)

    def test_convention_defaults(self):
        cfg = pipeline_config.from_dict({"environments": {"dev": {}, "uat-2": None, "prod": {}}})
        dev, uat, prod = (cfg["environments"][n] for n in ("dev", "uat-2", "prod"))
        self.assertEqual(cfg["promotion"]["chain"], ["dev", "uat-2", "prod"])
        self.assertEqual((dev["branch"], dev["org_alias"], dev["status_check"]), ("dev", "dev", "deploy-dev"))
        self.assertEqual(uat["secrets"]["auth_url"], "SF_UAT_2_AUTH_URL")
        self.assertEqual(uat["secrets"]["jwt_key"], "SF_UAT_2_JWT_KEY")
        self.assertEqual((dev["github_environment"], dev["test_policy"]), ("sf-dev", "specified"))
        self.assertTrue(prod["production"])
        self.assertEqual((prod["branch"], prod["test_policy"], prod["github_environment"]), ("main", "local", "production"))
        self.assertEqual(prod["validation_environment"], "production-validate")
        self.assertEqual(prod["instance_url"], "https://login.salesforce.com")
        self.assertEqual(dev["instance_url"], "https://test.salesforce.com")
        self.assertEqual(cfg["release"]["source_env"], "uat-2")
        self.assertFalse(prod["sync_after_release"])
        self.assertEqual(cfg["quality"]["coverage_threshold"], 75)
        self.assertEqual(cfg["auth"]["method"], "sfdx_url")

    def test_four_and_two_stage_chains(self):
        four = pipeline_config.from_dict({"environments": {"int": {}, "qa": {}, "stage": {}, "prod": {}}})
        self.assertEqual(pipeline_config.env_view(four, "int")["is_entry"], "true")
        self.assertEqual(four["release"]["source_env"], "stage")
        self.assertEqual(pipeline_config.promote_plan(four, "int", ["promote:qa"])["targets"][0]["env"], "qa")
        two = pipeline_config.from_dict({"environments": {"dev": {}, "prod": {}}})
        self.assertEqual(two["release"]["source_env"], "dev")
        self.assertIn("SF Prod Release", pipeline_config.promote_plan(two, "dev", ["promote:prod"])["errors"][0])

    def test_structural_errors(self):
        cases = {
            "at least two environments": {"environments": {"prod": {}}},
            "must be the last environment": {"environments": {"prod": {"production": True}, "dev": {}}},
            "only one environment may set production": {"environments": {"a": {"production": True}, "b": {"production": True}}},
            "unknown key(s): test_polcy": {"environments": {"dev": {"test_polcy": "local"}, "prod": {}}},
            "must start with a letter": {"environments": {"1dev": {}, "prod": {}}},
            "secrets may only set": {"environments": {"dev": {"secrets": {"password": "X"}}, "prod": {}}},
            "release.source_env": {"environments": {"dev": {}, "prod": {}}, "release": {"source_env": "prod"}},
        }
        for needle, raw in cases.items():
            with self.subTest(needle):
                with self.assertRaises(pipeline_config.ConfigError) as ctx:
                    pipeline_config.from_dict(raw)
                self.assertIn(needle, str(ctx.exception))

    def test_non_string_env_keys_are_handled(self):
        cfg = pipeline_config.from_dict({"environments": {"dev": {}, "prod": {}}})
        self.assertEqual(cfg["github"]["admin_pat_secret"], "SF_PIPELINE_PAT")
        with self.assertRaises(pipeline_config.ConfigError) as ctx:
            pipeline_config.from_dict({"environments": {2024: {}, True: {}, "prod": {}}})
        self.assertIn("'2024' must start with a letter", str(ctx.exception))

    def test_required_secrets_by_auth_method(self):
        cfg = pipeline_config.from_dict(
            {"environments": {"dev": {}, "prod": {"auth_method": "jwt", "secrets": {"username": "PROD_USER"}}}}
        )
        self.assertEqual(pipeline_config.required_secrets(cfg, "dev"), ["SF_DEV_AUTH_URL"])
        self.assertEqual(
            pipeline_config.required_secrets(cfg, "prod"), ["PROD_USER", "SF_PROD_CONSUMER_KEY", "SF_PROD_JWT_KEY"]
        )

    def test_production_cannot_use_none(self):
        bad = copy.deepcopy(CFG)
        bad["environments"]["prod"]["test_policy"] = "none"
        self.assertTrue(any("cannot be 'none'" in e for e in pipeline_config.validate(bad)))

    def test_find_env(self):
        self.assertEqual(pipeline_config.find_env(CFG, branch="refs/heads/int"), ("int", "branch"))
        self.assertEqual(pipeline_config.find_env(CFG, branch="main"), ("prod", "branch"))
        self.assertEqual(pipeline_config.find_env(CFG, label="deploy:qa"), ("qa", "deploy"))
        self.assertEqual(pipeline_config.find_env(CFG, label="promote:stage"), ("stage", "promote"))
        self.assertEqual(pipeline_config.find_env(CFG, label="bug")[0], None)
        self.assertEqual(pipeline_config.find_env(CFG, production=True)[0], "prod")

    def test_env_view(self):
        view = pipeline_config.env_view(CFG, "int")
        self.assertEqual(view["previous_env"], "dev")
        self.assertEqual(view["next_env"], "qa")
        self.assertEqual(view["is_production"], "false")
        self.assertEqual(view["username_secret"], "SF_INT_USERNAME")
        self.assertEqual(pipeline_config.env_view(CFG, "prod")["sync_after_release"], "false")
        self.assertEqual(pipeline_config.env_view(CFG, "dev")["is_entry"], "true")

    def test_promote_plan_enforces_order(self):
        plan = pipeline_config.promote_plan(CFG, "dev", ["promote:int", "promote:qa", "promote:prod", "bug"])
        self.assertEqual(plan["targets"], [{"env": "int", "branch": "int"}])
        self.assertEqual(len(plan["errors"]), 2)
        self.assertEqual(pipeline_config.promote_plan(CFG, "feature/x", ["promote:int"])["targets"], [])
        self.assertEqual(pipeline_config.promote_plan(CFG, "qa", target="stage")["targets"][0]["env"], "stage")

    def test_promote_plan_from_last_envs_does_not_crash(self):
        from_main = pipeline_config.promote_plan(CFG, "main", ["promote:dev"])
        self.assertEqual(from_main["targets"], [])
        self.assertIn("SF Prod Release", from_main["errors"][0])
        from_stage = pipeline_config.promote_plan(CFG, "stage", ["promote:dev"])
        self.assertIn("SF Prod Release", from_stage["errors"][0])

    def test_validation_environment_default(self):
        self.assertEqual(pipeline_config.env_view(CFG, "prod")["validation_environment"], "production-validate")
        self.assertEqual(pipeline_config.env_view(CFG, "int")["validation_environment"], "sf-int-validate")


# ----------------------------------------------------------------------- tests


class SpecifiedTestsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name) / "force-app" / "main" / "default"
        write(root / "classes" / "AccountService.cls", "public with sharing class AccountService {}")
        write(root / "classes" / "AccountServiceTest.cls", "@IsTest\nprivate class AccountServiceTest {}")
        write(root / "classes" / "Billing.cls", "// @isTest in a comment does not count\npublic class Billing {}")
        write(root / "classes" / "Billing_Test.cls", "public class Billing_Test {}  // not annotated")
        write(root / "classes" / "Legacy.cls", "public class Legacy {}")
        write(root / "classes" / "LegacyCoverage.cls", "@isTest(SeeAllData=false)\npublic class LegacyCoverage {}")
        write(root / "classes" / "SmokeTest.cls", "@isTest class SmokeTest {}")
        write(root / "triggers" / "AccountTrigger.trigger", "trigger AccountTrigger on Account (before insert) {}")
        write(root / "classes" / "AccountTriggerHandlerTest.cls", "@isTest class AccountTriggerHandlerTest {}")
        self.index = specified_tests.SourceIndex.build(Path(self.tmp.name) / "force-app")
        self.tests_cfg = copy.deepcopy(CFG["tests"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_is_test_class_ignores_comments(self):
        self.assertTrue(self.index.is_test("AccountServiceTest"))
        self.assertFalse(self.index.is_test("Billing"))
        self.assertFalse(self.index.is_test("Billing_Test"))
        self.assertTrue(self.index.is_test("legacycoverage"))

    def test_specified_resolves_companions_and_flags_untested(self):
        changed = {"ApexClass": ["AccountService", "Billing"], "ApexTrigger": ["AccountTrigger"]}
        plan = specified_tests.resolve(changed, self.index, self.tests_cfg, "specified", [])
        self.assertEqual(plan["test_level"], "RunSpecifiedTests")
        self.assertEqual(plan["tests"], ["AccountServiceTest", "AccountTriggerHandlerTest"])
        self.assertEqual([u["name"] for u in plan["untested"]], ["Billing"])
        self.assertIn("Billing_Test", plan["untested"][0]["searched"])

    def test_overrides(self):
        self.tests_cfg["overrides"] = {"Legacy": ["LegacyCoverage"]}
        plan = specified_tests.resolve({"ApexClass": ["Legacy"]}, self.index, self.tests_cfg, "specified", [])
        self.assertEqual(plan["tests"], ["LegacyCoverage"])
        self.assertEqual(plan["untested"], [])

    def test_no_apex_is_notestrun(self):
        plan = specified_tests.resolve({"CustomObject": ["Foo__c"]}, self.index, self.tests_cfg, "specified", [])
        self.assertEqual(plan["test_level"], "NoTestRun")
        self.assertEqual(plan["tests"], [])

    def test_changed_test_class_runs_itself(self):
        plan = specified_tests.resolve({"ApexClass": ["SmokeTest"]}, self.index, self.tests_cfg, "specified", [])
        self.assertEqual((plan["test_level"], plan["tests"]), ("RunSpecifiedTests", ["SmokeTest"]))

    def test_local_and_canary_policies(self):
        changed = {"ApexClass": ["AccountService"]}
        local = specified_tests.resolve(changed, self.index, self.tests_cfg, "local", [])
        self.assertEqual((local["test_level"], local["tests"]), ("RunLocalTests", []))
        canary = specified_tests.resolve({}, self.index, self.tests_cfg, "canary", ["SmokeTest"])
        self.assertEqual((canary["test_level"], canary["tests"]), ("RunSpecifiedTests", ["SmokeTest"]))

    def test_production_never_gets_notestrun(self):
        plan = specified_tests.resolve({"CustomObject": ["X__c"]}, self.index, self.tests_cfg, "specified", [],
                                       production=True)
        self.assertEqual((plan["test_level"], plan["tests"]), ("RunLocalTests", []))

    def test_coverage_exempt_flags(self):
        root = Path(self.tmp.name) / "force-app" / "main" / "default" / "classes"
        write(root / "Shape.cls", "/* class */ public interface Shape { Decimal area(); }")
        write(root / "Color.cls", "public enum Color { RED, GREEN }")
        write(root / "Consts.cls", "public class Consts { public static final Integer X = 1; }")
        index = specified_tests.SourceIndex.build(Path(self.tmp.name) / "force-app")
        cfg = dict(self.tests_cfg, require_companion=False)
        plan = specified_tests.resolve({"ApexClass": ["Shape", "Color", "Consts", "AccountService"]}, index, cfg,
                                       "specified", [], coverage_exempt=["Consts"])
        flags = {a["name"]: a["coverage_exempt"] for a in plan["apex_changed"]}
        self.assertEqual(flags, {"AccountService": False, "Color": True, "Consts": True, "Shape": True})

    def test_untested_still_fails_under_local(self):
        plan = specified_tests.resolve({"ApexClass": ["Billing"]}, self.index, self.tests_cfg, "local", [])
        self.assertEqual([u["name"] for u in plan["untested"]], ["Billing"])

    def test_read_package_and_synthetic_result(self):
        pkg = package_xml(Path(self.tmp.name) / "package.xml", {"ApexClass": ["Billing"], "CustomObject": ["X__c"]})
        self.assertEqual(specified_tests.read_package(pkg), {"ApexClass": ["Billing"], "CustomObject": ["X__c"]})
        plan = specified_tests.resolve({"ApexClass": ["Billing"]}, self.index, self.tests_cfg, "specified", [])
        result = specified_tests.synthetic_result(plan)
        self.assertEqual(result["status"], 1)
        self.assertEqual(result["result"]["details"]["componentFailures"][0]["fullName"], "Billing")


# ---------------------------------------------------------------------- report


class DeployReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.plan = self.dir / "plan.json"
        self.plan.write_text(
            json.dumps(
                {
                    "test_level": "RunSpecifiedTests",
                    "tests": ["AccountServiceTest"],
                    "apex_changed": [{"name": "AccountService", "type": "ApexClass"}],
                    "untested": [],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.tmp.cleanup()

    def run_report(self, result: dict | None, **extra) -> tuple[str, dict]:
        res = None
        if result is not None:
            res = self.dir / "result.json"
            res.write_text(json.dumps(result), encoding="utf-8")
        args = Namespace(
            env="int", org="int", mode="validate", result=str(res) if res else None, plan=str(self.plan),
            threshold=85.0, from_sha="a" * 40, to_sha="b" * 40, baseline="main", package=None,
            destructive=None, coverage_dir=None, no_changes=False, run_url="https://example.invalid/run", bypass="",
        )
        for k, v in extra.items():
            setattr(args, k, v)
        return deploy_report.build(args)

    @staticmethod
    def sf_result(success: bool, covered: int, **details) -> dict:
        return {
            "status": 0 if success else 1,
            "result": {
                "id": "0Af000000000001",
                "success": success,
                "status": "Succeeded" if success else "Failed",
                "numberComponentsDeployed": 2,
                "numberComponentsTotal": 2,
                "numberComponentErrors": 0 if success else 1,
                "details": {
                    "runTestResult": {
                        "numTestsRun": 3,
                        "numFailures": 0,
                        "codeCoverage": [
                            {"name": "AccountService", "numLocations": 100, "numLocationsNotCovered": 100 - covered}
                        ],
                    },
                    **details,
                },
            },
        }

    def test_success_with_coverage(self):
        text, out = self.run_report(self.sf_result(True, 90))
        self.assertEqual((out["status"], out["passed"], out["coverage_ok"]), ("success", "true", "true"))
        self.assertEqual(out["job_id"], "0Af000000000001")
        self.assertIn("<!-- sf-pipeline-report:int:validate -->", text)
        self.assertIn("🚀 Salesforce Pipeline Report", text)
        self.assertIn("| `AccountService` | 100 | 90 | 90.0% | ✅ |", text)

    def test_uncovered_changed_class_fails_unless_exempt(self):
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        plan["apex_changed"] += [
            {"name": "NeverRun", "type": "ApexClass", "coverage_exempt": False},
            {"name": "Shape", "type": "ApexClass", "coverage_exempt": True},
        ]
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        text, out = self.run_report(self.sf_result(True, 95))
        self.assertEqual((out["status"], out["coverage_ok"]), ("failed", "false"))
        self.assertIn("NeverRun (not covered)", text)
        self.assertIn("| `Shape` | – | – | – | ➖ exempt", text)

    def test_admin_bypass_waives_only_the_coverage_gate(self):
        text, out = self.run_report(self.sf_result(True, 80), bypass="quality gates bypassed by @admin")
        self.assertEqual((out["status"], out["coverage_ok"]), ("success", "false"))
        self.assertIn("**waived**", text)
        failed = self.sf_result(False, 95, componentFailures={"componentType": "ApexClass", "fullName": "X", "problem": "boom"})
        _, out = self.run_report(failed, bypass="quality gates bypassed by @admin")
        self.assertEqual(out["status"], "failed")  # deploy failures are never waived

    def test_coverage_gate_fails_below_threshold(self):
        text, out = self.run_report(self.sf_result(True, 80))
        self.assertEqual((out["status"], out["coverage_ok"], out["deploy_success"]), ("failed", "false", "true"))
        self.assertIn("failed the coverage gate", text)

    def test_component_and_test_failures(self):
        result = self.sf_result(
            False,
            90,
            componentFailures={"componentType": "ApexClass", "fullName": "AccountService", "problem": "Bad | pipe",
                               "fileName": "classes/AccountService.cls", "lineNumber": 3},
        )
        result["result"]["details"]["runTestResult"]["failures"] = [
            {"name": "AccountServiceTest", "methodName": "testIt", "message": "boom", "stackTrace": "line 1"}
        ]
        text, out = self.run_report(result)
        self.assertEqual(out["status"], "failed")
        self.assertIn("Component failures (1)", text)
        self.assertIn("Bad \\| pipe", text)
        self.assertIn("Apex test failures (1)", text)

    def test_top_level_message_without_structured_failures(self):
        text, out = self.run_report({"status": 1, "name": "INVALID_SESSION_ID", "message": "Session expired"})
        self.assertEqual(out["status"], "failed")
        self.assertIn("**INVALID_SESSION_ID**: Session expired", text)

    def test_synthetic_missing_tests(self):
        plan = json.loads(self.plan.read_text(encoding="utf-8"))
        plan["untested"] = [{"name": "Billing", "type": "ApexClass", "searched": ["BillingTest"]}]
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        synthetic = specified_tests.synthetic_result(plan)
        text, out = self.run_report(synthetic)
        self.assertEqual(out["status"], "failed")
        self.assertIn("MissingApexTests", json.dumps(synthetic))
        self.assertIn("Changed Apex without a test class", text)
        self.assertIn("no fallback to RunLocalTests", text)

    def test_no_changes_and_missing_result(self):
        _, out = self.run_report(None, no_changes=True)
        self.assertEqual((out["status"], out["passed"]), ("skipped", "true"))
        text, out = self.run_report(None)
        self.assertEqual(out["status"], "failed")
        self.assertIn("No deploy result was produced", text)

    # ---- real `sf project deploy` output captured from a scratch org (sf 2.153) ----

    def fixture_report(self, fixture: str, changed: list[str]) -> tuple[str, dict]:
        plan = {
            "test_level": "RunSpecifiedTests",
            "tests": [f"{c}Test" for c in changed],
            "apex_changed": [{"name": c, "type": "ApexClass", "coverage_exempt": False} for c in changed],
            "untested": [],
        }
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        data = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
        return self.run_report(data)

    def test_real_success(self):
        text, out = self.fixture_report("validate-success.json", ["FxCalc"])
        self.assertEqual((out["status"], out["coverage_ok"], out["job_id"]), ("success", "true", "0Af000000000001AAA"))
        self.assertIn("| `FxCalc` | 5 | 5 | 100.0% | ✅ |", text)

    def test_real_partial_coverage_fails_85_gate(self):
        text, out = self.fixture_report("validate-partial-coverage.json", ["FxPartial"])
        self.assertEqual((out["deploy_success"], out["coverage_ok"], out["status"]), ("true", "false", "failed"))
        self.assertIn("| `FxPartial` | 5 | 4 | 80.0% | ❌ |", text)

    def test_real_test_failure_after_report_merge(self):
        text, out = self.fixture_report("validate-testfail-merged.json", ["FxCalc"])
        self.assertEqual(out["status"], "failed")
        self.assertIn("Apex test failures (1)", text)
        self.assertIn("math is hard", text)
        self.assertIn("Class.FxFailTest.fails: line 1", text)

    def test_real_compile_error_after_report_merge(self):
        text, out = self.fixture_report("validate-compile-merged.json", ["FxBroken"])
        self.assertEqual(out["status"], "failed")
        self.assertIn("Variable does not exist: undefinedVar", text)
        self.assertIn("classes/FxBroken.cls:1:60", text)

    def test_annotations_point_at_source_lines(self):
        src = self.dir / "force-app" / "main" / "default"
        write(src / "classes" / "FxBroken.cls", "x")
        write(src / "classes" / "FxFailTest.cls", "x")
        compile_data = json.loads((FIXTURES / "validate-compile-merged.json").read_text(encoding="utf-8"))
        test_data = json.loads((FIXTURES / "validate-testfail-merged.json").read_text(encoding="utf-8"))
        cwd = os.getcwd()
        os.chdir(self.dir)
        try:
            comp = deploy_report.annotations(compile_data, "force-app")
            tests = deploy_report.annotations(test_data, "force-app")
        finally:
            os.chdir(cwd)
        self.assertEqual(
            comp,
            ["::error file=force-app/main/default/classes/FxBroken.cls,line=1,col=60,title=ApexClass FxBroken::"
             "Variable does not exist: undefinedVar"],
        )
        self.assertTrue(tests[0].startswith("::error file=force-app/main/default/classes/FxFailTest.cls,line=1,col=1,"))
        self.assertIn("math is hard", tests[0])
        # property values escape the workflow-command separators
        weird = {"result": {"details": {"componentFailures": [
            {"componentType": "Flow", "fullName": "a,b:c", "problem": "x: y, z"}]}}}
        self.assertEqual(deploy_report.annotations(weird, "force-app"), ["::error title=Flow a%2Cb%3Ac::x: y, z"])

    def test_real_cli_error_without_details_shows_message(self):
        text, out = self.fixture_report("validate-testfail-cli.json", ["FxCalc"])
        self.assertEqual(out["status"], "failed")
        self.assertIn("**FailedValidationError**", text)

    def test_notestrun_skips_gate(self):
        plan = {"test_level": "NoTestRun", "tests": [], "apex_changed": [], "untested": []}
        self.plan.write_text(json.dumps(plan), encoding="utf-8")
        text, out = self.run_report({"status": 0, "result": {"success": True, "status": "Succeeded", "details": {}}})
        self.assertEqual(out["status"], "success")
        self.assertIn("not evaluated", text)


# ------------------------------------------------------------------- preflight


class PreflightTests(unittest.TestCase):
    def test_meta_pairs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "classes" / "Ok.cls")
            write(root / "classes" / "Ok.cls-meta.xml")
            write(root / "classes" / "NoMeta.cls")
            write(root / "classes" / "Orphan.cls-meta.xml")
            write(root / "triggers" / "T.trigger")
            problems = [msg for _, msg in preflight.meta_pair_problems(root)]
            self.assertEqual(len(problems), 3)
            self.assertTrue(any("NoMeta.cls has no" in p for p in problems))
            self.assertTrue(any("Orphan.cls-meta.xml has no Orphan.cls" in p for p in problems))
            self.assertTrue(any("T.trigger has no" in p for p in problems))

    def test_pr_route(self):
        self.assertEqual(preflight.pr_route_problems(CFG, "dev", "feature/abc"), [])
        self.assertEqual(preflight.pr_route_problems(CFG, "int", "promote/int/pr-7"), [])
        self.assertTrue(preflight.pr_route_problems(CFG, "int", "feature/abc"))
        self.assertTrue(preflight.pr_route_problems(CFG, "int", "promote/qa/pr-7"))
        self.assertTrue(preflight.pr_route_problems(CFG, "prod", "feature/abc"))
        self.assertEqual(preflight.pr_route_problems(CFG, "prod", "release/2026.10.12.1"), [])
        self.assertTrue(preflight.pr_route_problems(CFG, "dev", "int"))

    def test_branch_origin(self):
        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                return subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True, text=True).stdout.strip()

            git("init", "-q", "-b", "main")
            git("config", "user.email", "ci@example.invalid")
            git("config", "user.name", "ci")
            write(Path(tmp) / "a.txt", "a")
            git("add", "."), git("commit", "-qm", "base")
            git("update-ref", "refs/remotes/origin/main", "HEAD")
            git("checkout", "-qb", "int-local")
            write(Path(tmp) / "b.txt", "b")
            git("add", "."), git("commit", "-qm", "squash: feature A on int")
            git("update-ref", "refs/remotes/origin/int", "HEAD")
            git("checkout", "-qb", "bad-feature")  # cut from int
            write(Path(tmp) / "c.txt", "c")
            git("add", "."), git("commit", "-qm", "feature B")
            bad = git("rev-parse", "HEAD")
            git("checkout", "-q", "-b", "good-feature", "main")  # cut from main
            write(Path(tmp) / "d.txt", "d")
            git("add", "."), git("commit", "-qm", "feature C")
            good = git("rev-parse", "HEAD")

            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                self.assertEqual(preflight.branch_origin_problems(CFG, good), [])
                problems = preflight.branch_origin_problems(CFG, bad)
                # Commits already in the PR's target are allowed (conflict-resolution merges)...
                into_int = preflight.branch_origin_problems(CFG, bad, target_branch="int")
                # ...but still flagged when that head is later promoted elsewhere.
                into_qa = preflight.branch_origin_problems(CFG, bad, target_branch="qa")
            finally:
                os.chdir(cwd)
            self.assertEqual(len(problems), 1)
            self.assertIn("exist only on 'int'", problems[0])
            self.assertEqual(into_int, [])
            self.assertEqual(len(into_qa), 1)


# ---------------------------------------------------------- protection + gh


class BranchProtectionTests(unittest.TestCase):
    GET = {
        "required_status_checks": {"strict": True, "contexts": ["deploy-int"],
                                   "checks": [{"context": "deploy-int", "app_id": None}]},
        "enforce_admins": {"enabled": True},
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True, "require_code_owner_reviews": False,
            "required_approving_review_count": 1, "require_last_push_approval": False,
        },
        "required_linear_history": {"enabled": True},
        "allow_force_pushes": {"enabled": False},
        "allow_deletions": {"enabled": False},
        "required_conversation_resolution": {"enabled": True},
        "required_signatures": {"enabled": True},
    }

    def test_get_to_put_body(self):
        body = branch_protection.to_put_body(self.GET)
        self.assertEqual(body["required_status_checks"], {"strict": True, "checks": [{"context": "deploy-int", "app_id": -1}]})
        self.assertIs(body["enforce_admins"], True)
        self.assertIsNone(body["restrictions"])
        self.assertEqual(body["required_pull_request_reviews"]["required_approving_review_count"], 1)
        self.assertIs(body["required_linear_history"], True)
        self.assertIs(body["lock_branch"], False)
        self.assertTrue(branch_protection.signatures_enabled(self.GET))

    def test_unprotected_sections_become_null(self):
        body = branch_protection.to_put_body({"enforce_admins": {"enabled": False}})
        self.assertIsNone(body["required_status_checks"])
        self.assertIsNone(body["required_pull_request_reviews"])

    def test_desired_protection_from_config(self):
        int_body = branch_protection.desired_protection(CFG, "int")
        prod_body = branch_protection.desired_protection(CFG, "prod")
        contexts = [c["context"] for c in int_body["required_status_checks"]["checks"]]
        self.assertEqual(contexts, ["deploy-int", "sf-approval"])
        self.assertIs(int_body["enforce_admins"], True)
        self.assertIs(int_body["required_linear_history"], True)
        self.assertIs(prod_body["required_linear_history"], False)
        self.assertIs(int_body["required_conversation_resolution"], True)
        self.assertIs(prod_body["required_conversation_resolution"], False)  # the release job merges itself
        # Approval is enforced by the sf-approval status (label OR reviews), not GitHub's review count.
        self.assertEqual(int_body["required_pull_request_reviews"]["required_approving_review_count"], 0)

    def test_desired_protection_round_trips(self):
        """A GET of what we PUT converts back to the same body (so `github` is idempotent)."""
        body = branch_protection.desired_protection(CFG, "qa")
        as_get = {
            "required_status_checks": {"strict": False, "checks": body["required_status_checks"]["checks"]},
            "enforce_admins": {"enabled": True},
            "required_pull_request_reviews": dict(body["required_pull_request_reviews"]),
            **{flag: {"enabled": body[flag]} for flag in branch_protection.FLAGS},
        }
        self.assertEqual(branch_protection.to_put_body(as_get), body)


class GhHelpersTests(unittest.TestCase):
    def test_parse_paginated_stream(self):
        self.assertEqual(gh_helpers.parse_json_stream('[{"a":1}]\n[{"a":2}]'), [{"a": 1}, {"a": 2}])
        self.assertEqual(gh_helpers.parse_json_stream('{"x": 1}'), {"x": 1})
        self.assertIsNone(gh_helpers.parse_json_stream("  "))

    def test_latest_approvers(self):
        reviews = [
            {"user": {"login": "ana"}, "state": "APPROVED"},
            {"user": {"login": "bo"}, "state": "APPROVED"},
            {"user": {"login": "bo"}, "state": "CHANGES_REQUESTED"},
            {"user": {"login": "cy"}, "state": "APPROVED"},
            {"user": {"login": "cy"}, "state": "COMMENTED"},
            {"user": {"login": "dee"}, "state": "APPROVED"},
            {"user": {"login": "dee"}, "state": "DISMISSED"},
            {"user": {"login": "author"}, "state": "APPROVED"},
        ]
        self.assertEqual(gh_helpers.latest_approvers(reviews, "author"), ["ana", "cy"])

    def test_approval_verdict(self):
        verdict = gh_helpers.approval_verdict
        self.assertEqual(verdict(["approved"], [], 1, "approved")[0], True)
        self.assertEqual(verdict(["bug"], ["ana"], 1, "approved")[0], True)
        self.assertEqual(verdict([], [], 0, "approved")[0], True)
        ok, why = verdict(["bug"], ["ana"], 2, "approved")
        self.assertFalse(ok)
        self.assertIn("add the 'approved' label", why)
        self.assertIn("1/2", why)

    def test_labeled_by_uses_the_latest_labeler(self):
        timeline = [
            {"event": "labeled", "label": {"name": "bypass-quality-gates"}, "actor": {"login": "dev1"}},
            {"event": "unlabeled", "label": {"name": "bypass-quality-gates"}, "actor": {"login": "admin"}},
            {"event": "labeled", "label": {"name": "approved"}, "actor": {"login": "lead"}},
            {"event": "labeled", "label": {"name": "bypass-quality-gates"}, "actor": {"login": "admin"}},
        ]
        self.assertEqual(gh_helpers.labeled_by(timeline, "bypass-quality-gates"), "admin")
        self.assertIsNone(gh_helpers.labeled_by(timeline, "deploy:qa"))

    def test_squash_subject_pr_reference(self):
        self.assertEqual(gh_helpers._PR_REF.search("E2E: tier service (#4)").group(1), "4")
        self.assertIsNone(gh_helpers._PR_REF.search("Merge branch 'main' into dev"))

    def test_pr_marker_round_trip(self):
        body = "Release notes\n\n- change"
        once = gh_helpers.write_marker(body, "sf-release", {"sha": "abc", "job_id": "0Af1"})
        self.assertEqual(gh_helpers.read_marker(once, "sf-release"), {"sha": "abc", "job_id": "0Af1"})
        twice = gh_helpers.write_marker(once, "sf-release", {"sha": "def"})
        self.assertEqual(twice.count("<!-- sf-release"), 1)
        self.assertEqual(gh_helpers.read_marker(twice, "sf-release"), {"sha": "def"})
        self.assertTrue(twice.startswith("Release notes"))
        self.assertEqual(gh_helpers.read_marker("no marker", "sf-release"), {})


class CodeAnalysisTests(unittest.TestCase):
    DATA = json.loads((FIXTURES / "code-analyzer.json").read_text(encoding="utf-8"))

    def test_real_output_engine_errors_dont_block(self):
        result = code_analysis.analyze(self.DATA, threshold=2)
        self.assertEqual(len(result["violations"]), 6)          # ApexDoc (Low)
        self.assertEqual(len(result["engine_errors"]), 1)       # flow engine without Python
        self.assertEqual(result["blocking"], [])
        text = code_analysis.render(result, "dev", 2, "")
        self.assertIn("<!-- sf-pipeline-analysis:dev -->", text)
        self.assertIn("No violations at High or worse", text)
        self.assertIn("Engine `flow` didn't run", text)
        self.assertEqual(code_analysis.annotations(result, 2), [])  # Low findings aren't annotated

    def test_threshold_blocks_and_annotates(self):
        data = {"violations": [
            {"rule": "ApexCRUDViolation", "engine": "pmd", "severity": 2, "primaryLocationIndex": 0,
             "locations": [{"file": "force-app/main/default/classes/A.cls", "startLine": 7, "startColumn": 3}],
             "message": "Validate CRUD permission before SOQL/DML operation"},
            {"rule": "AvoidDebugStatements", "engine": "pmd", "severity": 3, "primaryLocationIndex": 0,
             "locations": [{"file": "force-app/main/default/classes/A.cls", "startLine": 9}], "message": "Avoid debug"},
        ]}
        result = code_analysis.analyze(data, threshold=2)
        self.assertEqual(len(result["blocking"]), 1)
        notes = code_analysis.annotations(result, 2)
        self.assertTrue(notes[0].startswith("::error file=force-app/main/default/classes/A.cls,line=7,col=3,title=High ApexCRUDViolation::"))
        self.assertTrue(notes[1].startswith("::warning "))
        self.assertIn("1 blocking violation", code_analysis.render(result, "dev", 2, ""))
        self.assertEqual(code_analysis.analyze(data, threshold=1)["blocking"], [])
        self.assertIn("**waived**", code_analysis.render(result, "dev", 2, "", bypass="bypassed by @admin"))


def _working_python3() -> bool:
    exe = shutil.which("python3")
    if not exe:
        return False
    try:
        return subprocess.run([exe, "-c", "print(1)"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


class _Capture(http.server.BaseHTTPRequestHandler):
    received: list = []

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        type(self).received.append((self.path, dict(self.headers), self.rfile.read(length).decode("utf-8")))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):  # silence
        pass


@unittest.skipUnless(shutil.which("bash") and shutil.which("curl") and _working_python3(),
                     "needs bash, curl and python3 (as on GitHub runners)")
class NotifyScriptTests(unittest.TestCase):
    """notify.sh against a local HTTP endpoint: provider detection and payloads."""

    @classmethod
    def setUpClass(cls):
        cls.server = http.server.HTTPServer(("127.0.0.1", 0), _Capture)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def send(self, message: str, **env) -> tuple[str, dict, dict]:
        _Capture.received.clear()
        run_env = dict(os.environ, TMPDIR=tempfile.gettempdir(), CHAT_WEBHOOK_URL="", CHAT_API_URL="",
                       CHAT_API_TOKEN="", CHAT_CHANNEL_ID="", CHAT_PROVIDER="auto")
        run_env.update(env)
        proc = subprocess.run(["bash", str(SCRIPTS / "notify.sh"), message], env=run_env,
                              capture_output=True, text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(len(_Capture.received), 1, proc.stdout + proc.stderr)
        path, headers, body = _Capture.received[0]
        return path, headers, json.loads(body)

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def test_providers(self):
        _, _, body = self.send("hi *there*", CHAT_WEBHOOK_URL=self.url("/hooks"), CHAT_PROVIDER="slack")
        self.assertEqual(body, {"text": "hi *there*"})
        _, _, body = self.send("hi", CHAT_WEBHOOK_URL=self.url("/teams"), CHAT_PROVIDER="teams")
        self.assertEqual((body["@type"], body["text"]), ("MessageCard", "hi"))
        _, _, body = self.send("**bold**", CHAT_WEBHOOK_URL=self.url("/webex"), CHAT_PROVIDER="webex")
        self.assertEqual(body, {"markdown": "**bold**"})
        _, _, body = self.send('quote " and\nnewline', CHAT_WEBHOOK_URL=self.url("/any"))  # auto -> generic
        self.assertEqual(body["markdown"], 'quote " and\nnewline')
        _, headers, body = self.send("api", CHAT_API_URL=self.url("/v1/messages"), CHAT_API_TOKEN="t0k",
                                     CHAT_CHANNEL_ID="room-1")
        self.assertEqual(headers.get("Authorization"), "Bearer t0k")
        self.assertEqual((body["roomId"], body["markdown"]), ("room-1", "api"))

    def test_unconfigured_is_a_silent_no_op(self):
        proc = subprocess.run(["bash", str(SCRIPTS / "notify.sh"), "x"], capture_output=True, text=True,
                              env=dict(os.environ, CHAT_WEBHOOK_URL="", CHAT_API_URL="", CHAT_PROVIDER="auto"))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("not configured", proc.stdout)


class JiraAndAuditTests(unittest.TestCase):
    def test_jira_keys_and_no_op_without_credentials(self):
        self.assertEqual(sorted(set(jira.KEY_RE.findall("feature/PROJ-12-x fixes ABC-1 and PROJ-12"))), ["ABC-1", "PROJ-12"])
        env = {k: v for k, v in os.environ.items() if not k.startswith("JIRA_")}
        proc = subprocess.run([sys.executable, str(SCRIPTS / "jira.py"), "deploy-text", "PROJ-1", "int", "In Int"],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("credentials not configured", proc.stdout)

    def test_audit_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            pkg = package_xml(Path(tmp) / "package.xml", {"ApexClass": ["A", "B"], "CustomLabel": ["L"]})
            env = dict(os.environ, AUDIT_DIR=str(Path(tmp) / "out"), AUDIT_TYPE="Release", AUDIT_ENVIRONMENT="prod",
                       AUDIT_STATUS="success", AUDIT_SHA="0123456789abcdef", AUDIT_PR="7", AUDIT_DEPLOY_ID="0Af1",
                       AUDIT_TEST_LEVEL="RunLocalTests", AUDIT_COVERAGE="91.5", AUDIT_THRESHOLD="85",
                       AUDIT_PACKAGE=str(pkg), GITHUB_STEP_SUMMARY=str(Path(tmp) / "summary.md"))
            proc = subprocess.run([sys.executable, str(SCRIPTS / "audit.py")], capture_output=True, text=True,
                                  env=env, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            record = json.loads((Path(tmp) / "out" / "audit-record.json").read_text(encoding="utf-8"))
            self.assertEqual((record["type"], record["environment"], record["status"]), ("Release", "prod", "success"))
            self.assertEqual((record["components_total"], record["components_by_type"]),
                             (3, {"ApexClass": 2, "CustomLabel": 1}))
            self.assertEqual((record["coverage_percent"], record["test_level"], record["short_sha"]),
                             (91.5, "RunLocalTests", "01234567"))
            self.assertIn("Audit — Release → prod", (Path(tmp) / "summary.md").read_text(encoding="utf-8"))

    def test_notification_config_validation(self):
        cfg = pipeline_config.from_dict({"environments": {"dev": {"jira_status": "In Dev"}, "prod": {}}})
        view = pipeline_config.env_view(cfg, "dev")
        self.assertEqual((view["jira_status"], view["chat_provider"], view["audit_branch"]), ("In Dev", "auto", "audit-log"))
        with self.assertRaises(pipeline_config.ConfigError) as ctx:
            pipeline_config.from_dict({"environments": {"dev": {}, "prod": {}},
                                       "notifications": {"chat": {"provider": "carrier-pigeon"}}})
        self.assertIn("notifications.chat.provider", str(ctx.exception))


class DependencyLintAndProfilesTests(unittest.TestCase):
    def test_deleting_a_referenced_class_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write(root / "force-app/main/default/classes/Keeper.cls", "public class Keeper { Integer x() { return Doomed.y(); } }")
            package_xml(root / ".sgd/destructiveChanges/destructiveChanges.xml", {"ApexClass": ["Doomed"]})
            package_xml(root / ".sgd/package/package.xml", {})
            proc = subprocess.run([sys.executable, str(SCRIPTS / "dependency_lint.py"), "--dir", ".sgd",
                                   "--source-root", "force-app"], cwd=root, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 1, proc.stdout)
            self.assertIn("'Doomed' is being DELETED but is still referenced", proc.stdout)
            # nothing references what's deleted -> passes
            (root / "force-app/main/default/classes/Keeper.cls").write_text("public class Keeper {}", encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPTS / "dependency_lint.py"), "--dir", ".sgd",
                                   "--source-root", "force-app"], cwd=root, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stdout)

    def test_incremental_permission_set_keeps_only_changed_entries(self):
        def fls(field: str, edit: str) -> str:
            return (f"<fieldPermissions><editable>{edit}</editable><field>{field}</field>"
                    f"<readable>true</readable></fieldPermissions>")

        def permset(edit_b: str) -> str:
            return ('<?xml version="1.0" encoding="UTF-8"?>\n<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">'
                    + fls("Account.A__c", "false") + fls("Account.B__c", edit_b)
                    + "<label>Sales</label></PermissionSet>\n")

        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                subprocess.run(["git", *args], cwd=tmp, check=True, capture_output=True)

            ps = Path(tmp) / "force-app/main/default/permissionsets/Sales.permissionset-meta.xml"
            git("init", "-q", "-b", "main")
            git("config", "user.email", "ci@example.invalid")
            git("config", "user.name", "ci")
            write(ps, permset("false"))
            git("add", "."), git("commit", "-qm", "baseline")
            ps.write_text(permset("true"), encoding="utf-8")  # only Account.B__c changes
            pkg = package_xml(Path(tmp) / ".sgd/package/package.xml", {"PermissionSet": ["Sales"]})
            proc = subprocess.run([sys.executable, str(SCRIPTS / "profiles.py"), "--package", str(pkg),
                                   "--source-root", "force-app", "--mode", "incremental", "--baseline", "main"],
                                  cwd=tmp, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            trimmed = ps.read_text(encoding="utf-8")
            self.assertIn("Account.B__c", trimmed)
            self.assertNotIn("Account.A__c", trimmed)       # unchanged entry stripped
            self.assertIn("<label>Sales</label>", trimmed)    # required singleton kept


class BootstrapTests(unittest.TestCase):
    def test_labels_for_five_and_four_stages(self):
        five = bootstrap.wanted_labels(CFG)
        self.assertEqual(
            sorted(five),
            sorted(["approved", "bypass-quality-gates", "promote:int", "promote:qa", "promote:stage",
                    "deploy:dev", "deploy:int", "deploy:qa", "deploy:stage"]),
        )
        four = bootstrap.wanted_labels(
            pipeline_config.from_dict({"environments": {"int": {}, "qa": {}, "stage": {}, "prod": {}}})
        )
        self.assertNotIn("promote:int", four)  # the entry env is never a promotion target
        self.assertIn("promote:qa", four)
        self.assertNotIn("deploy:prod", four)

    def test_parse_pairs(self):
        self.assertEqual(bootstrap.parse_pairs(["dev=sfp-dev,int=sfp-int", "qa=x"], "--org"),
                         {"dev": "sfp-dev", "int": "sfp-int", "qa": "x"})
        with self.assertRaises(bootstrap.SetupError):
            bootstrap.parse_pairs(["dev"], "--org")

    def test_environment_settings_parse(self):
        current = {
            "deployment_branch_policy": {"protected_branches": True, "custom_branch_policies": False},
            "protection_rules": [
                {"type": "wait_timer", "wait_timer": 5},
                {"type": "required_reviewers", "prevent_self_review": True,
                 "reviewers": [{"type": "User", "reviewer": {"id": 42, "login": "ana"}}]},
                {"type": "branch_policy"},
            ],
        }
        policy, reviewers, other = bootstrap.environment_settings(current)
        self.assertEqual(policy, bootstrap.PROTECTED_POLICY)
        self.assertEqual(reviewers, [{"type": "User", "id": 42}])
        self.assertEqual(other, {"wait_timer": 5, "prevent_self_review": True})
        self.assertEqual(bootstrap.environment_settings({"deployment_branch_policy": None}), (None, [], {}))

    def test_consumer_keys_are_stable_per_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            first = bootstrap.consumer_key_for(d, "qa")
            self.assertEqual(bootstrap.consumer_key_for(d, "qa"), first)
            self.assertNotEqual(bootstrap.consumer_key_for(d, "int"), first)
            self.assertTrue(first.startswith("SFPipeline") and len(first) > 40)

    def test_eca_templates_render_valid_xml(self):
        import xml.etree.ElementTree as ET
        values = {"n": bootstrap.ECA_NAME, "cert": "-----BEGIN CERTIFICATE-----\nAAA\n-----END CERTIFICATE-----",
                  "consumer_key": "SFPipelineX", "profile": "System Administrator", "email": "ci@example.com"}
        for rel, template in bootstrap.ECA_FILES.items():
            root = ET.fromstring(template.format(**values))
            self.assertTrue(root.tag.endswith(("ExternalClientApplication", "ExtlClntAppOauthSettings",
                                               "ExtlClntAppGlobalOauthSettings", "ExtlClntAppOauthConfigurablePolicies")), rel)
        policy = bootstrap.ECA_FILES["extlClntAppOauthPolicies/{n}_oauthPlcy.ecaOauthPlcy-meta.xml"].format(**values)
        self.assertIn("<permittedUsersPolicyType>AdminApprovedPreAuthorized</permittedUsersPolicyType>", policy)

    def test_init_rewrites_only_the_environment_block(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pipeline.config.yml"
            path.write_text(
                "# header\nversion: 1\n\nenvironments:\n  dev: {}\n  int: {}\n\n  prod: {}\n\nquality:\n  coverage_threshold: 90\n",
                encoding="utf-8",
            )
            args = Namespace(envs="int, qa ,stage,prod", config=str(path), dry_run=False)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(bootstrap.cmd_init(args), 0)
            text = path.read_text(encoding="utf-8")
            self.assertIn("# header", text)
            self.assertIn("coverage_threshold: 90", text)
            cfg = pipeline_config.load(path)
            self.assertEqual(cfg["promotion"]["chain"], ["int", "qa", "stage", "prod"])
            self.assertEqual(cfg["quality"]["coverage_threshold"], 90)


if __name__ == "__main__":
    unittest.main()
