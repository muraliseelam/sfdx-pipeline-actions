#!/usr/bin/env python3
"""
test_config_matrix.py — prove the pipeline is environment-name agnostic.

The promise this repo makes is: "change pipeline.config.yml and go." Every
branch name, environment name and label is supposed to come from that one file,
so a team whose environments are mon/tue/wed/thu must work exactly as well as
one using dev/int/qa/stage.

These tests drive plan.py — the only component that reads the config, and the
thing every workflow asks for its branch, environment and label names — across
several very different configs, and assert the resolutions are right.

Run:  python3 tests/test_config_matrix.py
"""
import json
import os
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLAN = os.path.join(ROOT, "sf-pipeline", "scripts", "plan.py")

FAILURES = []
CHECKS = 0


def check(label, actual, expected):
    global CHECKS
    CHECKS += 1
    if actual != expected:
        FAILURES.append(f"{label}\n      expected: {expected!r}\n      actual:   {actual!r}")
        print(f"  \033[31m\u2717\033[0m {label}")
    else:
        print(f"  \033[32m\u2713\033[0m {label}")


def plan(cfg_text, *args, expect_fail=False):
    """Run plan.py against a config and return its $GITHUB_OUTPUT as a dict."""
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "pipeline.config.yml")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(cfg_text)
        out = os.path.join(tmp, "gh_output")
        open(out, "w").close()
        env = dict(os.environ, GITHUB_OUTPUT=out)
        proc = subprocess.run(
            [sys.executable, PLAN, "--config", cfg, *args],
            capture_output=True, text=True, env=env,
        )
        if expect_fail:
            return proc
        if proc.returncode != 0:
            raise AssertionError(f"plan.py {' '.join(args)} failed:\n{proc.stderr}")
        result, key = {}, None
        for line in open(out, encoding="utf-8"):
            line = line.rstrip("\n")
            if key is not None:
                if line == "__EOF__":
                    key = None
                else:
                    result[key] += line
            elif line.endswith("<<__EOF__"):
                key = line[: -len("<<__EOF__")]
                result[key] = ""
            elif "=" in line:
                k, _, v = line.partition("=")
                result[k] = v
        return result


# ---------------------------------------------------------------------------
# A deliberately hostile config: non-standard environment names, five stages,
# and branch names that differ from the environment names (so anything that
# confuses "name" with "branch" fails here).
# ---------------------------------------------------------------------------
WEEKDAYS = """
source_path: force-app
api_version: "62.0"
baseline_branch: production
environments:
  - name: mon
    branch: env/monday
    gh_environment: monday-org
    gate: auto
  - name: tue
    branch: env/tuesday
    gh_environment: tuesday-org
    gate: approval
  - name: wed
    branch: env/wednesday
    gh_environment: wednesday-org
    gate: on_demand
  - name: thu
    branch: env/thursday
    gh_environment: thursday-org
    gate: on_demand
production:
  branch: production
  deploy_environment: live
  validate_environment: live-validate
  release_branch_prefix: "rel/"
coverage_threshold: 90
"""

# The opposite extreme: the smallest config that can work.
MINIMAL = """
source_path: src
baseline_branch: main
environments:
  - name: sandbox
    branch: sandbox
production:
  branch: main
  deploy_environment: prod
  validate_environment: prod-validate
"""


def test_weekday_chain():
    print("\n\033[1mArbitrary environment names (mon/tue/wed/thu), branch != name\033[0m")
    o = plan(WEEKDAYS, "outputs")
    check("entry_branch is the first env's branch", o["entry_branch"], "env/monday")
    check("env_count counts every stage", o["env_count"], "4")
    check("last_env is the final stage", o["last_env"], "thu")
    check("release_label follows the last stage", o["release_label"], "deployed:thu")
    check("baseline_branch passes through", o["baseline_branch"], "production")
    check("prod deploy environment", o["prod_deploy_env"], "live")
    check("prod validate environment", o["prod_validate_env"], "live-validate")
    check("release prefix", o["release_prefix"], "rel/")
    check("coverage threshold", o["coverage_threshold"], "90")
    check(
        "env_branches lists every branch in order",
        json.loads(o["env_branches"]),
        ["env/monday", "env/tuesday", "env/wednesday", "env/thursday"],
    )
    check(
        "auto_json carries name + branch + gh_environment",
        [(e["name"], e["branch"], e["gh_environment"]) for e in json.loads(o["auto_json"])],
        [
            ("mon", "env/monday", "monday-org"),
            ("tue", "env/tuesday", "tuesday-org"),
            ("wed", "env/wednesday", "wednesday-org"),
            ("thu", "env/thursday", "thursday-org"),
        ],
    )


def test_resolve_by_branch():
    print("\n\033[1mresolve (branch -> environment) — used by PR validation\033[0m")
    o = plan(WEEKDAYS, "resolve", "env/tuesday")
    check("resolves the environment name", o["r_name"], "tue")
    check("resolves the GitHub Environment", o["r_gh_environment"], "tuesday-org")
    check("an approval stage is PR-driven", o["r_is_stage"], "true")

    o = plan(WEEKDAYS, "resolve", "refs/heads/env/monday")
    check("accepts a full refs/heads/ ref", o["r_name"], "mon")

    o = plan(WEEKDAYS, "resolve", "env/wednesday")
    check("an on-demand stage is NOT PR-driven", o["r_is_stage"], "false")

    o = plan(WEEKDAYS, "resolve", "rel/2025.07")
    check("a release branch validates against prod-validate", o["r_gh_environment"], "live-validate")

    o = plan(WEEKDAYS, "resolve", "some/random/branch")
    check("an unmapped branch is inert, not an error", o["r_is_stage"], "false")


def test_prdeploy_chain():
    print("\n\033[1mprdeploy (environment -> deploy target) — used by promotion\033[0m")
    o = plan(WEEKDAYS, "prdeploy", "mon")
    check("entry stage is flagged as entry", o["r_is_entry"], "true")
    check("entry stage has no predecessor to wait for", o["r_require_env"], "")
    check("entry stage knows its own branch", o["r_branch"], "env/monday")
    check("entry stage knows the next stage", o["r_next_name"], "tue")
    check("entry stage knows the next branch", o["r_next_branch"], "env/tuesday")

    o = plan(WEEKDAYS, "prdeploy", "wed")
    check("mid stage is not entry", o["r_is_entry"], "false")
    check("mid stage requires its predecessor", o["r_require_env"], "tue")
    check("mid stage resolves its GitHub Environment", o["r_gh_environment"], "wednesday-org")
    check("mid stage resolves its branch", o["r_branch"], "env/wednesday")

    o = plan(WEEKDAYS, "prdeploy", "thu")
    check("last stage has no next stage", o["r_next_name"], "")
    check("last stage has no next branch", o["r_next_branch"], "")


def test_promote_guards():
    print("\n\033[1mpromote (manual promotion) — guard rails\033[0m")
    o = plan(WEEKDAYS, "promote", "wed")
    check("promotes from the previous stage's branch", o["r_source_branch"], "env/tuesday")
    check("targets its own branch", o["r_branch"], "env/wednesday")
    check("a PR-driven predecessor needs no ordering guard", o["r_require_env"], "")

    o = plan(WEEKDAYS, "promote", "thu")
    check("an on-demand predecessor DOES need the guard", o["r_require_env"], "wed")

    p = plan(WEEKDAYS, "promote", "tue", expect_fail=True)
    check("refuses to manually promote a PR-driven stage", p.returncode != 0, True)
    check("and names the valid targets", "wed, thu" in p.stderr, True)

    p = plan(WEEKDAYS, "promote", "friday", expect_fail=True)
    check("rejects an unknown environment", p.returncode != 0, True)
    check("and lists the configured names", "mon, tue, wed, thu" in p.stderr, True)

    p = plan(WEEKDAYS, "prdeploy", "friday", expect_fail=True)
    check("prdeploy also rejects an unknown environment", p.returncode != 0, True)


def test_minimal_config():
    print("\n\033[1mSingle-environment config\033[0m")
    o = plan(MINIMAL, "outputs")
    check("works with one environment", o["env_count"], "1")
    check("entry branch is that environment", o["entry_branch"], "sandbox")
    check("release label follows it", o["release_label"], "deployed:sandbox")
    check("gh_environment defaults to the name", json.loads(o["auto_json"])[0]["gh_environment"], "sandbox")
    check("test level defaults", json.loads(o["auto_json"])[0]["test_level"], "RunSpecifiedTests")
    check("coverage threshold defaults", o["coverage_threshold"], "75")

    o = plan(MINIMAL, "prdeploy", "sandbox")
    check("the only stage is the entry stage", o["r_is_entry"], "true")
    check("and has no next stage", o["r_next_name"], "")


def test_gate_defaults():
    print("\n\033[1mGate defaults\033[0m")
    cfg = """
baseline_branch: main
require_pr_approval: false
environments:
  - name: a
    branch: a
  - name: b
    branch: b
production: { branch: main }
"""
    envs = json.loads(plan(cfg, "outputs")["auto_json"])
    check("first env follows require_pr_approval=false", envs[0]["gate"], "auto")
    check("later envs default to on_demand", envs[1]["gate"], "on_demand")

    envs = json.loads(plan(cfg.replace("false", "true"), "outputs")["auto_json"])
    check("first env follows require_pr_approval=true", envs[0]["gate"], "approval")


def test_release_label_override():
    print("\n\033[1mrelease_label override\033[0m")
    cfg = WEEKDAYS.replace(
        "  release_branch_prefix:", "  release_label: ready-to-ship\n  release_branch_prefix:"
    )
    check("an explicit release_label wins", plan(cfg, "outputs")["release_label"], "ready-to-ship")


def test_empty_environments():
    print("\n\033[1mBad config is rejected loudly\033[0m")
    p = plan("baseline_branch: main\nenvironments: []\n", "outputs", expect_fail=True)
    check("no environments is a hard error", p.returncode != 0, True)
    check("with an actionable message", "no `environments`" in p.stderr, True)


def main():
    print("Environment-name portability suite")
    print(f"plan.py: {PLAN}")
    for fn in (
        test_weekday_chain,
        test_resolve_by_branch,
        test_prdeploy_chain,
        test_promote_guards,
        test_minimal_config,
        test_gate_defaults,
        test_release_label_override,
        test_empty_environments,
    ):
        fn()

    print()
    if FAILURES:
        print(f"\033[31m{len(FAILURES)} of {CHECKS} checks FAILED\033[0m\n")
        for f in FAILURES:
            print(f"  \u2717 {f}\n")
        return 1
    print(f"\033[32mAll {CHECKS} checks passed.\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
