#!/usr/bin/env python3
"""Snapshot, lift, restore and apply branch protection for environment branches.

sync-env-branches uses snapshot -> lift -> force-push -> restore (always) so a
protected branch can be realigned to the production baseline and end up with
exactly the protection it had before. `apply` sets the pipeline's standard
protection from pipeline.config.yml during first-time setup.

Commands
  snapshot --branch B --out FILE        Save current protection ({"protected": false} if none).
  lift     --branch B                   Remove protection.
  restore  --branch B --snapshot FILE   Re-apply a snapshot, then verify it matches.
  apply    (--env ENV | --all)          Apply standard protection from config:
                                          required checks deploy-<env> + sf-approval,
                                          enforce_admins, PRs required, linear history on
                                          non-production branches (squash merges), no force
                                          pushes or deletions.
  show     --branch B                   Print the protection as a PUT body.

Needs a token with repository administration rights in $GH_TOKEN (the PAT).
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pipeline_config  # noqa: E402
from gh_helpers import GhError, gh_api, parse_json_stream  # noqa: E402

FLAGS = (
    "required_linear_history",
    "allow_force_pushes",
    "allow_deletions",
    "block_creations",
    "required_conversation_resolution",
    "lock_branch",
    "allow_fork_syncing",
)


def _path(repo: str, branch: str, suffix: str = "") -> str:
    return f"repos/{repo}/branches/{quote(branch, safe='')}/protection{suffix}"


def get_protection(repo: str, branch: str) -> dict | None:
    """Current protection JSON, or None when the branch is not protected."""
    proc = subprocess.run(
        ["gh", "api", "-H", "Accept: application/vnd.github+json", _path(repo, branch)],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode == 0:
        return parse_json_stream(proc.stdout)
    text = (proc.stderr + proc.stdout).lower()
    if "404" in text and ("not protected" in text or "not found" in text):
        return None
    raise GhError(f"GET protection for {branch}: {(proc.stderr or proc.stdout).strip()}")


def _logins(block: dict | None) -> dict | None:
    if not block:
        return None
    out = {
        "users": sorted(u["login"] for u in block.get("users", [])),
        "teams": sorted(t["slug"] for t in block.get("teams", [])),
        "apps": sorted(a["slug"] for a in block.get("apps", [])),
    }
    return out if any(out.values()) else None


def to_put_body(p: dict) -> dict:
    """Convert a GET /protection response into the equivalent PUT body."""
    body: dict = {}

    rsc = p.get("required_status_checks")
    if rsc:
        checks = [
            {"context": c["context"], "app_id": c.get("app_id") if c.get("app_id") is not None else -1}
            for c in rsc.get("checks") or []
        ]
        if not checks:
            checks = [{"context": ctx, "app_id": -1} for ctx in rsc.get("contexts") or []]
        body["required_status_checks"] = {
            "strict": bool(rsc.get("strict")),
            "checks": sorted(checks, key=lambda c: (c["context"], c["app_id"])),
        }
    else:
        body["required_status_checks"] = None

    body["enforce_admins"] = bool((p.get("enforce_admins") or {}).get("enabled"))

    reviews = p.get("required_pull_request_reviews")
    if reviews:
        r = {
            "dismiss_stale_reviews": bool(reviews.get("dismiss_stale_reviews")),
            "require_code_owner_reviews": bool(reviews.get("require_code_owner_reviews")),
            "required_approving_review_count": int(reviews.get("required_approving_review_count") or 0),
            "require_last_push_approval": bool(reviews.get("require_last_push_approval")),
        }
        for key in ("dismissal_restrictions", "bypass_pull_request_allowances"):
            block = _logins(reviews.get(key))
            if block:
                r[key] = block
        body["required_pull_request_reviews"] = r
    else:
        body["required_pull_request_reviews"] = None

    body["restrictions"] = _logins(p.get("restrictions"))
    for flag in FLAGS:
        body[flag] = bool((p.get(flag) or {}).get("enabled"))
    return body


def signatures_enabled(p: dict) -> bool:
    return bool((p.get("required_signatures") or {}).get("enabled"))


def desired_protection(cfg: dict, env_name: str) -> dict:
    """Standard protection: deploy-<env> + sf-approval checks, PRs required, admins included.

    Approval is enforced through the sf-approval status (label OR reviews), so
    GitHub's own review count stays 0; otherwise the label alone couldn't approve.
    """
    env = cfg["environments"][env_name]
    app_id = int(cfg["github"].get("status_check_app_id", -1))
    production = bool(env.get("production"))
    contexts = [env["status_check"], cfg["approvals"]["status_check"]]
    return {
        "required_status_checks": {
            "strict": False,
            "checks": sorted(({"context": c, "app_id": app_id} for c in contexts), key=lambda c: (c["context"], c["app_id"])),
        },
        "enforce_admins": True,
        "required_pull_request_reviews": {
            "dismiss_stale_reviews": True,
            "require_code_owner_reviews": False,
            "required_approving_review_count": 0,
            "require_last_push_approval": False,
        },
        "restrictions": None,
        # Squash/rebase only into env branches; main takes the release merge commit.
        "required_linear_history": not production,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "block_creations": False,
        # Not on production: the release workflow merges the release PR itself.
        "required_conversation_resolution": not production,
        "lock_branch": False,
        "allow_fork_syncing": False,
    }


def put_protection(repo: str, branch: str, body: dict, signatures: bool) -> None:
    gh_api(_path(repo, branch), method="PUT", body=body)
    if signatures:
        gh_api(_path(repo, branch, "/required_signatures"), method="POST")


def verify(repo: str, branch: str, body: dict, signatures: bool) -> list[str]:
    current = get_protection(repo, branch)
    if current is None:
        return [f"{branch} is not protected after restore"]
    now = to_put_body(current)
    diffs = [f"{k}: expected {json.dumps(body.get(k))} got {json.dumps(now.get(k))}" for k in body if body.get(k) != now.get(k)]
    if signatures_enabled(current) != signatures:
        diffs.append(f"required_signatures: expected {signatures} got {signatures_enabled(current)}")
    return diffs


# ----------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--config", help="path to pipeline.config.yml")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("snapshot")
    p.add_argument("--branch", required=True)
    p.add_argument("--out", required=True)
    p = sub.add_parser("lift")
    p.add_argument("--branch", required=True)
    p = sub.add_parser("restore")
    p.add_argument("--branch", required=True)
    p.add_argument("--snapshot", required=True)
    p = sub.add_parser("apply")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--env")
    g.add_argument("--all", action="store_true")
    p = sub.add_parser("show")
    p.add_argument("--branch", required=True)
    args = parser.parse_args(argv)

    try:
        cfg = pipeline_config.load(args.config)
    except pipeline_config.ConfigError as exc:
        print(f"::error title=pipeline.config.yml::{exc}")
        return 2
    repo = args.repo or cfg["project"].get("repository", "")
    if not repo:
        # Run locally: the repo of the current clone, as bootstrap.py does.
        gh = shutil.which("gh") or "gh"
        proc = subprocess.run([gh, "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
                              capture_output=True, text=True, encoding="utf-8")
        repo = proc.stdout.strip() if proc.returncode == 0 else ""
    if not repo:
        print("::error::--repo, GITHUB_REPOSITORY or project.repository is required")
        return 2

    try:
        if args.command == "snapshot":
            current = get_protection(repo, args.branch)
            snap = {"branch": args.branch, "protected": current is not None, "protection": current}
            Path(args.out).write_text(json.dumps(snap, indent=2), encoding="utf-8")
            print(f"snapshot {args.branch}: {'protected' if current else 'not protected'} -> {args.out}")
            if current:
                print(json.dumps(to_put_body(current), indent=2))

        elif args.command == "lift":
            if get_protection(repo, args.branch) is None:
                print(f"{args.branch}: not protected; nothing to lift")
            else:
                gh_api(_path(repo, args.branch), method="DELETE")
                print(f"{args.branch}: protection lifted")

        elif args.command == "restore":
            snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            if not snap.get("protected"):
                print(f"{args.branch}: snapshot says unprotected; nothing to restore")
                return 0
            body = to_put_body(snap["protection"])
            sigs = signatures_enabled(snap["protection"])
            put_protection(repo, args.branch, body, sigs)
            diffs = verify(repo, args.branch, body, sigs)
            if diffs:
                for d in diffs:
                    print(f"::error title=Protection restore mismatch ({args.branch})::{d}")
                return 1
            print(f"{args.branch}: protection restored and verified")

        elif args.command == "apply":
            names = list(cfg["environments"]) if args.all else [args.env]
            for name in names:
                if name not in cfg["environments"]:
                    print(f"::error::unknown environment {name!r}")
                    return 2
                branch = cfg["environments"][name]["branch"]
                body = desired_protection(cfg, name)
                put_protection(repo, branch, body, signatures=False)
                diffs = verify(repo, branch, body, signatures=False)
                if diffs:
                    for d in diffs:
                        print(f"::warning title=Protection drift ({branch})::{d}")
                checks = ", ".join(c["context"] for c in body["required_status_checks"]["checks"])
                print(f"{branch}: protection applied (required checks: {checks})")

        elif args.command == "show":
            current = get_protection(repo, args.branch)
            print(json.dumps(to_put_body(current) if current else {"protected": False}, indent=2))
    except GhError as exc:
        print(f"::error title=GitHub API::{exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
