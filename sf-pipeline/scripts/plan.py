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
"""
import argparse
import json
import os
import sys

DEFAULT_CONFIG = os.path.join("sf-pipeline", "pipeline.config.yml")


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


def cmd_outputs(cfg):
    envs = normalize_envs(cfg)
    if not envs:
        print("::error::pipeline.config.yml has no `environments` defined", file=sys.stderr)
        sys.exit(1)
    q = cfg.get("quality_gates", {}) or {}
    prod = cfg.get("production", {}) or {}
    notif = cfg.get("notifications", {}) or {}

    def flag(v, default=True):
        return "true" if (default if v is None else bool(v)) else "false"

    emit(
        {
            "source_path": cfg.get("source_path", "force-app"),
            "api_version": cfg.get("api_version", "66.0"),
            "node_version": cfg.get("node_version", "22"),
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
            "prod_destructive_tests": prod.get("destructive_tests", ""),
            "release_prefix": prod.get("release_branch_prefix", "release/"),
            "q_apex_coverage": flag(q.get("apex_coverage")),
            "coverage_threshold": str(cfg.get("coverage_threshold", 75)),
            "profiles_mode": str(cfg.get("profiles_mode", "full")).strip().lower(),
            "bypass_label": cfg.get("bypass_label", "bypass-quality-gates"),
            "require_pr_approval": flag(cfg.get("require_pr_approval"), default=True),
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
        cmd_outputs(cfg)
    elif args.cmd == "resolve":
        cmd_resolve(cfg, args.branch)
    elif args.cmd == "promote":
        cmd_promote(cfg, args.env)
    elif args.cmd == "prdeploy":
        cmd_prdeploy(cfg, args.env)


if __name__ == "__main__":
    main()
