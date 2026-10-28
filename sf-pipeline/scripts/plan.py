#!/usr/bin/env python3
"""
plan.py — read sf-pipeline/pipeline.config.yml and expose it to the workflows.

Two modes:

  plan.py outputs
      Emit all pipeline settings as GitHub Actions step outputs (to $GITHUB_OUTPUT),
      including `auto_json` — the ordered environment list used to drive the
      dynamic deploy matrix (works for any number of orgs).

  plan.py resolve <branch>
      Map a git branch (a PR base ref) to the environment it validates against.
      Emits: r_name, r_gh_environment, r_test_level.

  plan.py promote <env>
      Resolve a MANUAL promotion target (qa, stage, ...): the env to deploy to
      plus the branch it is promoted FROM (the previous env in the chain).
      Emits: r_name, r_gh_environment, r_branch, r_source_branch, r_test_level.

The single config file is the only thing a new project edits; this script keeps
the workflows generic so they never hardcode env names, branches or org counts.

Trust: --config is the file the gates and environment mapping come from. When a
workflow acts on a pull request, the plan action fetches it from the PR's
(protected) base branch, so a PR can't relax the gates that judge it. The keys
that describe the code being shipped (CODE_KEYS) are then read from
--code-config, the PR's own checkout.
"""
import argparse
import json
import os
import re
import sys

DEFAULT_CONFIG = os.path.join("sf-pipeline", "pipeline.config.yml")

# Keys that describe the change itself rather than policy. They follow the code
# (a PR that moves its metadata changes source_path in the same PR); everything
# else (gates, thresholds, environments, production) comes from --config.
CODE_KEYS = ("source_path", "api_version", "node_version")

# These values can come from a PR, and workflows interpolate them into shell, so
# only plain path / version characters are accepted.
SAFE_VALUE = {
    "source_path": re.compile(r"^(?!/)(?!.*(^|/)\.\.(/|$))[A-Za-z0-9_.][A-Za-z0-9_./-]*$"),
    "api_version": re.compile(r"^[0-9]+(\.[0-9]+)?$"),
    # 22, 22.x, 20.11.1, lts/*, lts/iron, latest — never a path out of the tree.
    "node_version": re.compile(r"^(?!/)(?!.*\.\.)[A-Za-z0-9.*/_-]+$"),
}

# production.destructive_tests: Apex test class names, comma/space separated
# (blank = none). The deploy step passes them through env, but a name that isn't
# an Apex identifier is a config mistake, so refuse it here, loudly.
APEX_TEST_LIST = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*([\s,]+[A-Za-z][A-Za-z0-9_]*)*)?\s*$")


def load_config(path):
    try:
        import yaml  # PyYAML is preinstalled on GitHub-hosted runners
    except ImportError:  # pragma: no cover - defensive
        os.system(f"{sys.executable} -m pip install --quiet pyyaml")
        import yaml
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def emit(outputs):
    """Write key=value / key<<EOF blocks to $GITHUB_OUTPUT (and echo for logs)."""
    gh_out = os.environ.get("GITHUB_OUTPUT")
    lines = []
    for key, val in outputs.items():
        if isinstance(val, (dict, list)):
            val = json.dumps(val, separators=(",", ":"))
        val = str(val)
        if "\n" in val:
            lines.append(f"{key}<<__EOF__\n{val}\n__EOF__")
        else:
            lines.append(f"{key}={val}")
        print(f"  {key} = {val}")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")


def normalize_envs(cfg):
    envs = []
    default_flag = cfg.get("require_pr_approval")
    for i, e in enumerate(cfg.get("environments", []) or []):
        gate = e.get("gate")
        if gate:
            gate = str(gate).strip().lower()
        elif i == 0:
            # Entry env: fall back to the global require_pr_approval flag.
            approval = True if default_flag is None else bool(default_flag)
            gate = "approval" if approval else "auto"
        else:
            # Downstream envs default to on-demand (manual Promote workflow).
            gate = "on_demand"
        if gate not in ("auto", "approval", "on_demand"):
            gate = "on_demand"
        envs.append(
            {
                "name": e["name"],
                "branch": e["branch"],
                "gh_environment": e.get("gh_environment", e["name"]),
                "test_level": e.get("test_level", "RunSpecifiedTests"),
                "gate": gate,
                "jira_status": e.get("jira_status", ""),
            }
        )
    return envs


def code_values(cfg, code_cfg):
    """source_path / api_version / node_version: from the change when given."""
    src = cfg if code_cfg is None else code_cfg
    defaults = {"source_path": "force-app", "api_version": "66.0", "node_version": "22"}
    vals = {}
    for key in CODE_KEYS:
        val = str(src.get(key, defaults[key])).strip()
        if not SAFE_VALUE[key].match(val):
            print(f"::error::pipeline.config.yml: `{key}: {val}` is not allowed. Use a plain "
                  f"relative path / version (letters, digits and . _ / - only).", file=sys.stderr)
            sys.exit(1)
        vals[key] = val
    return vals


def cmd_outputs(cfg, code_cfg=None):
    envs = normalize_envs(cfg)
    if not envs:
        print("::error::pipeline.config.yml has no `environments` defined", file=sys.stderr)
        sys.exit(1)
    q = cfg.get("quality_gates", {}) or {}
    prod = cfg.get("production", {}) or {}
    notif = cfg.get("notifications", {}) or {}
    code = code_values(cfg, code_cfg)
    destructive_tests = str(prod.get("destructive_tests") or "")
    if not APEX_TEST_LIST.match(destructive_tests):
        print(f"::error::pipeline.config.yml: `production.destructive_tests: {destructive_tests}` is not "
              f"allowed. List Apex test class names only, separated by commas or spaces.", file=sys.stderr)
        sys.exit(1)

    def flag(v, default=True):
        return "true" if (default if v is None else bool(v)) else "false"

    emit(
        {
            "source_path": code["source_path"],
            "api_version": code["api_version"],
            "node_version": code["node_version"],
            "baseline_branch": cfg.get("baseline_branch", "main"),
            "delta_baseline": str(cfg.get("delta_baseline", "prod")).strip().lower(),
            "entry_branch": envs[0]["branch"],
            "auto_json": envs,
            "env_branches": [e["branch"] for e in envs],
            "env_count": len(envs),
            "last_env": envs[-1]["name"],
            # Label the release train gathers on. Derived from the LAST env in the
            # chain so renaming environments needs no change outside the config.
            # Override with `production.release_label` only if you stamp manually.
            "release_label": prod.get("release_label") or f"deployed:{envs[-1]['name']}",
            "prod_branch": prod.get("branch", ""),
            "prod_deploy_env": prod.get("deploy_environment", ""),
            "prod_validate_env": prod.get("validate_environment", ""),
            "prod_test_level": prod.get("test_level", "RunSpecifiedTests"),
            "prod_destructive_tests": destructive_tests.strip(),
            "release_prefix": prod.get("release_branch_prefix", "release/"),
            "q_apex_coverage": flag(q.get("apex_coverage")),
            "coverage_threshold": str(cfg.get("coverage_threshold", 75)),
            "profiles_mode": str(cfg.get("profiles_mode", "full")).strip().lower(),
            "bypass_label": cfg.get("bypass_label", "bypass-quality-gates"),
            "require_pr_approval": flag(cfg.get("require_pr_approval"), default=True),
            # May a PR's own author approve its entry deploy with the `approved`
            # label? Off by default; a solo operator turns it on.
            "allow_self_approval": flag(cfg.get("allow_self_approval"), default=False),
            "chat": flag(notif.get("chat"), default=False),
            "jira": flag(notif.get("jira"), default=False),
        }
    )


def cmd_resolve(cfg, branch):
    branch = branch.replace("refs/heads/", "")
    envs = normalize_envs(cfg)
    prod = cfg.get("production", {}) or {}
    release_prefix = prod.get("release_branch_prefix", "release/")

    match = next((e for e in envs if e["branch"] == branch), None)
    if match:
        # A branch is a "PR-driven stage" only when its gate expects a PR
        # (auto or approval). on_demand envs (qa/stage) are deployed via the
        # manual Promote workflow, so PRs into them must no-op.
        pr_driven = match["gate"] in ("auto", "approval")
        emit(
            {
                "r_name": match["name"],
                "r_gh_environment": match["gh_environment"],
                "r_test_level": match["test_level"],
                "r_gate": match["gate"],
                "r_is_stage": "true" if pr_driven else "false",
                "r_jira_status": match.get("jira_status", ""),
            }
        )
        return

    # Release branches validate against the (ungated) prod-validate environment.
    if release_prefix and branch.startswith(release_prefix):
        emit(
            {
                "r_name": "prod-validate",
                "r_gh_environment": prod.get("validate_environment", "prod-validate"),
                "r_test_level": prod.get("test_level", "RunSpecifiedTests"),
                "r_gate": "approval",
                "r_is_stage": "true",
            }
        )
        return

    # Unmapped branch — not a PR-driven stage. Do NOT fail; the caller's guard
    # reads r_is_stage=false and skips every downstream job.
    emit(
        {
            "r_name": "",
            "r_gh_environment": "",
            "r_test_level": "",
            "r_gate": "none",
            "r_is_stage": "false",
        }
    )


def cmd_promote(cfg, target):
    """Resolve a MANUAL (on-demand) promotion target (qa, stage, ...).

    Emits the environment to deploy to plus the branch it is promoted FROM
    (the previous environment in the chain). Only `gate: on_demand`
    environments are promotable this way — PR-driven stages (gate auto/approval,
    e.g. dev/int) are deployed from their PRs, not promoted manually.
    """
    target = target.strip()
    envs = normalize_envs(cfg)
    if not envs:
        print("::error::pipeline.config.yml has no `environments` defined", file=sys.stderr)
        sys.exit(1)

    idx = next((i for i, e in enumerate(envs) if e["name"] == target), None)
    if idx is None:
        names = ", ".join(e["name"] for e in envs)
        print(f"::error::'{target}' is not a configured environment. Valid: {names}", file=sys.stderr)
        sys.exit(1)
    if envs[idx]["gate"] != "on_demand":
        promotable = ", ".join(e["name"] for e in envs if e["gate"] == "on_demand") or "<none>"
        print(f"::error::'{target}' is a PR-driven stage (gate={envs[idx]['gate']}) — it is "
              f"deployed from its PR, not promoted manually. On-demand targets: {promotable}.",
              file=sys.stderr)
        sys.exit(1)

    env = envs[idx]
    prev = envs[idx - 1]
    source_branch = prev["branch"]  # promote FROM the previous env's branch
    # Ordering guard: if the immediately-preceding stage is itself on-demand
    # (e.g. stage's predecessor qa), require that it was actually deployed for
    # the commit being promoted before this one may run. PR-driven predecessors
    # (auto/approval, e.g. int) are always deployed via their PR, so no guard.
    require_env = prev["name"] if prev.get("gate") == "on_demand" else ""
    emit(
        {
            "r_name": env["name"],
            "r_gh_environment": env["gh_environment"],
            "r_branch": env["branch"],
            "r_source_branch": source_branch,
            "r_test_level": env["test_level"],
            "r_jira_status": env.get("jira_status", ""),
            "r_require_env": require_env,
        }
    )


def cmd_prdeploy(cfg, target):
    """Resolve a label-driven, per-branch PR deploy target (int, qa, stage, ...).

    Unlike `promote` (which advances trunk env branches from the previous env's
    branch), this deploys a single PR's head commit to one environment's org and
    gates it on the *immediately preceding* environment in the chain — regardless
    of gate mode. The first environment has no predecessor (require_env = "").
    r_require_envs lists EVERY earlier environment, in chain order, so a deploy
    can check that the change really reached each one (stage needs int AND qa).

    Emits: r_name, r_gh_environment, r_test_level, r_require_env, r_require_envs.
    """
    target = target.strip()
    envs = normalize_envs(cfg)
    if not envs:
        print("::error::pipeline.config.yml has no `environments` defined", file=sys.stderr)
        sys.exit(1)

    idx = next((i for i, e in enumerate(envs) if e["name"] == target), None)
    if idx is None:
        names = ", ".join(e["name"] for e in envs)
        print(f"::error::'{target}' is not a configured environment. Valid: {names}", file=sys.stderr)
        sys.exit(1)

    env = envs[idx]
    require_env = envs[idx - 1]["name"] if idx > 0 else ""
    nxt = envs[idx + 1] if idx + 1 < len(envs) else None
    emit(
        {
            "r_name": env["name"],
            "r_gh_environment": env["gh_environment"],
            "r_test_level": env["test_level"],
            "r_jira_status": env.get("jira_status", ""),
            "r_require_env": require_env,
            # Every earlier environment, comma-separated, in chain order.
            "r_require_envs": ",".join(e["name"] for e in envs[:idx]),
            # This env's own trunk branch (the base a PR merges into once deployed).
            "r_branch": env["branch"],
            # Whether this is the entry env (idx 0) — the only stage that requires
            # a human PR approval before the label deploy is allowed to run.
            "r_is_entry": "true" if idx == 0 else "false",
            # The next environment in the chain (for auto-creating the promotion
            # PR after a successful deploy). Empty on the final stage.
            "r_next_name": nxt["name"] if nxt else "",
            "r_next_branch": nxt["branch"] if nxt else "",
        }
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default=DEFAULT_CONFIG)
    p.add_argument("--code-config", default="",
                   help="Config of the change being shipped; supplies only " + ", ".join(CODE_KEYS))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("outputs")
    r = sub.add_parser("resolve")
    r.add_argument("branch")
    pr = sub.add_parser("promote")
    pr.add_argument("env")
    pd = sub.add_parser("prdeploy")
    pd.add_argument("env")
    args = p.parse_args()

    cfg = load_config(args.config)
    if args.cmd == "outputs":
        code_cfg = None
        if args.code_config and os.path.isfile(args.code_config):
            code_cfg = load_config(args.code_config)
        cmd_outputs(cfg, code_cfg)
    elif args.cmd == "resolve":
        cmd_resolve(cfg, args.branch)
    elif args.cmd == "promote":
        cmd_promote(cfg, args.env)
    elif args.cmd == "prdeploy":
        cmd_prdeploy(cfg, args.env)


if __name__ == "__main__":
    main()
