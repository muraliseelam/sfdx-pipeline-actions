#!/usr/bin/env python3
"""
test_release_train.py — run the release train's own shell steps offline.

Extracts the real `run:` blocks of prod-release.yml, sync-env-branches.yml and
the release-gather / release-preflight / sync-branches actions, and runs them
with bash against throwaway git repositories (a bare "origin" plus a clone)
and a fake `gh` that answers each API call from a table. Nothing reaches
GitHub or Salesforce. Asserts:

  * assemble ships only what the gathered stories deployed — not a story on
    hold, nor another story pulled in by merging an env branch (BUG-002);
    a stale release branch is refused; re-assembly takes in the prod branch
  * the production delta is three-dot, preflight refuses a release behind the
    prod branch, and the deploy job refuses one the prod branch moved past
    while it waited for approval (BUG-003)
  * preflight's authorization rules; release-gather's proof rules
  * finalize is re-runnable and never tags a release it could not merge
  * the post-release realign merges, resets or leaves each env branch, and
    restores (and reports) protection per branch (BUG-008)
  * Sync Env Branches falls back to a PR when a push is refused
  * no `run:` block pastes an expression; a crafted dispatch version is refused

Run:  python3 tests/test_release_train.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PROD_RELEASE = ROOT / ".github" / "workflows" / "prod-release.yml"
SYNC_ENV = ROOT / ".github" / "workflows" / "sync-env-branches.yml"
GATHER = ROOT / "sf-pipeline" / "release-gather" / "action.yml"
PREFLIGHT = ROOT / "sf-pipeline" / "release-preflight" / "action.yml"
SYNC_ACTION = ROOT / "sf-pipeline" / "sync-branches" / "action.yml"
REPO = "o/r"

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


# ---- extracting the steps ----------------------------------------------------
def workflow_step(path, job, name):
    for s in yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"][job]["steps"]:
        if (s.get("name") or "").startswith(name) or s.get("id") == name:
            return s
    raise KeyError(f"{path.name}: no step {name!r} in job {job!r}")


def action_step(path, name):
    for s in yaml.safe_load(path.read_text(encoding="utf-8"))["runs"]["steps"]:
        if (s.get("name") or "").startswith(name) or s.get("id") == name:
            return s
    raise KeyError(f"{path.name}: no step {name!r}")


# ---- git + fake gh -------------------------------------------------------------
FAKE_GH = r'''
import json, os, re, sys
args = sys.argv[1:]
line = " ".join(args)
with open(os.environ["FAKE_GH_LOG"], "a", encoding="utf-8") as fh:
    fh.write(line + "\n")
    if "--input" in args:
        fh.write("  input: " + open(args[args.index("--input") + 1], encoding="utf-8").read().strip() + "\n")
for r in json.load(open(os.environ["FAKE_GH_RULES"], encoding="utf-8")):
    if re.search(r["match"], line):
        out = r.get("out", "")
        sys.stdout.buffer.write((out + ("\n" if out and not out.endswith("\n") else "")).encode("utf-8"))
        sys.exit(r.get("rc", 0))
sys.stderr.write("fake gh: no rule for: " + line + "\n")
sys.exit(1)
'''

GIT_ENV = {
    "GIT_AUTHOR_NAME": "Dev", "GIT_AUTHOR_EMAIL": "dev@example.com",
    "GIT_COMMITTER_NAME": "Dev", "GIT_COMMITTER_EMAIL": "dev@example.com",
    "GIT_CONFIG_NOSYSTEM": "1",
}


class Sandbox:
    """A bare origin, a dev clone to build history in, and a fake gh on PATH."""

    def __init__(self, tmp):
        self.tmp = Path(tmp)
        self.origin = self.tmp / "origin.git"
        self.dev = self.tmp / "dev"
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        (self.bin / "fake_gh.py").write_text(FAKE_GH, encoding="utf-8")
        gh = self.bin / "gh"
        gh.write_text(f'#!/usr/bin/env bash\nexec "{Path(sys.executable).as_posix()}" '
                      f'"{(self.bin / "fake_gh.py").as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
        gh.chmod(0o755)
        if os.name == "nt":
            # The steps parse python3's output line by line; on Windows it writes
            # CRLF, which a Linux runner never sees.
            py = self.bin / "python3"
            py.write_text(f'#!/usr/bin/env bash\nset -o pipefail\n"{Path(sys.executable).as_posix()}" "$@" '
                          f"| tr -d '\\r'\n", encoding="utf-8", newline="\n")
            py.chmod(0o755)
        self.log = self.tmp / "gh.log"
        self.rules = self.tmp / "gh.json"
        self.set_gh([])
        self.git(self.tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        self.git(self.tmp, "clone", "-q", str(self.origin), str(self.dev))
        self.git(self.dev, "config", "core.autocrlf", "false")
        self.commit("force-app/f/base.txt", "base\n", "base")
        self.git(self.dev, "push", "-q", "origin", "HEAD:refs/heads/main")

    def git(self, cwd, *args):
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                           env=dict(os.environ, **GIT_ENV))
        if p.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)}: {p.stderr}")
        return p.stdout.strip()

    def commit(self, path, text, msg):
        f = self.dev / path
        if text is None:
            f.unlink()
        else:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8", newline="\n")
        self.git(self.dev, "add", "-A")
        self.git(self.dev, "commit", "-q", "-m", msg)
        return self.git(self.dev, "rev-parse", "HEAD")

    def branch(self, name, base="origin/main"):
        self.git(self.dev, "fetch", "-q", "origin")
        self.git(self.dev, "checkout", "-q", "-B", name, base)

    def push(self, ref, name=None):
        self.git(self.dev, "push", "-q", "-f", "origin", f"{ref}:refs/heads/{name or ref}")

    def ref(self, name):
        p = subprocess.run(["git", "rev-parse", "-q", "--verify", f"refs/heads/{name}"], cwd=self.origin,
                           capture_output=True, text=True)
        return p.stdout.strip() if p.returncode == 0 else ""

    def files(self, ref):
        return sorted(self.git(self.origin, "ls-tree", "-r", "--name-only", ref).splitlines())

    def runner(self, ref="main"):
        """A fresh checkout, the way actions/checkout leaves it (fetch-depth: 0)."""
        r = self.tmp / f"runner-{len(list(self.tmp.glob('runner-*')))}"
        self.git(self.tmp, "clone", "-q", "-b", ref, str(self.origin), str(r))
        self.git(r, "config", "core.autocrlf", "false")
        self.git(r, "config", "user.name", "sf-release-bot")
        self.git(r, "config", "user.email", "sf-release-bot@users.noreply.github.com")
        return r

    def set_gh(self, rules):
        self.rules.write_text(json.dumps(rules), encoding="utf-8")
        self.log.write_text("", encoding="utf-8")

    def gh_calls(self):
        return self.log.read_text(encoding="utf-8")

    def run(self, script, cwd, env):
        out = self.tmp / "out.txt"
        summ = self.tmp / "summary.md"
        out.write_text("", encoding="utf-8")
        summ.write_text("", encoding="utf-8")
        step = self.tmp / "step.sh"
        step.write_text(script, encoding="utf-8", newline="\n")
        full = dict(os.environ, **GIT_ENV, **{k: str(v) for k, v in env.items()})
        full.update({
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "GITHUB_OUTPUT": out.as_posix(), "GITHUB_STEP_SUMMARY": summ.as_posix(),
            "GITHUB_REPOSITORY": REPO, "GITHUB_SERVER_URL": "https://github.com", "GITHUB_RUN_ID": "4242",
            "FAKE_GH_LOG": self.log.as_posix(), "FAKE_GH_RULES": self.rules.as_posix(),
        })
        p = subprocess.run(["bash", "--noprofile", "--norc", "-eo", "pipefail", step.as_posix()],
                           cwd=cwd, env=full, capture_output=True, text=True)
        outputs = {}
        for line in out.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                outputs[k] = v
        return p.returncode, p.stdout + p.stderr, outputs


# ---- assemble ------------------------------------------------------------------
BUILD = workflow_step(PROD_RELEASE, "assemble", "Build release branch")["run"]
ENVS = '["int", "qa", "stage"]'


def assemble(sb, stories, shas, held="", br="release/2026.300"):
    return sb.run(BUILD, sb.runner(), {"PROD_BRANCH": "main", "BR": br, "STORIES": stories, "SHAS": shas,
                                       "HELD": held, "ENV_BRANCHES": ENVS, "RELEASE_PREFIX": "release/"})


def test_assemble():
    print("\nAssemble ships only the gathered stories (BUG-002, TS-079/080, TS-071)")
    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(tmp)
        # B is squash-merged into int and put on hold; A is clean.
        sb.branch("feature/PROJ-301-b")
        sb.commit("force-app/f/storyB.txt", "B\n", "B")
        sb.push("feature/PROJ-301-b")
        sb.branch("int")
        sb.commit("force-app/f/storyB.txt", "B\n", "B (#1) [squash]")
        sb.push("int")
        sb.branch("feature/PROJ-302-a")
        a1 = sb.commit("force-app/f/storyA.txt", "A\n", "A")
        sb.push("feature/PROJ-302-a")
        # A2: the developer settled a conflict with `git merge origin/int`.
        sb.git(sb.dev, "merge", "-q", "--no-edit", "origin/int")
        a2 = sb.git(sb.dev, "rev-parse", "HEAD")
        sb.push("HEAD", "feature/PROJ-302-a-int")
        # A3: merged B's branch itself.
        sb.git(sb.dev, "checkout", "-q", "-B", "a3", a1)
        sb.git(sb.dev, "merge", "-q", "--no-edit", "origin/feature/PROJ-301-b")
        a3 = sb.git(sb.dev, "rev-parse", "HEAD")
        sb.push("HEAD", "feature/PROJ-302-a-b")

        rc, out, o = assemble(sb, "feature/PROJ-302-a", a1, held="feature/PROJ-301-b")
        check("clean story: assembled", rc, 0)
        check("clean story: release has base + A only", sb.files("release/2026.300"),
              ["force-app/f/base.txt", "force-app/f/storyA.txt"])
        check("clean story: changed=true", o.get("changed"), "true")

        rc, out, _ = assemble(sb, "feature/PROJ-302-a-int", a2, held="feature/PROJ-301-b", br="release/2026.301")
        check("story that merged int: refused", rc != 0, True)
        check("…naming int and the passenger commit", ("commit(s) of int" in out, "B (#1) [squash]" in out),
              (True, True))
        check("…and no release branch pushed", sb.ref("release/2026.301"), "")
        rc, out, _ = assemble(sb, "feature/PROJ-302-a-int", a2, held="", br="release/2026.301")
        check("…refused even when the passenger is merely not selected", rc != 0, True)

        rc, out, _ = assemble(sb, "feature/PROJ-302-a-b", a3, held="feature/PROJ-301-b", br="release/2026.302")
        check("story that merged a held story's branch: refused", rc != 0, True)
        check("…naming the held story's file", "feature/PROJ-301-b: force-app/f/storyB.txt" in out, True)
        check("…and no release branch pushed", sb.ref("release/2026.302"), "")
        rc, out, _ = assemble(sb, "feature/PROJ-302-a-b", a3, held="release/2026.299", br="release/2026.302")
        check("a release PR on hold is not a story", rc, 0)

        # TS-071: a hand commit on an existing release branch.
        sb.branch("release/2026.303")
        sb.commit("force-app/f/hand.txt", "hand\n", "hand push")
        sb.push("release/2026.303")
        rc, out, _ = assemble(sb, "feature/PROJ-302-a", a1, br="release/2026.303")
        check("stale release branch with an unaccounted commit: refused",
              (rc != 0, "already contains commits" in out), (True, True))

        # Re-assembly after a hotfix landed on main takes the hotfix in.
        sb.branch("main")
        sb.commit("force-app/f/hotfix.txt", "hotfix\n", "hotfix")
        sb.push("main")
        rc, out, o = assemble(sb, "feature/PROJ-302-a", a1)
        check("re-assembly after a hotfix: assembled, changed", (rc, o.get("changed")), (0, "true"))
        check("…the release now contains the hotfix", "force-app/f/hotfix.txt" in sb.files("release/2026.300"), True)
        rc, out, o = assemble(sb, "feature/PROJ-302-a", a1)
        check("re-assembly with nothing new: changed=false", (rc, o.get("changed")), (0, "false"))


# ---- production delta ------------------------------------------------------------
def test_prod_delta_three_dot():
    print("\nThe production delta is three-dot, and a release behind the prod branch never deploys (BUG-003)")
    wf = yaml.safe_load(PROD_RELEASE.read_text(encoding="utf-8"))
    for job in ("validate", "deploy"):
        calls = [s for s in wf["jobs"][job]["steps"] if "sf-pipeline/deploy@" in str(s.get("uses", ""))]
        check(f"{job}: one deploy call", len(calls), 1)
        check(f"{job}: delta_merge_base is 'true'", str((calls[0].get("with") or {}).get("delta_merge_base")), "true")
    names = [s.get("name") or s.get("uses") for s in wf["jobs"]["deploy"]["steps"]]
    guard = [i for i, n in enumerate(names) if n.startswith("Prod branch unchanged")]
    check("deploy: the prod-branch guard runs before the deploy",
          bool(guard) and guard[0] < next(i for i, n in enumerate(names) if "sf-pipeline/deploy@" in n), True)

    guard = workflow_step(PROD_RELEASE, "deploy", "Prod branch unchanged")["run"]
    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(tmp)
        sb.branch("release/2026.300")
        sb.commit("force-app/f/story.txt", "S\n", "story")
        sb.push("release/2026.300")
        r = sb.runner("release/2026.300")
        rc, out, _ = sb.run(guard, r, {"PROD_BRANCH": "main", "VERSION": "2026.300"})
        check("prod branch unchanged: the deploy goes ahead", rc, 0)
        sb.branch("main")
        sb.commit("force-app/f/hotfix.txt", "H\n", "hotfix")
        sb.push("main")
        rc, out, _ = sb.run(guard, r, {"PROD_BRANCH": "main", "VERSION": "2026.300"})
        check("a hotfix merged while waiting for approval: refused before deploying",
              (rc != 0, "1 commit(s) that v2026.300 doesn't" in out), (True, True))


# ---- preflight -------------------------------------------------------------------
PF = action_step(PREFLIGHT, "check")["run"]
SHA = "a" * 40


def preflight(sb, labels=("prod-approved",), decision="", approved_at="", behind="0", mss="CLEAN"):
    pr = {"number": 7, "reviewDecision": decision, "labels": [{"name": n} for n in labels]}
    sb.set_gh([
        {"match": r"^pr list .*--head release/2026\.300 ", "out": json.dumps(pr)},
        {"match": r"^api repos/o/r/pulls/7 ", "out": SHA},
        {"match": r"^api repos/o/r/pulls/7/reviews", "out": approved_at},
        {"match": r"/check-runs", "out": "0"},
        {"match": r"/status ", "out": "0"},
        {"match": rf"^api repos/o/r/compare/main\.\.\.{SHA} ", "out": behind},
        {"match": r"^pr view 7 .*mergeStateStatus", "out": mss},
    ])
    return sb.run(PF, sb.tmp, {"HEAD_BRANCH": "release/2026.300", "BASE_BRANCH": "main", "VERSION": "2026.300",
                               "APPROVAL_LABEL": "prod-approved", "DEPLOYED_LABEL": "deployed:prod"})


def test_preflight():
    print("\nPreflight (TS-077, TS-078, TS-087, BUG-003)")
    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(tmp)
        rc, out, o = preflight(sb)
        check("authorized, green, up to date: passes with PR + head", (rc, o.get("pr_number"), o.get("head_sha")),
              (0, "7", SHA))
        rc, out, _ = preflight(sb, behind="2")
        check("behind the prod branch: refused before anything deploys",
              (rc != 0, "2 commit(s) behind main" in out), (True, True))
        rc, out, _ = preflight(sb, mss="BEHIND")
        check("mergeStateStatus BEHIND: refused", rc != 0, True)
        rc, out, _ = preflight(sb, mss="DIRTY")
        check("mergeStateStatus DIRTY: refused", rc != 0, True)
        rc, out, _ = preflight(sb, labels=())
        check("no approval, no label: not authorized", (rc != 0, "is not authorized" in out), (True, True))
        rc, out, _ = preflight(sb, labels=(), decision="APPROVED", approved_at="b" * 40)
        check("approval of an older commit: refused", (rc != 0, "not at its current commit" in out), (True, True))
        rc, out, _ = preflight(sb, labels=(), decision="APPROVED", approved_at=SHA)
        check("approval of the current commit: passes", rc, 0)
        rc, out, _ = preflight(sb, labels=("prod-approved",), decision="APPROVED", approved_at="b" * 40)
        check("stale approval + prod-approved label: re-authorized", rc, 0)
        rc, out, _ = preflight(sb, labels=("prod-approved", "deployed:prod"))
        check("already deployed:prod: refused", (rc != 0, "carries 'deployed:prod'" in out), (True, True))


# ---- release-gather ----------------------------------------------------------------
G = action_step(GATHER, "gather")["run"]
BOT = "github-actions[bot]"


def statuses(*rows):
    return "\n".join("\t".join(r) for r in rows)


def proven(creator=BOT, stage="success"):
    return statuses(("deploy-int", BOT, "success"), ("deploy-qa", BOT, "success"), ("deploy-stage", creator, stage))


def test_gather():
    print("\nrelease-gather proves each story by the pipeline's own statuses (TS-073, TS-075, TS-076)")
    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(tmp)
        sb.branch("feature/S1")
        s1 = sb.commit("force-app/f/s1.txt", "1\n", "s1")
        sb.commit("force-app/f/s1.txt", "1b\n", "s1 after the promotions")
        sb.push("feature/S1")
        sb.branch("feature/S2")
        s2a = sb.commit("force-app/f/s2.txt", "2\n", "s2")
        s2b = sb.commit("force-app/f/s2.txt", "2b\n", "s2 again, int only")
        sb.push("feature/S2")
        sb.branch("feature/S3")
        s3 = sb.commit("force-app/f/s3.txt", "3\n", "s3")
        sb.push("feature/S3")
        sb.branch("feature/S4")
        s4old = sb.commit("force-app/f/s4.txt", "4\n", "s4 shipped")
        sb.push("HEAD", "main")
        s4new = sb.commit("force-app/f/s4.txt", "4b\n", "s4 follow-up")
        sb.push("feature/S4")
        sb.branch("feature/S5")
        s5 = sb.commit("force-app/f/s5.txt", "5\n", "s5 held")
        sb.push("feature/S5")
        prs = [
            {"number": 1, "headRefName": "feature/S1", "headRefOid": s1},
            {"number": 2, "headRefName": "feature/S2", "headRefOid": s2a},
            {"number": 3, "headRefName": "feature/S2", "headRefOid": s2b},
            {"number": 4, "headRefName": "feature/S3", "headRefOid": s3},
            {"number": 5, "headRefName": "feature/GONE", "headRefOid": "c" * 40},
            {"number": 6, "headRefName": "feature/S4", "headRefOid": s4old},
            {"number": 8, "headRefName": "feature/S4", "headRefOid": s4new},
            {"number": 9, "headRefName": "feature/S5", "headRefOid": s5},
        ]
        rules = [
            {"match": r"^pr list .*--label hold ", "out": "feature/S5"},
            {"match": r"^pr list .*--state merged --label deployed:stage ", "out": json.dumps(prs)},
            {"match": rf"commits/{s1}/statuses", "out": proven()},
            {"match": rf"commits/{s2a}/statuses", "out": proven()},
            {"match": rf"commits/{s2b}/statuses", "out": statuses(("deploy-int", BOT, "success"))},
            {"match": rf"commits/{s3}/statuses", "out": proven(creator="mallory")},
            {"match": rf"commits/{s4new}/statuses", "out": proven()},
            {"match": rf"commits/{s5}/statuses", "out": proven()},
        ]
        sb.set_gh(rules)
        env = {"LABEL": "deployed:stage", "EXCLUDE_LABEL": "hold", "BASE": "", "BASELINE": "main", "ONLY": "",
               "REQUIRE": "deploy-int,deploy-qa,deploy-stage", "STATUS_CREATOR": BOT, "REPO": REPO}
        rc, out, o = sb.run(G, sb.runner(), env)
        check("gathers the proven stories, in PR order", (rc, o.get("stories")),
              (0, "feature/S1 feature/S2 feature/S4"))
        check("…at the commit each deployed", o.get("shas"), f"{s1} {s2a} {s4new}")
        check("…and reports the held branches", o.get("held"), "feature/S5")
        check("a status posted by someone else is not proof (S3 left out, named)",
              "the one set by @mallory is not trusted" in out, True)
        check("a deleted story branch drops out with a notice", "Skipping 'feature/GONE'" in out, True)
        check("the newest commit not in the baseline is released (S4)", s4new in o.get("shas", ""), True)
        check("commits after the deployed one are named, not released (S1)",
              "'feature/S1' has 1 commit(s) after" in out, True)

        sb.set_gh(rules + [{"match": rf"^pr list .*--state merged --head {b} ",
                            "out": json.dumps([p for p in prs if p["headRefName"] == b])}
                           for b in ("feature/S3", "feature/S5")])
        rc, out, _ = sb.run(G, sb.runner(), dict(env, ONLY="feature/S3"))
        check("a named story that isn't proven fails the run", (rc != 0, "not deployed to every stage" in out),
              (True, True))
        rc, out, _ = sb.run(G, sb.runner(), dict(env, ONLY="feature/S5"))
        check("a named story on hold fails the run", (rc != 0, "on 'hold'" in out), (True, True))


# ---- finalize ----------------------------------------------------------------------
PUBLISH = workflow_step(PROD_RELEASE, "finalize", "Merge release PR")["run"]


def test_finalize():
    print("\nFinalize is re-runnable and never tags what it could not merge (TS-088, TS-089)")
    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(tmp)
        env = {"VERSION": "2026.300", "BASELINE": "main", "PR_NUMBER": "7", "HEAD_SHA": SHA}
        sb.set_gh([
            {"match": r"^pr view 7 .*--json state", "out": "MERGED"},
            {"match": r"^release view", "rc": 1},
            {"match": r"^release create", "out": ""},
        ])
        rc, out, _ = sb.run(PUBLISH, sb.runner(), env)
        calls = sb.gh_calls()
        check("already merged: no second merge", ("already merged" in out, "pr merge" in calls), (True, False))
        check("…tagged and released", (rc, bool(sb.git(sb.origin, "tag", "-l", "v2026.300")), "release create" in calls),
              (0, True, True))
        rc, out, _ = sb.run(PUBLISH, sb.runner(), env)
        check("re-run with the tag present: no second tag push", rc, 0)

        sb.set_gh([
            {"match": r"^pr view 7 .*--json state", "out": "OPEN"},
            {"match": rf"^pr merge 7 .*--match-head-commit {SHA}", "rc": 1},
            {"match": r"^release", "out": ""},
        ])
        env["VERSION"] = "2026.301"
        rc, out, _ = sb.run(PUBLISH, sb.runner(), env)
        check("release branch moved after preflight: merge refused, step fails",
              (rc != 0, "Could not merge release PR #7" in out), (True, True))
        check("…no tag, no GitHub Release", (sb.git(sb.origin, "tag", "-l", "v2026.301"), "release create" in sb.gh_calls()),
              ("", False))


# ---- realign env branches ----------------------------------------------------------
SYNC = yaml.safe_load(SYNC_ACTION.read_text(encoding="utf-8"))["runs"]["steps"][0]["run"]


def protection(ctx):
    return json.dumps({
        "required_status_checks": {"strict": False, "contexts": [ctx], "checks": [{"context": ctx, "app_id": 15368}]},
        "enforce_admins": {"enabled": False},
        "required_pull_request_reviews": {"dismiss_stale_reviews": False, "require_code_owner_reviews": False,
                                          "required_approving_review_count": 0},
        "allow_force_pushes": {"enabled": False}, "allow_deletions": {"enabled": False},
    })


def test_realign():
    print("\nPost-release realign: merge, reset or leave each env branch; restore protection per branch "
          "(TS-090, TS-091, TS-092, BUG-008)")
    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(tmp)
        old_main = sb.git(sb.dev, "rev-parse", "HEAD")
        sb.commit("force-app/f/conflict.txt", "main\n", "c")
        sb.push("main")
        for b in ("int", "qa", "stage", "uat"):
            sb.push(old_main, b)
        sb.branch("int", "origin/int")
        sb.commit("force-app/f/unreleased.txt", "u\n", "unreleased story on int")
        sb.push("int")
        int_before = sb.ref("int")
        sb.branch("stage", "origin/stage")
        sb.commit("force-app/f/conflict.txt", "stage\n", "conflicting change on stage")
        sb.push("stage")
        stage_before = sb.ref("stage")
        sb.branch("main")
        sb.commit("force-app/f/released.txt", "r\n", "release")
        sb.push("main")
        tip = sb.ref("main")
        # Every PUT for qa is refused; the others succeed.
        sb.set_gh(
            [{"match": rf"^api repos/o/r/branches/{b}/protection$", "out": protection(f"deploy-{b}")}
             for b in ("int", "qa", "uat")]
            + [{"match": r"^api -X DELETE ", "out": ""},
               {"match": r"^api -X PUT repos/o/r/branches/qa/protection", "rc": 1},
               {"match": r"^api -X PUT ", "out": ""}])
        url = "https://x-access-token:tok@github.com/o/r.git"
        rc, out, _ = sb.run(SYNC, sb.runner(), {
            "GH_TOKEN": "tok", "BASELINE": "main", "ENV_BRANCHES": '["int", "qa", "stage", "uat", "main"]',
            "PIPELINE_PAT": "", "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": f"url.{sb.origin.as_posix()}.insteadOf", "GIT_CONFIG_VALUE_0": url})
        int_after = sb.ref("int")
        parents = sb.git(sb.origin, "log", "-1", "--format=%P %an", int_after).split()
        check("int (unreleased work): main merged in, nothing discarded",
              (parents[:2], parents[2] if len(parents) > 2 else ""), ([int_before, tip], "sf-release-bot"))
        check("qa (nothing of its own): reset to main", sb.ref("qa"), tip)
        check("uat: reset to main", sb.ref("uat"), tip)
        check("stage (conflicts): left as is, with a warning",
              (sb.ref("stage"), "stage has changes not in main that conflict" in out), (stage_before, True))
        check("a refused restore fails the step", rc != 0, True)
        check("…and is reported against qa only", re.findall(r"not fully restored on:(.*?)\.", out), [" qa"])
        check("…the error names qa as unprotected", "Failed to restore protection on qa" in out, True)
        check("int and uat restored", ("protection restored on int" in out, "protection restored on uat" in out),
              (True, True))
        puts = re.findall(r"^api -X PUT repos/o/r/branches/(\S+)/protection.*\n  input: (.*)$", sb.gh_calls(), re.M)
        check("each restore PUTs that branch's own snapshot",
              sorted({b for b, body in puts if f'"deploy-{b}"' in body}), ["int", "qa", "uat"])
        check("…and no other", [b for b, body in puts if f'"deploy-{b}"' not in body], [])
        check("…qa tried full then minimal", [b for b, _ in puts].count("qa"), 2)


# ---- Sync Env Branches -------------------------------------------------------------
SYNC_WF = workflow_step(SYNC_ENV, "sync", "Merge ")


def test_sync_env_branches():
    print("\nSync Env Branches falls back to a PR when the push is refused (TS-093)")
    check("the branch reaches the script through env, not ${{ }} in the script",
          ("${{" in SYNC_WF["run"], "matrix.branch" in str(SYNC_WF.get("env"))), (False, True))
    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(tmp)
        sb.push("HEAD", "int")
        sb.branch("main")
        sb.commit("force-app/f/released.txt", "r\n", "release")
        sb.push("main")
        hook = sb.origin / "hooks" / "pre-receive"
        hook.write_text('#!/usr/bin/env bash\nwhile read -r o n ref; do [ "$ref" = refs/heads/int ] && '
                        '{ echo "protected branch"; exit 1; }; done; exit 0\n', encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        int_before = sb.ref("int")
        env = {"BR": "int", "GH_TOKEN": "tok", "PROD_BRANCH": "main"}
        sb.set_gh([{"match": r"^pr create --base int --head sync/main-into-int-4242", "out": "https://x/pull/9"}])
        rc, out, _ = sb.run(SYNC_WF["run"], sb.runner(), env)
        check("refused push: a sync PR is opened, the job doesn't fail",
              (rc, "attempting a sync PR" in out, "pr create --base int" in sb.gh_calls()), (0, True, True))
        check("…from a pushed sync branch, int untouched",
              (sb.ref("sync/main-into-int-4242") != "", sb.ref("int")), (True, int_before))
        sb.set_gh([{"match": r"^pr create", "rc": 1}])
        rc, out, _ = sb.run(SYNC_WF["run"], sb.runner(), env)
        check("PR creation refused too: a warning, not a failure", (rc, "Could not open a sync PR" in out), (0, True))


# ---- nothing typed by a person is pasted into a script --------------------------------
OWNED = [PROD_RELEASE, SYNC_ENV, ROOT / ".github" / "workflows" / "project-init.yml",
         ROOT / ".github" / "workflows" / "org-bootstrap.yml", GATHER, PREFLIGHT, SYNC_ACTION]


def test_no_expressions_in_run():
    print("\nRun blocks read inputs through env, and the dispatch version must be a plain name (NEW-WS1-02)")
    for path in OWNED:
        d = yaml.safe_load(path.read_text(encoding="utf-8"))
        steps = [s for j in (d.get("jobs") or {}).values() for s in j.get("steps") or []] \
            or (d.get("runs") or {}).get("steps") or []
        left = sorted({e for s in steps for e in re.findall(r"\$\{\{\s*(.*?)\s*\}\}", s.get("run") or "")})
        check(f"{path.name}: no ${{{{ }}}} inside a run: block", left, [])
    ref = workflow_step(PROD_RELEASE, "plan", "ref")["run"]
    with tempfile.TemporaryDirectory() as tmp:
        sb = Sandbox(tmp)
        env = {"EARLIER": "int,qa", "LAST_ENV": "stage", "RELEASE_PREFIX": "release/"}
        rc, out, o = sb.run(ref, sb.tmp, dict(env, VERSION="2026.300"))
        check("a plain version: ref + every stage's status", (rc, o.get("ref"), o.get("chain_statuses")),
              (0, "release/2026.300", "deploy-int,deploy-qa,deploy-stage"))
        rc, out, o = sb.run(ref, sb.tmp, dict(env, VERSION='x" ; echo chain_statuses= >> "$GITHUB_OUTPUT" ; echo "'))
        check("a crafted version: refused, no outputs (the every-stage proof can't be emptied)", (rc != 0, o), (True, {}))


def main():
    print("Release train suite")
    if not shutil.which("bash") or not shutil.which("git"):
        print("  (skipped — bash and git are needed)")
        return 0
    for fn in (test_assemble, test_prod_delta_three_dot, test_preflight, test_gather, test_finalize,
               test_realign, test_sync_env_branches, test_no_expressions_in_run):
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
