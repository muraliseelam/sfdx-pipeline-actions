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


def plan(cfg_text, *args, expect_fail=False, code=None):
    """Run plan.py against a config and return its $GITHUB_OUTPUT as a dict.

    `code` is the config of the change being shipped (--code-config), as the
    plan action passes it when the gates come from a PR's base branch.
    """
    with tempfile.TemporaryDirectory() as tmp:
        cfg = os.path.join(tmp, "pipeline.config.yml")
        with open(cfg, "w", encoding="utf-8") as fh:
            fh.write(cfg_text)
        extra = []
        if code is not None:
            code_path = os.path.join(tmp, "code.config.yml")
            with open(code_path, "w", encoding="utf-8") as fh:
                fh.write(code)
            extra = ["--code-config", code_path]
        out = os.path.join(tmp, "gh_output")
        open(out, "w").close()
        env = dict(os.environ, GITHUB_OUTPUT=out)
        proc = subprocess.run(
            [sys.executable, PLAN, "--config", cfg, *extra, *args],
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

    o = plan(WEEKDAYS, "resolve", "tue")
    check("resolve is keyed by branch: an env NAME is inert", (o["r_name"], o["r_is_stage"]), ("", "false"))


def test_prdeploy_chain():
    print("\n\033[1mprdeploy (environment -> deploy target) — used by promotion\033[0m")
    o = plan(WEEKDAYS, "prdeploy", "mon")
    check("entry stage is flagged as entry", o["r_is_entry"], "true")
    check("entry stage has no predecessor to wait for", o["r_require_env"], "")
    check("entry stage requires no earlier deploys", o["r_require_envs"], "")
    check("entry stage knows its own branch", o["r_branch"], "env/monday")
    check("entry stage knows the next stage", o["r_next_name"], "tue")
    check("entry stage knows the next branch", o["r_next_branch"], "env/tuesday")

    o = plan(WEEKDAYS, "prdeploy", "wed")
    check("mid stage is not entry", o["r_is_entry"], "false")
    check("mid stage requires its predecessor", o["r_require_env"], "tue")
    check("mid stage requires EVERY earlier stage", o["r_require_envs"], "mon,tue")
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
    check("it is also the last environment", o["last_env"], "sandbox")
    check("gh_environment defaults to the name", json.loads(o["auto_json"])[0]["gh_environment"], "sandbox")
    check("test level defaults", json.loads(o["auto_json"])[0]["test_level"], "RunSpecifiedTests")
    check("coverage threshold defaults", o["coverage_threshold"], "75")

    o = plan(MINIMAL, "prdeploy", "sandbox")
    check("the only stage is the entry stage", o["r_is_entry"], "true")
    check("and has no next stage", o["r_next_name"], "")
    check("and nothing before it", (o["r_require_env"], o["r_require_envs"]), ("", ""))
    o = plan(MINIMAL, "resolve", "sandbox")
    check("its branch resolves to it", (o["r_name"], o["r_gate"]), ("sandbox", "approval"))


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


def test_trusted_config():
    print("\n\033[1mGates come from the trusted (base-branch) config, code keys from the change\033[0m")
    # The PR's own copy relaxes every gate and re-maps the chain, as PR #52 did.
    tampered = """
source_path: src/app
api_version: "64.0"
node_version: "20"
baseline_branch: evil
quality_gates: { apex_coverage: false }
coverage_threshold: 1
bypass_label: anything
require_pr_approval: false
allow_self_approval: true
environments:
  - { name: thu, branch: env/thursday, gh_environment: live-validate, gate: auto, test_level: NoTestRun }
production: { branch: production, validate_environment: qa }
"""
    trusted = WEEKDAYS + "quality_gates: { apex_coverage: true }\n"
    o = plan(trusted, "outputs", code=tampered)
    check("the coverage gate stays on", o["q_apex_coverage"], "true")
    check("the coverage threshold stays", o["coverage_threshold"], "90")
    check("the bypass label stays", o["bypass_label"], "bypass-quality-gates")
    check("approval stays required", o["require_pr_approval"], "true")
    check("self-approval stays off", o["allow_self_approval"], "false")
    check("the baseline stays", o["baseline_branch"], "production")
    check("the entry branch stays", o["entry_branch"], "env/monday")
    check("the environment mapping stays", json.loads(o["auto_json"])[3]["gh_environment"], "thursday-org")
    check("the prod validate environment stays", o["prod_validate_env"], "live-validate")
    check("source_path follows the change", o["source_path"], "src/app")
    check("api_version follows the change", o["api_version"], "64.0")
    check("node_version follows the change", o["node_version"], "20")

    o = plan(trusted, "prdeploy", "thu", code=tampered)
    check("prdeploy ignores the change's chain", o["r_require_envs"], "mon,tue,wed")

    o = plan(WEEKDAYS, "outputs", code="baseline_branch: x\n")
    check("a change without code keys falls back to defaults", o["source_path"], "force-app")
    o = plan(WEEKDAYS, "outputs")
    check("without --code-config the config supplies them", o["api_version"], "62.0")


def test_self_approval_flag():
    print("\n\033[1mallow_self_approval\033[0m")
    check("defaults to off", plan(MINIMAL, "outputs")["allow_self_approval"], "false")
    check("can be turned on", plan(MINIMAL + "allow_self_approval: true\n", "outputs")["allow_self_approval"], "true")


def test_unsafe_values():
    print("\n\033[1mValues a PR supplies are plain paths / versions\033[0m")
    for bad in ('force-app"; curl x | sh; echo "', "../outside", "/abs/path", "$(id)"):
        p = plan(MINIMAL, "outputs", code=f"source_path: '{bad}'\n", expect_fail=True)
        check(f"rejects source_path {bad!r}", p.returncode != 0, True)
    p = plan(MINIMAL, "outputs", code="api_version: '66.0`id`'\n", expect_fail=True)
    check("rejects an api_version with shell characters", p.returncode != 0, True)
    p = plan(MINIMAL, "outputs", code="node_version: '22; id'\n", expect_fail=True)
    check("rejects a node_version with shell characters", p.returncode != 0, True)
    for bad in ("../../etc", "/usr/bin", "22/../.."):
        p = plan(MINIMAL, "outputs", code=f"node_version: '{bad}'\n", expect_fail=True)
        check(f"rejects node_version {bad!r} like source_path", p.returncode != 0, True)
    o = plan(MINIMAL, "outputs", code="source_path: force-app/main\nnode_version: lts/*\napi_version: 66\n")
    check("accepts a nested source_path", o["source_path"], "force-app/main")
    check("accepts lts/* as node_version", o["node_version"], "lts/*")
    check("accepts an integer api_version", o["api_version"], "66")
    for good in ("22", "22.x", "20.11.1", "lts/iron", "latest"):
        o = plan(MINIMAL, "outputs", code=f"node_version: '{good}'\n")
        check(f"accepts node_version {good!r}", o["node_version"], good)
    for bad in ("force-app; echo x", "force-app\necho x"):
        p = plan(MINIMAL, "outputs", code=f"source_path: {json.dumps(bad)}\n", expect_fail=True)
        check(f"rejects source_path {bad!r}", p.returncode != 0, True)


def test_outputs_declared():
    print("\n\033[1mEvery plan.py output is declared by the plan action\033[0m")
    # An undeclared output reads as "" in every consumer, silently.
    import yaml
    with open(os.path.join(ROOT, "sf-pipeline", "plan", "action.yml"), encoding="utf-8") as fh:
        declared = set((yaml.safe_load(fh).get("outputs") or {}).keys())
    emitted = set()
    for args in (("outputs",), ("resolve", "env/tuesday"), ("promote", "wed"), ("prdeploy", "wed")):
        emitted |= set(plan(WEEKDAYS, *args).keys())
    check("no emitted key is missing from action.yml", sorted(emitted - declared), [])


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
        test_trusted_config,
        test_self_approval_flag,
        test_unsafe_values,
        test_outputs_declared,
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
