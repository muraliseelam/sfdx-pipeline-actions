#!/usr/bin/env python3
"""
test_branch_protection.py — prove setup-branch-protection.sh leaves no pipeline
branch open, and that the pipeline can still merge into every one of them.

Drives the script with DRY_RUN=true (it prints each branch's PUT payload and
calls nothing) across several configs, and asserts:

  * every environment branch AND the production branch is pull-request-only —
    a direct push to qa, stage or main would bypass every deploy and guard;
  * the required statuses are ones the pipeline really writes (`pr-validation`,
    `deploy-<env name>`), keyed by environment NAME, not branch;
  * branches the pipeline merges unattended need no human review;
  * the post-release realign (sf-pipeline/sync-branches) restores exactly what
    the script set: its snapshot -> restore transform round-trips every payload.

Run:  python3 tests/test_branch_protection.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "sf-pipeline" / "scripts" / "setup-branch-protection.sh"
SYNC_ACTION = ROOT / "sf-pipeline" / "sync-branches" / "action.yml"

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


def protect(cfg_text, **env):
    """Dry-run the script in a scratch consumer repo; return (rc, {branch: payload})."""
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "sf-pipeline"))
        with open(os.path.join(tmp, "sf-pipeline", "pipeline.config.yml"), "w", encoding="utf-8") as fh:
            fh.write(cfg_text)
        run_env = dict(os.environ, REPO="owner/repo", DRY_RUN="true", **env)
        for k in ("REQUIRED_REVIEWS", "ENFORCE_ADMINS", "REQUIRE_CODE_OWNER_REVIEWS"):
            if k not in env:
                run_env.pop(k, None)
        proc = subprocess.run(["bash", SCRIPT.as_posix()], cwd=tmp, env=run_env,
                              capture_output=True, text=True)
    payloads = {}
    for line in proc.stdout.splitlines():
        m = re.match(r"^\s*-> (.+?) \(dry run\): (\{.*\})$", line)
        if m:
            payloads[m.group(1)] = json.loads(m.group(2))
    return proc.returncode, payloads


def contexts(p):
    rsc = p.get("required_status_checks")
    if not rsc:
        return []
    return [c["context"] for c in rsc["checks"]] if "checks" in rsc else rsc["contexts"]


def app_ids(p):
    rsc = p.get("required_status_checks") or {}
    return {c.get("app_id") for c in rsc.get("checks", [])}


def reviews(p):
    return (p.get("required_pull_request_reviews") or {}).get("required_approving_review_count")


def code_owners(p):
    return (p.get("required_pull_request_reviews") or {}).get("require_code_owner_reviews")


REFERENCE = """
baseline_branch: main
environments:
  - { name: int,   branch: int,   gh_environment: int,   gate: approval }
  - { name: qa,    branch: qa,    gh_environment: qa,    gate: on_demand }
  - { name: stage, branch: stage, gh_environment: stage, gate: on_demand }
production:
  branch: main
  deploy_environment: prod
  validate_environment: prod-validate
"""

# Names differ from branches, five stages, an auto entry and a non-main prod.
WEEKDAYS = """
baseline_branch: live
environments:
  - { name: mon, branch: env/monday,    gate: auto }
  - { name: tue, branch: env/tuesday,   gate: approval }
  - { name: wed, branch: env/wednesday }
  - { name: thu, branch: env/thursday }
  - { name: fri, branch: env/friday }
production:
  branch: live
  deploy_environment: live
  validate_environment: live-check
"""

NO_PROD = """
environments:
  - { name: dev, branch: develop, gate: approval }
  - { name: uat, branch: uat }
"""


def test_reference_chain():
    print("\nReference chain int -> qa -> stage -> main (defaults)")
    rc, p = protect(REFERENCE)
    check("script exits 0", rc, 0)
    check("protects every env branch and production", sorted(p), ["int", "main", "qa", "stage"])
    for b, body in sorted(p.items()):
        check(f"{b}: pull-request-only (no direct push)", body.get("required_pull_request_reviews") is not None, True)
        check(f"{b}: no force-push, no deletion", (body["allow_force_pushes"], body["allow_deletions"]), (False, False))
        check(f"{b}: enforce_admins defaults to false", body["enforce_admins"], False)
    check("int: requires pr-validation and deploy-int", contexts(p["int"]), ["pr-validation", "deploy-int"])
    check("int: one approval, code owners, stale dismissed",
          (reviews(p["int"]), code_owners(p["int"]), p["int"]["required_pull_request_reviews"]["dismiss_stale_reviews"]),
          (1, True, True))
    check("int: conversation resolution", p["int"]["required_conversation_resolution"], True)
    for b in ("qa", "stage"):
        check(f"{b}: requires deploy-{b} only", contexts(p[b]), [f"deploy-{b}"])
        check(f"{b}: no human review (the pipeline merges it)", (reviews(p[b]), code_owners(p[b])), (0, False))
    check("main: no required status (the release train writes none)", contexts(p["main"]), [])
    check("main: entry review settings", (reviews(p["main"]), code_owners(p["main"])), (1, True))


def test_solo():
    print("\nSolo maintainer — REQUIRED_REVIEWS=0")
    _, p = protect(REFERENCE, REQUIRED_REVIEWS="0")
    for b in ("int", "main"):
        check(f"{b}: 0 approvals", reviews(p[b]), 0)
        check(f"{b}: code-owner review follows REQUIRED_REVIEWS (off)", code_owners(p[b]), False)
        check(f"{b}: still pull-request-only", p[b].get("required_pull_request_reviews") is not None, True)
    _, p = protect(REFERENCE, REQUIRED_REVIEWS="0", REQUIRE_CODE_OWNER_REVIEWS="true")
    check("explicit REQUIRE_CODE_OWNER_REVIEWS=true wins", (code_owners(p["int"]), code_owners(p["main"])), (True, True))
    check("…but never on pipeline-merged stages", code_owners(p["qa"]), False)


def test_names_not_branches():
    print("\nStatus contexts use environment NAMES; branch names may contain '/'")
    rc, p = protect(WEEKDAYS)
    check("script exits 0", rc, 0)
    check("protects all five env branches and production",
          sorted(p), ["env/friday", "env/monday", "env/thursday", "env/tuesday", "env/wednesday", "live"])
    check("auto entry: deploy-mon only (pr-validation is written for approval gates)",
          contexts(p["env/monday"]), ["deploy-mon"])
    check("auto entry: 0 reviews, so the pipeline's unattended merge isn't refused",
          (reviews(p["env/monday"]), code_owners(p["env/monday"])), (0, False))
    check("approval-gated later stage: deploy-tue only", contexts(p["env/tuesday"]), ["deploy-tue"])
    check("env/thursday requires deploy-thu", contexts(p["env/thursday"]), ["deploy-thu"])
    check("production branch 'live' is pull-request-only", p["live"].get("required_pull_request_reviews") is not None, True)


def test_no_production_block():
    print("\nNo production block")
    rc, p = protect(NO_PROD)
    check("script exits 0", rc, 0)
    check("only the env branches are protected", sorted(p), ["develop", "uat"])


def test_options():
    print("\nOptions")
    _, p = protect(REFERENCE, ENFORCE_ADMINS="true")
    check("ENFORCE_ADMINS=true applies to every branch", {b["enforce_admins"] for b in p.values()}, {True})
    rc, p = protect(REFERENCE, REQUIRED_REVIEWS="7")
    check("REQUIRED_REVIEWS outside 0-6 is refused", (rc != 0, p), (True, {}))
    rc, p = protect(REFERENCE, ENFORCE_ADMINS="yes")
    check("a non-boolean flag is refused", (rc != 0, p), (True, {}))
    _, p = protect(REFERENCE)
    check("required statuses are pinned to the GitHub Actions app by default",
          set().union(*(app_ids(b) for b in p.values())), {15368})
    _, p = protect(REFERENCE, STATUS_APP_ID="")
    check("STATUS_APP_ID='' leaves them unpinned",
          set().union(*(app_ids(b) for b in p.values())), {None})
    rc, p = protect(REFERENCE, STATUS_APP_ID="abc")
    check("a non-numeric STATUS_APP_ID is refused", (rc != 0, p), (True, {}))


def as_get_payload(put):
    """Shape a PUT body the way GET /branches/{b}/protection returns it."""
    get = {}
    if put.get("required_status_checks"):
        rsc = put["required_status_checks"]
        checks = [{"context": c["context"], "app_id": c.get("app_id")} for c in rsc["checks"]]
        get["required_status_checks"] = {"strict": rsc["strict"], "contexts": [c["context"] for c in checks],
                                         "checks": checks}
    get["enforce_admins"] = {"enabled": put["enforce_admins"]}
    if put.get("required_pull_request_reviews"):
        get["required_pull_request_reviews"] = dict(put["required_pull_request_reviews"],
                                                    require_last_push_approval=False)
    for k in ("allow_force_pushes", "allow_deletions", "required_linear_history",
              "required_conversation_resolution"):
        get[k] = {"enabled": bool(put.get(k))}
    get["block_creations"] = {"enabled": False}
    get["lock_branch"] = {"enabled": False}
    return get


def kept(want, got):
    """True when every setting in `want` has the same value in `got`."""
    if isinstance(want, dict) and isinstance(got, dict):
        return all(k in got and kept(v, got[k]) for k, v in want.items())
    return want == got


def test_realign_round_trip():
    print("\nPost-release realign restores the protection unchanged")
    text = SYNC_ACTION.read_text(encoding="utf-8")
    m = re.search(r"read -r -d '' XFORM <<'PY' \|\| true\n(.*?)\n\s*PY\n", text, re.S)
    if not m:
        print("  (skipped — the snapshot transform is no longer an inline XFORM block)")
        return
    lines = m.group(1).split("\n")
    indent = min(len(l) - len(l.lstrip()) for l in lines if l.strip())
    xform = "\n".join(l[indent:] for l in lines)
    for cfg in (REFERENCE, WEEKDAYS):
        _, p = protect(cfg)
        for b, put in sorted(p.items()):
            proc = subprocess.run([sys.executable, "-c", xform], input=json.dumps(as_get_payload(put)),
                                  capture_output=True, text=True)
            restored = json.loads(proc.stdout) if proc.returncode == 0 else {"error": proc.stderr}
            # The restore may carry extra settings at their defaults
            # (e.g. require_last_push_approval=false); nothing set here may change.
            lost = {k: (v, restored.get(k)) for k, v in put.items() if not kept(v, restored.get(k))}
            check(f"{b}: snapshot -> restore keeps every setting", lost, {})


def main():
    print("Branch protection suite")
    print(f"script: {SCRIPT}")
    if not shutil.which("bash"):
        print("  (skipped — bash is not available)")
        return 0
    for fn in (
        test_reference_chain,
        test_solo,
        test_names_not_branches,
        test_no_production_block,
        test_options,
        test_realign_round_trip,
    ):
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
