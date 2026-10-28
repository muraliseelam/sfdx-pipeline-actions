#!/usr/bin/env python3
"""
test_promotion_ops.py — offline checks for label promotion and the ops runbooks.

Runs the REAL `run:` blocks of the promotion workflow, the merge/guard actions
and the ops runbooks against throwaway local git repos, with a fake `gh` (and,
where the machine has none, a fake `jq`) on PATH. Nothing reaches GitHub or a
Salesforce org.

  * Recreate Feature  — the name is normalised to a ref git accepts; `..` is
                        dropped, never resolved; a reset records the old tip
  * Rollback          — refuses an unmerged PR / a non-SHA ref; cuts the revert
                        from the production baseline when it applies there, so
                        it carries nothing else; refuses a never-released change
                        under delta_baseline: prod; never reverts a look-alike file
  * Redeploy          — unknown environment / profiles_mode fail with the list
  * promote prepare   — every refusal is explained, clears the label and opens
                        no stray PR; "No commits between" is the one benign
                        create failure; an expired PAT is not
  * guard / merge-pr  — UNKNOWN mergeability and non-bot statuses never pass;
                        a moved head is never merged
  * jira.py           — only a real issue key reaches the Jira API path
  * no `${{ }}` interpolation of caller-supplied text inside a `run:` script

Run:  python3 tests/test_promotion_ops.py
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WF = os.path.join(ROOT, ".github", "workflows")
ACT = os.path.join(ROOT, "sf-pipeline")
REPO = "owner/repo"
BASH = shutil.which("bash") or "bash"
BOT = "github-actions[bot]"

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


# --------------------------------------------------------------------------
# Locating run blocks
# --------------------------------------------------------------------------
def wf_step(workflow, job, name):
    with open(os.path.join(WF, workflow), encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    for s in d["jobs"][job]["steps"]:
        if (s.get("name") or "").startswith(name) or s.get("id") == name:
            return s["run"]
    raise KeyError(f"{workflow}:{job}:{name}")


def action_step(action, name):
    with open(os.path.join(ACT, action, "action.yml"), encoding="utf-8") as fh:
        d = yaml.safe_load(fh)
    for s in d["runs"]["steps"]:
        if (s.get("name") or "").startswith(name) or s.get("id") == name:
            return s["run"]
    raise KeyError(f"{action}:{name}")


# --------------------------------------------------------------------------
# Fake gh / jq / sleep
# --------------------------------------------------------------------------
FAKE_GH = r'''
import json, os, re, sys
state_path = os.environ["FAKEGH_STATE"]
st = json.load(open(state_path, encoding="utf-8"))
a = sys.argv[1:]
def opt(n, d=None):
    return a[a.index(n) + 1] if n in a else d
def log(kind, **kw):
    with open(os.environ["FAKEGH_LOG"], "a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(kind=kind, **kw)) + "\n")
def save():
    json.dump(st, open(state_path, "w", encoding="utf-8"))
def pr(n):
    return st["prs"].setdefault(str(n), {})
def path_arg():
    skip = {"-X", "-f", "-F", "--jq", "-q", "--input", "-H"}
    i = 1
    while i < len(a):
        if a[i] in skip: i += 2; continue
        if a[i].startswith("-"): i += 1; continue
        return a[i]
    return ""
if a[0] == "pr":
    sub, n = a[1], a[2] if len(a) > 2 else ""
    if sub == "view":
        p = pr(n); f = opt("--json", "")
        if f == "state,headRefOid,labels":
            print(p["state"]); print(p["head"])
            for l in p.get("labels", []): print(l)
        elif f == "state,headRefOid":
            if p.get("unreadable"): sys.exit(1)
            print(p["state"] + " " + p["head"])
        elif f == "mergeable":
            seq = p.get("mergeable", ["MERGEABLE"])
            print(seq.pop(0) if len(seq) > 1 else seq[0]); save()
        elif f in ("state", "title", "url", "headRefOid", "baseRefName"):
            key = {"headRefOid": "head", "baseRefName": "base"}.get(f, f)
            print(p.get(key, f"https://github.com/{os.environ.get('REPO','o/r')}/pull/{n}" if f == "url" else ""))
        else:
            sys.stderr.write(f"fake gh: unsupported --json {f}\n"); sys.exit(3)
    elif sub == "comment":
        body = opt("--body")
        if body is None:
            body = open(opt("--body-file"), encoding="utf-8").read()
        log("comment", pr=n, body=body)
    elif sub == "close":
        pr(n)["state"] = "CLOSED"; save(); log("close", pr=n)
    elif sub == "list":
        print(st.get("open_env_pr", ""))
    elif sub == "create":
        c = st.get("create", {"number": 900})
        log("create", base=opt("--base"), head=opt("--head"), title=opt("--title"), body=opt("--body"))
        if "stderr" in c:
            sys.stderr.write(c["stderr"] + "\n"); sys.exit(1)
        st["prs"][str(c["number"])] = {"state": "OPEN", "head": c.get("head", ""),
                                       "mergeable": c.get("mergeable", ["MERGEABLE"])}
        save(); print(f"https://github.com/o/r/pull/{c['number']}")
    elif sub == "merge":
        log("merge", pr=n, match=opt("--match-head-commit"))
        m = st.get("merge", "ok")
        if m == "ok":
            pr(n)["state"] = "MERGED"; save()
        elif m != "accepted-only":
            print(m); sys.exit(1)
    elif sub == "edit":
        log("edit", pr=n, args=a[3:])
    else:
        sys.stderr.write(f"fake gh: unsupported pr {sub}\n"); sys.exit(3)
elif a[0] == "api":
    path, method, jqf = path_arg(), opt("-X", "GET"), opt("--jq")
    m = re.match(r"repos/[^/]+/[^/]+/commits/([0-9a-f]+)/statuses", path)
    if m:
        print(json.dumps(st.get("statuses", {}).get(m.group(1), [])))
    elif method == "DELETE" and "/labels/" in path:
        n, lab = re.match(r"repos/[^/]+/[^/]+/issues/(\d+)/labels/(.+)$", path).groups()
        log("unlabel", pr=n, label=lab)
    elif "/git/ref/heads/" in path:
        tip = st.get("tips", {}).get(path.split("/git/ref/heads/", 1)[1], "")
        if not tip: sys.exit(1)
        print(tip)
    elif re.match(r"repos/[^/]+/[^/]+/pulls/\d+$", path):
        p = st["pulls"][path.rsplit("/", 1)[1]]
        print({".merged": str(p["merged"]).lower(), ".merge_commit_sha": p["merge_commit_sha"]}[jqf])
    else:
        sys.stderr.write(f"fake gh: unsupported api {method} {path}\n"); sys.exit(3)
else:
    sys.stderr.write(f"fake gh: unsupported {a}\n"); sys.exit(3)
'''

# Only for machines without jq (CI has the real one): the filters these blocks use.
FAKE_JQ = r'''
import json, sys
a = sys.argv[1:]
args, flags, pos = {}, set(), []
i = 0
while i < len(a):
    if a[i] == "--arg": args[a[i + 1]] = a[i + 2]; i += 3; continue
    if a[i].startswith("-"): flags.add(a[i]); i += 1; continue
    pos.append(a[i]); i += 1
f = pos[0]
data = json.load(open(pos[1], encoding="utf-8")) if len(pos) > 1 else json.loads(sys.stdin.read() or "null")
BOT = '.creator.login == "github-actions[bot]"'
out = None
if f == '.[] | select(.context == $c and ' + BOT + ') | .state':
    out = [s["state"] for s in data if s["context"] == args["c"] and s["creator"]["login"] == "github-actions[bot]"]
elif f == '.[] | select(.context == "pr-validation" and ' + BOT + ') | .state':
    out = [s["state"] for s in data if s["context"] == "pr-validation" and s["creator"]["login"] == "github-actions[bot]"]
elif f == '.coverage_measured == true and .coverage_pass != true':
    out = [data.get("coverage_measured") is True and data.get("coverage_pass") is not True]
elif f == '.mode == "gated"':
    out = [data.get("mode") == "gated"]
elif f == '.[] | select(.name==$t)':
    out = [json.dumps(r) for r in data if r["name"] == args["t"]]
elif f == '[.[].name] | join(", ")':
    out = [", ".join(r["name"] for r in data)]
elif f in (".name", ".gh_environment", ".test_level"):
    out = [data[f[1:]]]
else:
    sys.stderr.write(f"fake jq: unsupported filter {f!r}\n"); sys.exit(3)
for o in out:
    print(str(o).lower() if isinstance(o, bool) else o)
if "-e" in flags:
    sys.exit(0 if out and out[-1] not in (False, None) else 1)
'''


class Sandbox:
    """A temp dir with fake gh (+ jq when missing) and a no-op sleep on PATH."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="promo-ops-")
        self.bin = os.path.join(self.dir, "bin")
        os.makedirs(self.bin)
        py = sys.executable.replace("\\", "/")
        self._tool("gh", FAKE_GH, py)
        if not shutil.which("jq"):
            self._tool("jq", FAKE_JQ, py)
        with open(os.path.join(self.bin, "sleep"), "w", newline="\n") as fh:
            fh.write("#!/usr/bin/env bash\nexit 0\n")
        os.chmod(os.path.join(self.bin, "sleep"), 0o755)
        self.state = os.path.join(self.dir, "state.json")
        self.log = os.path.join(self.dir, "gh.log")

    def _tool(self, name, src, py):
        with open(os.path.join(self.bin, name + ".py"), "w", encoding="utf-8") as fh:
            fh.write(src)
        with open(os.path.join(self.bin, name), "w", newline="\n") as fh:
            fh.write(f'#!/usr/bin/env bash\nexec "{py}" "$(dirname "$0")/{name}.py" "$@"\n')
        os.chmod(os.path.join(self.bin, name), 0o755)

    def run(self, script, env, state=None, cwd=None):
        with open(self.state, "w", encoding="utf-8") as fh:
            json.dump(state or {"prs": {}}, fh)
        open(self.log, "w").close()
        out = os.path.join(self.dir, "github_output")
        summ = os.path.join(self.dir, "step_summary")
        open(out, "w").close()
        open(summ, "w").close()
        e = dict(os.environ)
        e.update({"PATH": self.bin + os.pathsep + e["PATH"], "FAKEGH_STATE": self.state,
                  "FAKEGH_LOG": self.log, "GITHUB_OUTPUT": out, "GITHUB_STEP_SUMMARY": summ,
                  "REPO": REPO, "GITHUB_SERVER_URL": "https://github.com", "GH_TOKEN": "x",
                  "PYTHONUTF8": "1"})
        e.update(env)
        script_path = os.path.join(self.dir, "step.sh")
        with open(script_path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(script)
        # How the runner invokes a `shell: bash` step.
        p = subprocess.run([BASH, "--noprofile", "--norc", "-eo", "pipefail", script_path],
                           cwd=cwd or self.dir, env=e,
                           capture_output=True, text=True, encoding="utf-8")
        outputs = {}
        for line in open(out, encoding="utf-8").read().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                outputs[k] = v
        calls = [json.loads(line) for line in open(self.log, encoding="utf-8") if line.strip()]
        return p.returncode, p.stdout + p.stderr, outputs, calls, open(summ, encoding="utf-8").read()

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)


def git(cwd, *args):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {p.stderr}")
    return p.stdout.strip()


def commit_file(cwd, path, content, msg):
    full = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", newline="\n") as fh:
        fh.write(content)
    git(cwd, "add", "-A")
    git(cwd, "commit", "-qm", msg)
    return git(cwd, "rev-parse", "HEAD")


def new_origin(sb):
    """Bare origin + a working clone; main has one field file."""
    origin = os.path.join(sb.dir, "origin.git")
    work = os.path.join(sb.dir, "work")
    git(sb.dir, "init", "-q", "--bare", "-b", "main", origin)
    git(sb.dir, "clone", "-q", origin, work)
    for k, v in (("user.name", "t"), ("user.email", "t@t"), ("core.autocrlf", "false")):
        git(work, "config", k, v)
    commit_file(work, "force-app/f/Base__c.field-meta.xml", field("Base__c"), "base")
    git(work, "push", "-q", "origin", "HEAD:main")
    return origin, work


def field(name, length=40):
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<CustomField>\n"
            f"    <fullName>{name}</fullName>\n    <label>{name}</label>\n"
            f"    <length>{length}</length>\n    <required>false</required>\n"
            "    <type>Text</type>\n    <unique>false</unique>\n</CustomField>\n")


def far_field(name, length):
    """A field whose length line sits well away from its name lines."""
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<CustomField>\n"
            f"    <fullName>{name}</fullName>\n    <label>{name}</label>\n"
            "    <externalId>false</externalId>\n    <required>false</required>\n"
            "    <trackTrending>false</trackTrending>\n    <type>Text</type>\n"
            f"    <unique>false</unique>\n    <length>{length}</length>\n</CustomField>\n")


def remote_branches(origin):
    out = git(origin, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return sorted(out.split()) if out else []


# --------------------------------------------------------------------------
# Recreate Feature Branch (TS-064, TS-065)
# --------------------------------------------------------------------------
def test_recreate():
    print("Recreate Feature Branch")
    run = wf_step("sf-ops-recreate-feature.yml", "recreate", "Recreate the feature branch")
    cases = [
        ("PROJ 299 foo/../bar", "feature/PROJ-299-foo/bar"),
        ("feature/PROJ-7-x", "feature/PROJ-7-x"),
        ("a/./b//c.", "feature/a/b/c"),
        ("x.lock", "feature/x"),
        (".hidden..name", "feature/hidden.name"),
        ("-n", "feature/-n"),
    ]
    for raw, want in cases:
        sb = Sandbox()
        try:
            origin, work = new_origin(sb)
            rc, log, _, _, _ = sb.run(run, {"RAW_NAME": raw, "BASE_IN": "", "BASELINE": "main",
                                            "CONFIRM": "false"}, cwd=work)
            check(f"name {raw!r} -> {want} (rc={rc})", (rc, remote_branches(origin)), (0, sorted(["main", want])))
        finally:
            sb.close()
    for raw in ("..", "/", "@{}", ""):
        sb = Sandbox()
        try:
            origin, work = new_origin(sb)
            rc, log, _, _, _ = sb.run(run, {"RAW_NAME": raw, "BASE_IN": "", "BASELINE": "main",
                                            "CONFIRM": "false"}, cwd=work)
            check(f"name {raw!r} is refused, no branch pushed",
                  (rc, "invalid feature name" in log, remote_branches(origin)), (1, True, ["main"]))
        finally:
            sb.close()

    # An existing branch: refused without confirm_reset, reset (old tip recorded) with it.
    sb = Sandbox()
    try:
        origin, work = new_origin(sb)
        git(work, "checkout", "-qb", "feature/PROJ-290")
        old = commit_file(work, "force-app/f/Work__c.field-meta.xml", field("Work__c"), "work")
        git(work, "push", "-q", "origin", "feature/PROJ-290")
        env = {"RAW_NAME": "PROJ-290", "BASE_IN": "", "BASELINE": "main", "CONFIRM": "false"}
        rc, log, _, _, _ = sb.run(run, env, cwd=work)
        check("existing branch without confirm_reset is refused, untouched",
              (rc, "confirm_reset=true" in log, git(origin, "rev-parse", "feature/PROJ-290")), (1, True, old))
        rc, log, _, _, summ = sb.run(run, dict(env, CONFIRM="true"), cwd=work)
        check("confirm_reset=true resets it to the baseline",
              (rc, git(origin, "rev-parse", "feature/PROJ-290")), (0, git(origin, "rev-parse", "main")))
        check("the reset records the discarded tip", old in summ and old in log, True)
    finally:
        sb.close()


# --------------------------------------------------------------------------
# Rollback (TS-066, TS-067)
# --------------------------------------------------------------------------
def rollback_env(**kw):
    env = {"PR_NUMBER": "", "REF_IN": "", "BASE_IN": "", "ENTRY": "int", "BASELINE": "main",
           "DELTA_BASELINE": "prod"}
    env.update(kw)
    return env


def test_rollback():
    print("Rollback")
    run = wf_step("sf-ops-rollback.yml", "revert", "Create the revert branch")
    sb = Sandbox()
    try:
        origin, work = new_origin(sb)
        # int = main + an unreleased story; main later gains a released story.
        git(work, "checkout", "-qb", "int")
        other = commit_file(work, "force-app/f/Other__c.field-meta.xml", field("Other__c"), "other story")
        git(work, "push", "-q", "origin", "int")
        git(work, "checkout", "-q", "main")
        rel = commit_file(work, "force-app/f/Released__c.field-meta.xml", field("Released__c"), "released story")
        git(work, "push", "-q", "origin", "main")
        git(work, "checkout", "-q", "int")
        git(work, "merge", "-q", "--no-edit", "origin/main")
        git(work, "push", "-q", "origin", "int")
        git(work, "checkout", "-q", "main")
        state = {"prs": {}, "pulls": {"5": {"merged": False, "merge_commit_sha": rel},
                                      "6": {"merged": True, "merge_commit_sha": rel}}}

        rc, log, _, calls, _ = sb.run(run, rollback_env(PR_NUMBER="5"), state, cwd=work)
        check("an unmerged PR is refused (TS-066a)", (rc, "is not merged" in log, calls), (1, True, []))

        for bad in ("main", "--upload-pack=x", "HEAD~1"):
            rc, log, _, calls, _ = sb.run(run, rollback_env(REF_IN=bad), state, cwd=work)
            check(f"ref {bad!r} is refused as not a commit SHA", (rc, "not a commit SHA" in log), (1, True))

        # A released change: the revert is cut from main, so it carries nothing else.
        rc, log, _, calls, summ = sb.run(run, rollback_env(PR_NUMBER="6"), state, cwd=work)
        br = f"rollback/{rel[:8]}"
        files = git(origin, "diff", "--name-only", f"main...{br}").split() if rc == 0 else []
        check("released change: revert PR into int, cut from main (TS-066b)",
              (rc, [c.get("base") for c in calls if c["kind"] == "create"], "cut from `main`" in summ),
              (0, ["int"], True))
        check("released change: the revert branch carries ONLY the revert", files,
              ["force-app/f/Released__c.field-meta.xml"])
        check("released change: no carry-over warning", "[!WARNING]" in json.dumps(calls), False)

        # An unreleased change under delta_baseline: prod: a revert cut from int
        # would carry every unreleased int change — refused, nothing pushed.
        before = remote_branches(origin)
        rc, log, _, calls, summ = sb.run(run, rollback_env(REF_IN=other), state, cwd=work)
        check("unreleased change, delta_baseline prod: refused, no branch, no PR",
              (rc, "never reached 'main'" in log, "every unreleased change on 'int'" in log,
               "(a) fix forward" in log, "(c) set delta_baseline: target" in log, "Rollback refused" in summ,
               kinds(calls, "create"), remote_branches(origin)),
              (1, True, True, True, True, True, [], before))

        # Under delta_baseline: target the package is computed against int: allowed, with the warning.
        rc, log, _, calls, summ = sb.run(run, rollback_env(REF_IN=other, DELTA_BASELINE="target"), state, cwd=work)
        body = next((c["body"] for c in calls if c["kind"] == "create"), "")
        check("unreleased change, delta_baseline target: cut from int, warns about promotion",
              (rc, "cut from `int`" in summ, "::warning::" in log, "[!WARNING]" in body, "Do not promote" in body),
              (0, True, True, True, True))

        # A released change is still cut from main under target.
        rc, log, _, calls, summ = sb.run(run, rollback_env(PR_NUMBER="6", DELTA_BASELINE="target"), state, cwd=work)
        check("released change, delta_baseline target: still cut from main", (rc, "cut from `main`" in summ), (0, True))
    finally:
        sb.close()

    # Conflicting revert: stops with the exact commands (TS-067).
    sb = Sandbox()
    try:
        origin, work = new_origin(sb)
        git(work, "checkout", "-qb", "feature/x")
        c1 = commit_file(work, "force-app/f/Base__c.field-meta.xml", field("Base__c", 50), "len 50")
        git(work, "checkout", "-q", "main")
        commit_file(work, "force-app/f/Base__c.field-meta.xml", field("Base__c", 60), "len 60 on main")
        git(work, "push", "-q", "origin", "main", "feature/x")
        git(work, "push", "-q", "origin", "main:int")
        rc, log, _, calls, _ = sb.run(run, rollback_env(REF_IN=c1), {"prs": {}}, cwd=work)
        check("delta_baseline prod: a revert conflicting on main is refused with the commands for main (TS-067)",
              (rc, "never reached 'main'" in log, f"git checkout -B rollback/{c1[:8]} origin/main" in log,
               kinds(calls, "create"), f"rollback/{c1[:8]}" in remote_branches(origin)),
              (1, True, True, [], False))
        rc, log, _, calls, _ = sb.run(run, rollback_env(REF_IN=c1, DELTA_BASELINE="target"), {"prs": {}}, cwd=work)
        check("delta_baseline target: a conflicting revert stops with manual git commands, no PR (TS-067)",
              (rc, "Resolve manually" in log, f"git checkout -B rollback/{c1[:8]} origin/int" in log,
               kinds(calls, "create"), f"rollback/{c1[:8]}" in remote_branches(origin)),
              (1, True, True, [], False))
    finally:
        sb.close()

    # Rename detection would revert a look-alike field instead of the real one.
    sb = Sandbox()
    try:
        origin, work = new_origin(sb)
        git(work, "checkout", "-qb", "int")
        commit_file(work, "force-app/f/Story__c.field-meta.xml", far_field("Story__c", 40), "story")
        bump = commit_file(work, "force-app/f/Story__c.field-meta.xml", far_field("Story__c", 50), "story len 50")
        git(work, "push", "-q", "origin", "int")
        git(work, "checkout", "-q", "main")
        commit_file(work, "force-app/f/Story2__c.field-meta.xml", far_field("Story2__c", 50), "look-alike on main")
        git(work, "push", "-q", "origin", "main")
        br = f"rollback/{bump[:8]}"
        # With rename detection main would "revert" Story2__c cleanly; without, it is not on main: refused.
        rc, log, _, calls, summ = sb.run(run, rollback_env(REF_IN=bump), {"prs": {}}, cwd=work)
        check("no rename matching: the look-alike on main is not reverted (refused under prod)",
              (rc, br in remote_branches(origin)), (1, False))
        rc, log, _, calls, summ = sb.run(run, rollback_env(REF_IN=bump, DELTA_BASELINE="target"), {"prs": {}}, cwd=work)
        touched = git(origin, "diff", "--name-only", f"{br}~1", br).split() if rc == 0 else []
        check("the revert touches only the file the change touched (no rename matching)",
              (rc, touched), (0, ["force-app/f/Story__c.field-meta.xml"]))
    finally:
        sb.close()


# --------------------------------------------------------------------------
# Redeploy (TS-063)
# --------------------------------------------------------------------------
def test_redeploy():
    print("Redeploy Feature Branch")
    run = wf_step("sf-ops-redeploy-feature.yml", "plan", "Resolve target environment")
    auto = json.dumps([{"name": "int", "gh_environment": "int", "test_level": "RunSpecifiedTests"},
                       {"name": "qa", "gh_environment": "qa", "test_level": "NoTestRun"}])
    sb = Sandbox()
    try:
        rc, log, out, _, _ = sb.run(run, {"AUTO_JSON": auto, "TARGET": "nope", "PROFILES_MODE": ""})
        check("unknown environment fails with the valid list (TS-063)",
              (rc, "'nope' is not a configured environment. Valid: int, qa" in log), (1, True))
        rc, log, out, _, _ = sb.run(run, {"AUTO_JSON": auto, "TARGET": "qa", "PROFILES_MODE": "incremental"})
        check("a known environment resolves", (rc, out.get("gh_environment"), out.get("test_level")),
              (0, "qa", "NoTestRun"))
        rc, log, out, _, _ = sb.run(run, {"AUTO_JSON": auto, "TARGET": "qa", "PROFILES_MODE": 'full" ; id ; "'})
        check("an unknown profiles_mode is refused before it reaches the deploy action",
              (rc, "is not one of" in log, out), (1, True, {}))
    finally:
        sb.close()


# --------------------------------------------------------------------------
# Promotion prepare (TS-043, TS-046..TS-052, TS-055)
# --------------------------------------------------------------------------
SHA = "a" * 40
MOVED = "b" * 40


def st(context, state="success", creator=BOT):
    return {"context": context, "state": state, "creator": {"login": creator}}


def prepare_env(**kw):
    env = {"CONTROL_PR": "10", "ENVN": "qa", "ENVBR": "qa", "HEAD": "feature/PROJ-1", "SHA": SHA,
           "IS_ENTRY": "false", "NEEDED_ENVS": "int", "PAT": "x"}
    env.update(kw)
    return env


def prepare_state(**kw):
    s = {"prs": {"10": {"state": "MERGED", "head": SHA, "labels": ["deployed:int"], "title": "PROJ-1"}},
         "statuses": {SHA: [st("deploy-int")]}, "tips": {"feature/PROJ-1": SHA},
         "create": {"number": 900, "head": SHA, "mergeable": ["MERGEABLE"]}}
    s.update(kw)
    return s


def kinds(calls, kind):
    return [c for c in calls if c["kind"] == kind]


def test_prepare():
    print("Promotion prepare")
    run = wf_step("sf-promote-label.yml", "prepare", "ensure")
    sb = Sandbox()
    try:
        def go(env=None, **state):
            return sb.run(run, prepare_env(**(env or {})), prepare_state(**state))

        rc, log, out, calls, _ = go()
        check("happy path: env PR opened at the deployed commit and mergeable",
              (rc, out.get("env_pr"), out.get("env_pr_created"), kinds(calls, "comment")), (0, "900", "true", []))

        rc, log, out, calls, _ = go({"IS_ENTRY": "true", "ENVN": "int"})
        check("deploy:<entry> is not a promotion: refused, label removed (TS-043)",
              (rc, out.get("reported"), [c["label"] for c in kinds(calls, "unlabel")],
               "is not a promotion" in kinds(calls, "comment")[0]["body"]), (1, "true", ["deploy:int"], True))

        rc, log, out, calls, _ = go(prs={"10": {"state": "OPEN", "head": SHA, "labels": []}})
        check("an OPEN control PR is refused (TS-055)",
              (rc, "still open" in kinds(calls, "comment")[0]["body"], kinds(calls, "create")), (1, True, []))

        rc, log, out, calls, _ = go(statuses={SHA: [st("deploy-int"), st("deploy-qa")]},
                                    prs={"10": {"state": "MERGED", "head": SHA, "labels": ["deployed:int", "deployed:qa"]}})
        check("deployed:qa with the bot's deploy-qa is a no-op (TS-048)",
              (rc, out.get("skip"), "nothing to do" in kinds(calls, "comment")[0]["body"], kinds(calls, "create")),
              (0, "true", True, []))

        rc, log, out, calls, _ = go(statuses={SHA: [st("deploy-int"), st("deploy-qa", creator="someone")]},
                                    prs={"10": {"state": "MERGED", "head": SHA, "labels": ["deployed:int", "deployed:qa"]}})
        check("a hand-added deployed:qa + forged deploy-qa is not trusted: promotes (TS-049)",
              (rc, out.get("skip"), "label not trusted" in log, len(kinds(calls, "create"))), (0, None, True, 1))

        rc, log, out, calls, _ = go({"ENVN": "stage", "ENVBR": "stage", "NEEDED_ENVS": "int,qa"},
                                    statuses={SHA: [st("deploy-int"), st("deploy-qa", creator="someone")]})
        body = kinds(calls, "comment")[0]["body"] if kinds(calls, "comment") else ""
        check("stage needs int AND qa; a forged deploy-qa does not count (TS-042, TS-050)",
              (rc, "`qa` (`deploy-qa`=missing)" in body, kinds(calls, "create")), (1, True, []))

        rc, log, out, calls, _ = go(tips={"feature/PROJ-1": MOVED})
        check("feature tip moved past the deployed commit: refused, no PR (TS-046)",
              (rc, "has moved on" in kinds(calls, "comment")[0]["body"], kinds(calls, "create")), (1, True, []))

        rc, log, out, calls, _ = go(create={"stderr": "HTTP 401: Bad credentials (https://api.github.com/graphql)"})
        body = kinds(calls, "comment")[0]["body"]
        check("an expired PIPELINE_PAT is a loud failure, label re-addable (TS-051)",
              (rc, out.get("reported"), "could not start" in body, "PIPELINE_PAT" in body,
               [c["label"] for c in kinds(calls, "unlabel")], kinds(calls, "close")),
              (1, "true", True, True, ["deploy:qa"], []))

        rc, log, out, calls, _ = go(create={"stderr": "pull request create failed: GraphQL: No commits between qa and feature/PROJ-1 (createPullRequest)"})
        check("'No commits between' is the one benign create failure: no-op (TS-052)",
              (rc, out.get("skip"), "nothing to do" in kinds(calls, "comment")[0]["body"]), (0, "true", True))

        rc, log, out, calls, _ = go(create={"number": 901, "head": SHA, "mergeable": ["CONFLICTING"]})
        check("a conflicting env PR is reported on the control PR and closed (TS-047)",
              (rc, "merge conflicts" in kinds(calls, "comment")[0]["body"], [c["pr"] for c in kinds(calls, "close")]),
              (1, True, ["901"]))

        rc, log, out, calls, _ = go(create={"number": 902, "head": SHA, "mergeable": ["UNKNOWN"]})
        check("mergeable stays UNKNOWN: refused, the auto-created PR closed (TS-039)",
              (rc, "did not start" in kinds(calls, "comment")[0]["body"], [c["pr"] for c in kinds(calls, "close")]),
              (1, True, ["902"]))
    finally:
        sb.close()


# --------------------------------------------------------------------------
# Promotion "Record why" (TS-053)
# --------------------------------------------------------------------------
def test_record_why():
    print("Promotion failure reason")
    run = wf_step("sf-promote-label.yml", "deploy", "Record why the promotion stopped")
    sb = Sandbox()
    try:
        base = {"SHA": SHA, "ENVN": "qa", "NEEDED_ENVS": "int", "ENVBR": "qa", "JOB_STATUS": "failure",
                "GUARD": "success", "FRESH": "success", "FRESH_REASON": "", "DEPLOY": "failure"}
        for mode, want in (("gated", "org and `qa` still agree"), ("validate", "disagree until this is fixed")):
            wd = os.path.join(sb.dir, "w-" + mode)
            os.makedirs(os.path.join(wd, ".deploy"), exist_ok=True)
            with open(os.path.join(wd, ".deploy", "report.json"), "w") as fh:
                json.dump({"mode": mode, "coverage_measured": True, "coverage_pass": False}, fh)
            rc, log, out, _, _ = sb.run(run, base, cwd=wd)
            check(f"coverage gate failed in {mode} mode -> the note says the org/branch state",
                  (rc, want in out.get("note", "")), (0, True))
        rc, log, out, _, _ = sb.run(run, dict(base, DEPLOY="skipped", GUARD="failure"),
                                    {"prs": {}, "statuses": {SHA: [st("deploy-int", creator="someone")]}})
        check("ordering-guard failure names the missing stage (TS-040)",
              "missing `int` (`deploy-int`=missing)" in out.get("reason", ""), True)
    finally:
        sb.close()


# --------------------------------------------------------------------------
# guard / merge-pr (TS-037, TS-039, TS-040)
# --------------------------------------------------------------------------
def test_guard_and_merge():
    print("Pre-deploy guard and merge")
    pre = action_step("guard", "PR state + merge-conflict precheck")
    order = action_step("guard", "Ordering guard")
    env = {"PR": "7", "ENVN": "qa", "BASE": "qa", "SHA": SHA}
    sb = Sandbox()
    try:
        rc, log, _, calls, _ = sb.run(pre, env, {"prs": {"7": {"state": "OPEN", "head": SHA, "mergeable": ["UNKNOWN"]}}})
        check("mergeable=UNKNOWN after every retry is a refusal (TS-039)",
              (rc, "mergeability is 'UNKNOWN'" in log), (1, True))
        rc, log, _, calls, _ = sb.run(pre, env, {"prs": {"7": {"state": "OPEN", "head": SHA, "mergeable": ["CONFLICTING"]}}})
        check("a conflicting PR is refused before the org (TS-038)",
              (rc, "merge conflicts" in kinds(calls, "comment")[0]["body"]), (1, True))
        rc, log, _, calls, _ = sb.run(order, dict(env, NEEDED_ENVS="int"),
                                      {"prs": {}, "statuses": {SHA: [st("deploy-int", creator="muraliseelam")]}})
        check("ordering guard ignores a deploy-int status not written by the bot (TS-040)",
              (rc, "deploy-int=missing" in log), (1, True))
        rc, log, _, calls, _ = sb.run(order, dict(env, NEEDED_ENVS="int"),
                                      {"prs": {}, "statuses": {SHA: [st("deploy-int")]}})
        check("ordering guard passes on the bot's deploy-int", rc, 0)
    finally:
        sb.close()

    merge = action_step("merge-pr", "merge")
    env = {"PR": "8", "BASE": "qa", "SHA": SHA}
    sb = Sandbox()
    try:
        rc, log, out, calls, _ = sb.run(merge, env, {"prs": {"8": {"state": "OPEN", "head": MOVED}}})
        check("a head that moved after the deploy is never merged (TS-037)",
              (rc, out.get("merged"), "head moved" in out.get("reason", ""), kinds(calls, "merge")),
              (1, "false", True, []))
        rc, log, out, calls, _ = sb.run(merge, env, {"prs": {"8": {"state": "OPEN", "head": SHA}}})
        check("merged pinned to the deployed commit",
              (rc, out.get("merged"), [c["match"] for c in kinds(calls, "merge")]), (0, "true", [SHA]))
        rc, log, out, calls, _ = sb.run(merge, env, {"prs": {"8": {"state": "MERGED", "head": SHA}}})
        check("re-run on a PR already merged at that commit is idempotent", (rc, out.get("merged")), (0, "true"))
        rc, log, out, calls, _ = sb.run(merge, env, {"prs": {"8": {"state": "OPEN", "head": SHA}},
                                                     "merge": "accepted-only"})
        check("a merge GitHub accepted but did not perform is a failure",
              (rc, "still OPEN" in out.get("reason", "")), (1, True))
    finally:
        sb.close()


# --------------------------------------------------------------------------
# Jira: the issue key is the only caller text that reaches the API path
# --------------------------------------------------------------------------
def test_jira():
    print("Jira")
    script = os.path.join(ACT, "scripts", "jira.py")
    # Port 9 (discard) on localhost: any request fails fast with a warning.
    env = dict(os.environ, JIRA_BASE_URL="http://127.0.0.1:9", JIRA_EMAIL="e", JIRA_API_TOKEN="t")

    def jira(*args):
        p = subprocess.run([sys.executable, script, *args], env=env, capture_output=True, text=True)
        return p.returncode, p.stdout + p.stderr

    check("key from a branch name", jira("extract-key", "feature/PROJ-123-foo")[1].strip(), "PROJ-123")
    check("no key in a plain name", jira("extract-key", "feature/foo")[1].strip(), "")
    for bad in ("../../myself", "PROJ-1/../../x", "proj-1"):
        rc, out = jira("deploy", bad, "qa", "", "")
        check(f"key {bad!r} is refused before any request", (rc, "is not a Jira issue key" in out, "failed" in out),
              (0, True, False))
    rc, out = jira("deploy", "PROJ-1", "qa", "", "")
    check("a real key is used (request attempted, best-effort warning)", (rc, "comment on PROJ-1 failed" in out),
          (0, True))


# --------------------------------------------------------------------------
# No caller-supplied text interpolated into a run: script
# --------------------------------------------------------------------------
UNSAFE = re.compile(r"\$\{\{\s*(inputs\.|github\.event\.|github\.head_ref|steps\.[^}]*outputs|needs\.[^}]*outputs)")


def test_no_interpolation():
    print("Run blocks take caller text through env")
    files = [os.path.join(WF, f) for f in ("sf-promote-label.yml", "sf-ops-redeploy-feature.yml",
                                           "sf-ops-recreate-feature.yml", "sf-ops-rollback.yml")]
    files += [os.path.join(ACT, a, "action.yml") for a in ("merge-pr", "status", "jira", "notify")]
    for f in files:
        with open(f, encoding="utf-8") as fh:
            d = yaml.safe_load(fh)
        steps = [s for j in (d.get("jobs") or {}).values() for s in j.get("steps", [])]
        steps += (d.get("runs") or {}).get("steps", [])
        bad = [s.get("name") or s.get("id") for s in steps if "run" in s and UNSAFE.search(s["run"])]
        check(f"{os.path.relpath(f, ROOT)}: no ${{{{ }}}} in run scripts", bad, [])


def main():
    print("Promotion & ops suite")
    for fn in (test_recreate, test_rollback, test_redeploy, test_prepare, test_record_why,
               test_guard_and_merge, test_jira, test_no_interpolation):
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
