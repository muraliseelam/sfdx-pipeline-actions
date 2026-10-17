#!/usr/bin/env python3
"""PR preflight guardrails. They run before anything touches a Salesforce org.

Commands
  meta-pairs [--source-path P]
      Every Apex class/trigger and Visualforce page/component has its -meta.xml
      companion, and every such -meta.xml has its source file.
  branch-origin [--head REF] [--remote origin] [--target-branch BR]
      The head must be cut from the baseline (main): it may not carry commits
      that exist only on another environment branch (e.g. a feature cut from
      int). Commits already in the PR's target branch are allowed.
  pr-route --base-env ENV --head-ref REF
      Feature PRs enter at promotion.chain[0]; later environment branches only
      accept promotion PRs (promote/<env>/...); production only accepts
      release branches; environment branches are never merged into each other.

Each command prints ::error:: annotations and exits 1 on any violation. With
PREFLIGHT_REPORT set, each problem is also appended to that file as a Markdown
bullet, so the PR comment can say what failed.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_config  # noqa: E402

PAIRED_EXTENSIONS = (".cls", ".trigger", ".page", ".component")


# ---------------------------------------------------------------------- meta pairs


def meta_pair_problems(source_path: str | Path) -> list[tuple[str, str]]:
    root = Path(source_path)
    problems: list[tuple[str, str]] = []
    for ext in PAIRED_EXTENSIONS:
        for src in root.rglob(f"*{ext}"):
            if src.is_file() and not src.with_name(src.name + "-meta.xml").is_file():
                problems.append((src.as_posix(), f"{src.name} has no {src.name}-meta.xml companion"))
        for meta in root.rglob(f"*{ext}-meta.xml"):
            src = meta.with_name(meta.name[: -len("-meta.xml")])
            if not src.is_file():
                problems.append((meta.as_posix(), f"{meta.name} has no {src.name} source file"))
    return sorted(problems)


# ------------------------------------------------------------------- branch origin


def _git(*args: str) -> str:
    proc = subprocess.run(["git", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {proc.stderr.strip()}")
    return proc.stdout


def _ref_exists(ref: str) -> bool:
    return subprocess.run(["git", "rev-parse", "--verify", "--quiet", ref], capture_output=True).returncode == 0


def branch_origin_problems(
    cfg: dict, head: str = "HEAD", remote: str = "origin", target_branch: str | None = None
) -> list[str]:
    """Commits on head that exist only on an env branch other than the PR's target.

    Commits already in the target branch are ignored: merging the target into a
    promotion branch to resolve a conflict adds nothing new to the target.
    """
    baseline = f"{remote}/{cfg['project']['baseline_branch']}"
    if not _ref_exists(baseline):
        raise RuntimeError(f"{baseline} not found; check out with fetch-depth: 0")
    exclude = [f"^{baseline}"]
    if target_branch and _ref_exists(f"{remote}/{target_branch}"):
        exclude.append(f"^{remote}/{target_branch}")
    head_commits = set(_git("rev-list", head, *exclude).split())
    if not head_commits:
        return []
    problems = []
    for env in cfg["environments"].values():
        if env.get("production") or env["branch"] == target_branch:
            continue
        ref = f"{remote}/{env['branch']}"
        if not _ref_exists(ref):
            continue
        leaked = head_commits & set(_git("rev-list", f"{baseline}..{ref}").split())
        if leaked:
            sample = _git("log", "--no-walk=unsorted", "--format=%h %s", *sorted(leaked)[:3]).strip().splitlines()
            problems.append(
                f"the branch carries {len(leaked)} commit(s) that exist only on '{env['branch']}' "
                f"(e.g. {'; '.join(sample)}). Feature branches must be cut from "
                f"'{cfg['project']['baseline_branch']}'; recreate the branch from it and cherry-pick your commits."
            )
    return problems


# ------------------------------------------------------------------------ pr route


def pr_route_problems(cfg: dict, base_env: str, head_ref: str) -> list[str]:
    envs = cfg["environments"]
    if base_env not in envs:
        return [f"unknown environment {base_env!r}"]
    chain = cfg["promotion"]["chain"]
    entry = chain[0]
    base_branch = envs[base_env]["branch"]
    promote_prefix = cfg["promotion"]["branch_prefix"]
    release_prefix = cfg["release"]["branch_prefix"]
    env_branches = {e["branch"]: name for name, e in envs.items()}

    if head_ref in env_branches:
        return [
            f"'{head_ref}' is an environment branch. Environment branches are never merged into each other; "
            "promote individual changes with the promote:<env> label instead."
        ]
    if envs[base_env].get("production"):
        if not head_ref.startswith(release_prefix):
            return [
                f"'{base_branch}' (production) only accepts {release_prefix}* PRs opened by the SF Prod Release workflow."
            ]
        return []
    if base_env == entry or not cfg["promotion"].get("enforce_order", True):
        return []
    if not head_ref.startswith(f"{promote_prefix}{base_env}/"):
        previous = chain[chain.index(base_env) - 1]
        label = cfg["github"]["labels"]["promote_prefix"] + base_env
        return [
            f"feature PRs must target '{envs[entry]['branch']}'. '{base_branch}' only accepts promotion PRs "
            f"({promote_prefix}{base_env}/...): add the label '{label}' to the merged PR in '{envs[previous]['branch']}'."
        ]
    return []


# ----------------------------------------------------------------------------- cli


def report(title: str, msg: str, file: str | None = None) -> None:
    where = f"file={file}," if file else ""
    print(f"::error {where}title={title}::{msg}")
    target = os.environ.get("PREFLIGHT_REPORT")
    if target:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"- **{title}**: {msg}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="path to pipeline.config.yml")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("meta-pairs")
    p.add_argument("--source-path")
    p = sub.add_parser("branch-origin")
    p.add_argument("--head", default="HEAD")
    p.add_argument("--remote", default="origin")
    p.add_argument("--target-branch", help="the PR's base branch (its commits are allowed on head)")
    p = sub.add_parser("pr-route")
    p.add_argument("--base-env", required=True)
    p.add_argument("--head-ref", required=True)
    args = parser.parse_args(argv)

    try:
        cfg = pipeline_config.load(args.config)
    except pipeline_config.ConfigError as exc:
        print(f"::error title=pipeline.config.yml::{exc}")
        return 2

    if args.command == "meta-pairs":
        source = args.source_path or cfg["project"]["source_path"]
        problems = meta_pair_problems(source)
        for path, msg in problems:
            report("Meta-pair check", msg, file=path)
        print(f"meta-pairs: {len(problems)} problem(s) under {source}")
        return 1 if problems else 0

    if args.command == "branch-origin":
        try:
            problems = branch_origin_problems(cfg, args.head, args.remote, args.target_branch)
        except RuntimeError as exc:
            report("Branch origin check", str(exc))
            return 1
        for msg in problems:
            report("Branch origin check", msg)
        print(f"branch-origin: {len(problems)} problem(s)")
        return 1 if problems else 0

    if args.command == "pr-route":
        problems = pr_route_problems(cfg, args.base_env, args.head_ref)
        for msg in problems:
            report("PR routing", msg)
        print(f"pr-route: {len(problems)} problem(s) for {args.head_ref} -> {args.base_env}")
        return 1 if problems else 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
