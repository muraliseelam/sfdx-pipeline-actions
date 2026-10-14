#!/usr/bin/env python3
"""GitHub API helpers shared by the workflows (thin wrapper over the `gh` CLI).

Commands
  upsert-comment    --pr N --marker M --body-file F [--only-update]
                    Create or update the PR comment that contains MARKER.
  set-status        --sha S --context C --state pending|success|failure|error
                    --description D [--target-url U]
                    Post the commit status that branch protection requires.
  pr-for-commit     --sha S
                    Print the number of the PR that introduced commit S (or nothing).
  approval          --pr N --required K --label L [--post-status CONTEXT] [--require]
                    A PR is approved when it carries label L or has >= K approving
                    reviews from people other than the author. Prints the verdict,
                    optionally posts it as a commit status on the PR head, and with
                    --require exits 1 when not approved (the deploy approval guard).
  remove-label      --pr N --label L
                    Remove a label from a PR (no error if it isn't there).
  bypass            --pr N --label L [--github-output]
                    Is the admin bypass label on the PR AND was it added by someone with
                    admin or maintain permission? A label added by anyone else is ignored.
  pr-marker         --pr N --name NAME [--set KEY=VALUE ...]
                    Read (prints JSON) or merge-update a JSON object stored in a
                    hidden <!-- NAME {...} --> block in the PR body. The release
                    workflow keeps the validated job ID and SHA there.

Repository comes from --repo or $GITHUB_REPOSITORY; auth from $GH_TOKEN.
Only the standard library is used, so this runs on the runner's system python3.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote


class GhError(Exception):
    """A gh api call failed."""


def parse_json_stream(text: str) -> Any:
    """Parse gh --paginate output (one JSON document per page) into one value."""
    text = text.strip()
    if not text:
        return None
    decoder = json.JSONDecoder()
    docs, idx = [], 0
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        obj, idx = decoder.raw_decode(text, idx)
        docs.append(obj)
    if len(docs) == 1:
        return docs[0]
    if all(isinstance(d, list) for d in docs):
        return [item for page in docs for item in page]
    return docs


def gh_api(path: str, *, method: str = "GET", body: dict | None = None, paginate: bool = False) -> Any:
    cmd = ["gh", "api", "-H", "Accept: application/vnd.github+json", "-X", method, path]
    if paginate:
        cmd.append("--paginate")
    if body is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(
        cmd,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode != 0:
        raise GhError(f"gh api {method} {path}: {(proc.stderr or proc.stdout).strip()}")
    return parse_json_stream(proc.stdout)


# ------------------------------------------------------------------------ commands


def upsert_comment(repo: str, pr: int, marker: str, body: str, only_update: bool = False) -> str:
    if marker not in body:
        body = f"{marker}\n{body}"
    comments = gh_api(f"repos/{repo}/issues/{pr}/comments?per_page=100", paginate=True) or []
    existing = [c for c in comments if marker in (c.get("body") or "")]
    if existing:
        gh_api(f"repos/{repo}/issues/comments/{existing[-1]['id']}", method="PATCH", body={"body": body})
        return "updated"
    if only_update:
        return "skipped"
    gh_api(f"repos/{repo}/issues/{pr}/comments", method="POST", body={"body": body})
    return "created"


def set_status(repo: str, sha: str, context: str, state: str, description: str, target_url: str = "") -> None:
    payload = {"state": state, "context": context, "description": description[:140]}
    if target_url:
        payload["target_url"] = target_url
    gh_api(f"repos/{repo}/statuses/{sha}", method="POST", body=payload)


_PR_REF = re.compile(r"\(#(\d+)\)\s*$")


def pr_for_commit(repo: str, sha: str, attempts: int = 6, delay: float = 5.0) -> int | None:
    """PR that introduced a commit. GitHub can take a few seconds after a merge to
    link the commit, so retry, then fall back to the "(#N)" of a squash subject."""
    for attempt in range(attempts):
        prs = gh_api(f"repos/{repo}/commits/{sha}/pulls?per_page=100", paginate=True) or []
        for pr in prs:
            if pr.get("merge_commit_sha") == sha:
                return int(pr["number"])
        merged = [pr for pr in prs if pr.get("merged_at")]
        if merged:
            return int(merged[0]["number"])
        if attempt + 1 < attempts:
            time.sleep(delay)
    subject = ((gh_api(f"repos/{repo}/commits/{sha}").get("commit") or {}).get("message") or "").splitlines()
    match = _PR_REF.search(subject[0]) if subject else None
    return int(match.group(1)) if match else None


def latest_approvers(reviews: list[dict], author: str) -> list[str]:
    """Reviewers whose most recent decisive review is APPROVED (author excluded)."""
    latest: dict[str, str] = {}
    for review in reviews:  # API returns reviews in chronological order
        login = (review.get("user") or {}).get("login")
        state = review.get("state")
        if not login or login == author or state in ("COMMENTED", "PENDING"):
            continue
        latest[login] = state
    return sorted(login for login, state in latest.items() if state == "APPROVED")


def approval_verdict(labels: list[str], approvers: list[str], required: int, label: str) -> tuple[bool, str]:
    """Pure decision (unit-tested): approved by label or by enough reviews."""
    if label and label in labels:
        return True, f"Approved via the '{label}' label"
    if required <= 0:
        return True, "No approval required for this environment"
    if len(approvers) >= required:
        return True, f"Approved by {', '.join(approvers)}"
    have = f"{len(approvers)}/{required} approving review(s)"
    return False, f"Awaiting approval: add the '{label}' label or get {required} approving review(s) ({have})"


def approval(repo: str, pr: int, required: int, label: str) -> tuple[bool, str, dict]:
    info = gh_api(f"repos/{repo}/pulls/{pr}")
    author = (info.get("user") or {}).get("login", "")
    labels = [lbl.get("name", "") for lbl in info.get("labels") or []]
    reviews = gh_api(f"repos/{repo}/pulls/{pr}/reviews?per_page=100", paginate=True) or []
    ok, why = approval_verdict(labels, latest_approvers(reviews, author), required, label)
    return ok, why, info


def labeled_by(timeline: list[dict], label: str) -> str | None:
    """Login of whoever most recently added `label` (pure; unit-tested)."""
    who = None
    for event in timeline:
        if event.get("event") == "labeled" and (event.get("label") or {}).get("name") == label:
            who = (event.get("actor") or {}).get("login") or who
    return who


def bypass(repo: str, pr: int, label: str) -> tuple[bool, str]:
    labels = [lbl.get("name") for lbl in gh_api(f"repos/{repo}/issues/{pr}/labels") or []]
    if label not in labels:
        return False, ""
    who = labeled_by(gh_api(f"repos/{repo}/issues/{pr}/timeline?per_page=100", paginate=True) or [], label)
    if not who:
        return False, f"'{label}' is present but who added it couldn't be determined; ignoring it"
    perm = (gh_api(f"repos/{repo}/collaborators/{who}/permission") or {}).get("permission")
    if perm in ("admin", "maintain"):
        return True, f"quality gates bypassed by @{who} ('{label}')"
    return False, f"'{label}' was added by @{who} ({perm or 'no'} permission), not an admin/maintainer; ignoring it"


def _marker_re(name: str) -> "re.Pattern[str]":
    return re.compile(r"<!--\s*" + re.escape(name) + r"\s+(\{.*?\})\s*-->", re.S)


def read_marker(body: str, name: str) -> dict:
    match = _marker_re(name).search(body or "")
    if not match:
        return {}
    try:
        data = json.loads(match.group(1))
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def write_marker(body: str, name: str, data: dict) -> str:
    block = f"<!-- {name} {json.dumps(data, sort_keys=True)} -->"
    pattern = _marker_re(name)
    if pattern.search(body or ""):
        return pattern.sub(lambda _: block, body, count=1)
    return (body or "").rstrip() + "\n\n" + block + "\n"


def remove_label(repo: str, pr: int, label: str) -> bool:
    """True when the label was removed, False when it wasn't on the PR."""
    proc = subprocess.run(
        ["gh", "api", "-X", "DELETE", f"repos/{repo}/issues/{pr}/labels/{quote(label, safe='')}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if proc.returncode == 0:
        return True
    if "404" in (proc.stderr + proc.stdout) or "Label does not exist" in (proc.stderr + proc.stdout):
        return False
    raise GhError(f"remove label {label!r} from #{pr}: {(proc.stderr or proc.stdout).strip()}")


# ----------------------------------------------------------------------------- cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("upsert-comment")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--marker", required=True)
    p.add_argument("--body-file", required=True)
    p.add_argument("--only-update", action="store_true", help="update an existing comment but never create one")

    p = sub.add_parser("set-status")
    p.add_argument("--sha", required=True)
    p.add_argument("--context", required=True)
    p.add_argument("--state", required=True, choices=["pending", "success", "failure", "error"])
    p.add_argument("--description", required=True)
    p.add_argument("--target-url", default="")

    p = sub.add_parser("pr-for-commit")
    p.add_argument("--sha", required=True)

    p = sub.add_parser("approval")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--required", type=int, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--post-status", metavar="CONTEXT", help="post the verdict as this commit status on the PR head")
    p.add_argument("--target-url", default="")
    p.add_argument("--require", action="store_true", help="exit 1 when not approved")
    p.add_argument("--github-output", action="store_true", help="write approved=true|false to $GITHUB_OUTPUT")

    p = sub.add_parser("remove-label")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--label", required=True)

    p = sub.add_parser("bypass")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--github-output", action="store_true")

    p = sub.add_parser("pr-marker")
    p.add_argument("--pr", type=int, required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")

    args = parser.parse_args(argv)
    if not args.repo:
        print("::error::--repo or GITHUB_REPOSITORY is required", file=sys.stderr)
        return 2
    try:
        if args.command == "upsert-comment":
            body = Path(args.body_file).read_text(encoding="utf-8")
            print(f"PR #{args.pr} comment {upsert_comment(args.repo, args.pr, args.marker, body, args.only_update)}")
        elif args.command == "set-status":
            set_status(args.repo, args.sha, args.context, args.state, args.description, args.target_url)
            print(f"status {args.context}={args.state} on {args.sha[:7]}")
        elif args.command == "pr-for-commit":
            number = pr_for_commit(args.repo, args.sha)
            print(number if number else "")
        elif args.command == "approval":
            ok, why, info = approval(args.repo, args.pr, args.required, args.label)
            print(f"PR #{args.pr}: {'approved' if ok else 'not approved'} - {why}")
            if args.post_status:
                set_status(
                    args.repo,
                    info["head"]["sha"],
                    args.post_status,
                    "success" if ok else "pending",
                    why,
                    args.target_url,
                )
            if args.github_output and os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
                    fh.write(f"approved={'true' if ok else 'false'}\nreason={why}\n")
            if args.require and not ok:
                print(f"::error title=Approval guard::PR #{args.pr}: {why}")
                return 1
        elif args.command == "remove-label":
            removed = remove_label(args.repo, args.pr, args.label)
            print(f"PR #{args.pr}: label {args.label!r} {'removed' if removed else 'was not present'}")
        elif args.command == "bypass":
            active, why = bypass(args.repo, args.pr, args.label)
            if why and not active:
                print(f"::warning title=Bypass ignored::{why}")
            print(f"PR #{args.pr}: bypass {'ACTIVE' if active else 'inactive'}{' - ' + why if why else ''}")
            if args.github_output and os.environ.get("GITHUB_OUTPUT"):
                with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
                    fh.write(f"active={'true' if active else 'false'}\nwhy={why}\n")
        elif args.command == "pr-marker":
            info = gh_api(f"repos/{args.repo}/pulls/{args.pr}")
            data = read_marker(info.get("body") or "", args.name)
            if args.set:
                for item in args.set:
                    key, _, value = item.partition("=")
                    data[key] = value
                body = write_marker(info.get("body") or "", args.name, data)
                gh_api(f"repos/{args.repo}/pulls/{args.pr}", method="PATCH", body={"body": body})
            print(json.dumps(data, sort_keys=True))
    except GhError as exc:
        # stderr: callers capture stdout with $(...), which would swallow the error.
        print(f"::error title=GitHub API::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
