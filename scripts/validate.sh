#!/usr/bin/env bash
#
# validate.sh — static checks for this repository.
#
# Everything here is cheap and offline. It catches the failure modes that would
# otherwise only show up at runtime, inside somebody else's repository.
#
#   1. every YAML file parses
#   2. every shell script is syntactically valid
#   3. every Python script compiles
#   4. every internal `uses:` points at a path that actually exists
#   5. no reusable workflow calls sf-pipeline/scripts/ or sf-setup/ directly
#   6. internal references all use the same owner/repo and the same ref
#   7-10. portability suite, example consumer vs example config, doc links,
#         an example caller for every reusable workflow
#  11. every composite-action output a workflow or action reads is declared
#  12. branch protection leaves no pipeline branch open
#  12b. the release train ships only proven content, never behind production
#  13. the example CODEOWNERS covers the caller workflows and the config
#
# Usage: ./scripts/validate.sh

set -uo pipefail

cd "$(dirname "$0")/.." || exit 1

FAIL=0
pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=1; }
head2() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
head2 "1. YAML parses"
# ---------------------------------------------------------------------------
YAML_FILES=$(find .github sf-pipeline examples -name '*.yml' -o -name '*.yaml' 2>/dev/null | sort)
if ! python3 -c 'import yaml' 2>/dev/null; then
  echo "  (skipped — PyYAML not installed: pip install pyyaml)"
else
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if ERR=$(python3 -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1]))' "$f" 2>&1); then
      :
    else
      fail "$f — ${ERR##*$'\n'}"
    fi
  done <<< "$YAML_FILES"
  [[ $FAIL -eq 0 ]] && pass "$(wc -l <<< "$YAML_FILES" | tr -d ' ') YAML files parse"
fi

# ---------------------------------------------------------------------------
head2 "2. Shell syntax"
# ---------------------------------------------------------------------------
SH_COUNT=0
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  SH_COUNT=$((SH_COUNT + 1))
  bash -n "$f" 2>/dev/null || fail "$f has a shell syntax error"
done <<< "$(find . -name '*.sh' -not -path './.git/*' | sort)"
pass "${SH_COUNT} shell scripts are syntactically valid"

# ---------------------------------------------------------------------------
head2 "3. Python compiles"
# ---------------------------------------------------------------------------
PY_COUNT=0
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  PY_COUNT=$((PY_COUNT + 1))
  python3 -m py_compile "$f" 2>/dev/null || fail "$f does not compile"
done <<< "$(find . -name '*.py' -not -path './.git/*' | sort)"
find . -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null
pass "${PY_COUNT} Python scripts compile"

# ---------------------------------------------------------------------------
head2 "4. Internal references resolve to real paths"
# ---------------------------------------------------------------------------
SELF=$(grep -rhoE '[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(\.github/workflows|sf-pipeline)/[^@]+@[A-Za-z0-9._-]+' \
         --include='*.yml' .github sf-pipeline 2>/dev/null | sort -u)

MISSING=0
TOTAL=0
while IFS= read -r ref; do
  [[ -z "$ref" ]] && continue
  TOTAL=$((TOTAL + 1))
  # strip owner/repo and @ref, leaving the in-repo path
  PATH_PART="${ref%@*}"
  PATH_PART="${PATH_PART#*/}"      # drop owner
  PATH_PART="${PATH_PART#*/}"      # drop repo
  if [[ "$PATH_PART" == .github/workflows/* ]]; then
    [[ -f "$PATH_PART" ]] || { fail "missing workflow: $PATH_PART"; MISSING=1; }
  else
    [[ -f "$PATH_PART/action.yml" ]] || { fail "missing action: $PATH_PART/action.yml"; MISSING=1; }
  fi
done <<< "$SELF"
[[ $MISSING -eq 0 ]] && pass "${TOTAL} distinct internal references all resolve"

# ---------------------------------------------------------------------------
head2 "5. Reusable workflows do not call scripts/ directly"
# ---------------------------------------------------------------------------
# A reusable workflow executes in the CONSUMER's checkout, where this repo's
# sf-pipeline/scripts/ and sf-setup/ do not exist. Scripts must be reached via a
# composite action using $GITHUB_ACTION_PATH (or an explicit checkout of this
# repo into its own path). Any relative path to them is a leak, on a `run:` line
# or inside a multi-line run block; comments are ignored.
Q="'"
LEAK=""
for f in .github/workflows/*.yml; do
  grep -q 'workflow_call' "$f" || continue
  HIT=$(grep -nE "(^|[[:space:]\"${Q}=(])(\./)?sf-(pipeline/scripts|setup)/" "$f" 2>/dev/null \
          | grep -vE '^[0-9]+:[[:space:]]*#' | sed "s|^|$f:|" || true)
  [[ -n "$HIT" ]] && LEAK="${LEAK}${HIT}"$'\n'
done
LEAK="${LEAK%$'\n'}"
if [[ -n "$LEAK" ]]; then
  while IFS= read -r l; do fail "direct script call: $l"; done <<< "$LEAK"
else
  pass "no reusable workflow calls sf-pipeline/scripts/ or sf-setup/ directly"
fi

# ---------------------------------------------------------------------------
head2 "6. Internal references are consistent"
# ---------------------------------------------------------------------------
OWNERS=$(sed -E 's#/(\.github/workflows|sf-pipeline)/.*##' <<< "$SELF" | sort -u)
REFS=$(sed -E 's|.*@||' <<< "$SELF" | sort -u)

if [[ $(wc -l <<< "$OWNERS" | tr -d ' ') -gt 1 ]]; then
  fail "mixed owner/repo in internal references:"; echo "$OWNERS" | sed 's/^/      /'
else
  pass "all internal references use ${OWNERS}"
fi

if [[ $(wc -l <<< "$REFS" | tr -d ' ') -gt 1 ]]; then
  fail "mixed refs in internal references:"; echo "$REFS" | sed 's/^/      /'
else
  pass "all internal references pin @${REFS}"
fi

# ---------------------------------------------------------------------------
head2 "7. Environment-name portability"
# The whole value proposition is that environment names live only in the config.
# This suite drives plan.py with non-standard names and branch != name.
# ---------------------------------------------------------------------------
if [[ -f tests/test_config_matrix.py ]]; then
  if OUT=$(python3 tests/test_config_matrix.py 2>&1); then
    pass "$(grep -oE 'All [0-9]+ checks passed' <<< "$OUT" || echo 'portability suite passed')"
  else
    fail "environment-name portability suite failed:"
    grep -E '✗' <<< "$OUT" | sed 's/^/    /'
  fi
else
  fail "tests/test_config_matrix.py is missing"
fi

# ---------------------------------------------------------------------------
head2 "7b. Deploy engine scripts"
# What reaches the org and what the report says: skipped deletions, the job id,
# the coverage gate. Offline — no Salesforce org involved.
# ---------------------------------------------------------------------------
if [[ -f tests/test_deploy_engine.py ]]; then
  if OUT=$(python3 tests/test_deploy_engine.py 2>&1); then
    pass "$(grep -oE 'All [0-9]+ checks passed' <<< "$OUT" || echo 'deploy engine suite passed')"
  else
    fail "deploy engine suite failed:"
    grep -E '✗' <<< "$OUT" | sed 's/^/    /'
  fi
else
  fail "tests/test_deploy_engine.py is missing"
fi

# ---------------------------------------------------------------------------
head2 "8. Example consumer matches the example config"
# A new user copies BOTH of these. If pr-validation.yml's branch filter does not
# match environments[0].branch, their PRs silently never trigger anything —
# the single most confusing possible first-run failure.
# ---------------------------------------------------------------------------
ENTRY=$(python3 -c '
import sys, yaml
c = yaml.safe_load(open("sf-pipeline/pipeline.config.example.yml"))
e = (c.get("environments") or [])
if not e: sys.exit("no environments in example config")
print(e[0].get("branch") or e[0]["name"])
')
FILTER=$(python3 -c '
import yaml
w = yaml.safe_load(open("examples/consumer/.github/workflows/pr-validation.yml"))
# PyYAML parses the bare key `on` as the boolean True
k = "on" if "on" in w else True
print(" ".join(w[k]["pull_request"]["branches"]))
')
if grep -qw -- "$ENTRY" <<< "$FILTER"; then
  pass "example pr-validation.yml triggers on the entry branch '${ENTRY}' (filter: ${FILTER})"
else
  fail "example pr-validation.yml filters on '${FILTER}' but the example config's entry branch is '${ENTRY}'"
fi

PREFIX=$(python3 -c '
import yaml
c = yaml.safe_load(open("sf-pipeline/pipeline.config.example.yml"))
print((c.get("production") or {}).get("release_branch_prefix", "release/"))
')
if grep -q -- "${PREFIX}" <<< "$FILTER"; then
  pass "example pr-validation.yml also validates '${PREFIX}**' branches"
else
  fail "example pr-validation.yml never fires for '${PREFIX}**', so plan.py's release-branch path is unreachable"
fi

# ---------------------------------------------------------------------------
head2 "9. Documentation links resolve"
# ---------------------------------------------------------------------------
if OUT=$(python3 scripts/check-links.py 2>&1); then
  pass "all relative markdown links and anchors resolve ($OUT)"
else
  fail "broken documentation links:"; echo "$OUT" | sed 's/^/    /'
fi

# ---------------------------------------------------------------------------
head2 "10. Every reusable workflow has an example caller"
# A consumer can only run what they have a caller for. A reusable workflow with
# no example is effectively unreachable, and the docs end up telling people to
# run something that is not in their repo.
# ---------------------------------------------------------------------------
MISSING=""
for f in .github/workflows/*.yml; do
  b=$(basename "$f" .yml)
  grep -q 'workflow_call' "$f" || continue
  grep -rqs "workflows/${b}.yml@" examples/consumer/.github/workflows/ \
    || MISSING="${MISSING} ${b}"
done
if [[ -z "$MISSING" ]]; then
  pass "all reusable workflows have an example caller"
else
  fail "reusable workflows with no example caller:${MISSING}"
fi

# ---------------------------------------------------------------------------
head2 "11. Composite-action outputs are declared"
# A composite action only exposes what its action.yml `outputs:` declares; any
# other `steps.<id>.outputs.<x>` silently reads ''. actionlint cannot see these
# actions (they are referenced by full slug, as a remote action), so plan.py
# once emitted r_require_envs, plan/action.yml never declared it, and the
# ordering guard got an empty list. Checked here:
#   * every key plan.py emit()s is declared in plan/action.yml, and every
#     plan output is wired to a key plan.py really emits;
#   * every steps.<id>.outputs.<x> in a reusable workflow or composite action
#     names a real step and, when that step uses one of this repo's composite
#     actions, an output that action declares.
# ---------------------------------------------------------------------------
if ! python3 -c 'import yaml' 2>/dev/null; then
  echo "  (skipped — PyYAML not installed: pip install pyyaml)"
elif OUT=$(python3 - <<'PY' 2>&1
import ast, glob, os, re, sys, yaml

STEP_REF = re.compile(r"\bsteps\.([A-Za-z0-9_-]+)\.outputs\.([A-Za-z0-9_-]+)")
LOCAL = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(sf-pipeline/[^@\s]+)@")
problems, refs = [], 0
cache = {}

def load(path):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}

def declared(action_dir):
    if action_dir not in cache:
        cache[action_dir] = set((load(os.path.join(action_dir, "action.yml")).get("outputs") or {}))
    return cache[action_dir]

def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from strings(v)

def check_scope(where, steps, scope):
    global refs
    uses = {s["id"]: s.get("uses") or "" for s in steps or [] if isinstance(s, dict) and s.get("id")}
    for text in strings(scope):
        for sid, out in STEP_REF.findall(text):
            refs += 1
            if sid not in uses:
                problems.append(f"{where}: steps.{sid}.outputs.{out} — no step has id '{sid}'")
                continue
            m = LOCAL.match(uses[sid])
            if not m or not os.path.isfile(os.path.join(m.group(1), "action.yml")):
                continue   # a run: step or a third-party action — outputs are dynamic
            if out not in declared(m.group(1)):
                problems.append(f"{where}: steps.{sid}.outputs.{out} — {m.group(1)}/action.yml "
                                f"declares no output '{out}', so this reads ''")

# plan.py <-> plan/action.yml, both ways.
emitted = set()
for node in ast.walk(ast.parse(open("sf-pipeline/scripts/plan.py", encoding="utf-8").read())):
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "emit":
        for arg in node.args:
            if isinstance(arg, ast.Dict):
                emitted |= {k.value for k in arg.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
for key in sorted(emitted - declared("sf-pipeline/plan")):
    problems.append(f"sf-pipeline/scripts/plan.py emits '{key}' but sf-pipeline/plan/action.yml "
                    f"does not declare it, so every workflow reading it gets ''")
# The action's own run block writes a few keys itself (e.g. config_source).
for s in (load("sf-pipeline/plan/action.yml").get("runs") or {}).get("steps") or []:
    emitted |= set(re.findall(r'echo "([a-z_]+)=', str(s.get("run", ""))))
for name, spec in (load("sf-pipeline/plan/action.yml").get("outputs") or {}).items():
    for _, out in STEP_REF.findall(str((spec or {}).get("value", ""))):
        if out not in emitted:
            problems.append(f"sf-pipeline/plan/action.yml output '{name}' reads '{out}', which plan.py never emits")

for wf in sorted(p.replace(os.sep, "/") for p in glob.glob(".github/workflows/*.yml")):
    for job_id, job in ((load(wf).get("jobs")) or {}).items():
        if isinstance(job, dict):
            check_scope(f"{wf} ({job_id})", job.get("steps"), job)
for act in sorted(p.replace(os.sep, "/") for p in glob.glob("sf-pipeline/*/action.yml")):
    doc = load(act)
    check_scope(act, (doc.get("runs") or {}).get("steps"), doc)

if problems:
    print("\n".join(problems))
    sys.exit(1)
print(f"{len(emitted)} plan.py keys declared; {refs} step-output references resolve")
PY
); then
  pass "$OUT"
else
  fail "undeclared composite-action outputs:"; echo "$OUT" | sed 's/^/    /'
fi

# ---------------------------------------------------------------------------
head2 "12. Branch protection leaves no pipeline branch open"
# A branch the protection script leaves without a pull-request requirement is
# one any writer can push to, skipping every deploy and guard. The suite
# dry-runs the script and also checks the post-release realign restores it.
# ---------------------------------------------------------------------------
if [[ -f tests/test_branch_protection.py ]]; then
  if OUT=$(python3 tests/test_branch_protection.py 2>&1); then
    pass "$(grep -oE 'All [0-9]+ checks passed|skipped.*' <<< "$OUT" | head -1 || echo 'branch protection suite passed')"
  else
    fail "branch protection suite failed:"
    grep -E '✗|expected|actual' <<< "$OUT" | sed 's/^/    /'
  fi
else
  fail "tests/test_branch_protection.py is missing"
fi

# ---------------------------------------------------------------------------
head2 "12b. Release train"
# What reaches production: assemble ships only the gathered stories' own work,
# the prod delta is three-dot and a release behind the prod branch never
# deploys, finalize never tags what it could not merge, and the realign
# restores protection per branch. Runs the real steps on local git repos with a
# fake gh — offline.
# ---------------------------------------------------------------------------
if [[ -f tests/test_release_train.py ]]; then
  if OUT=$(python3 tests/test_release_train.py 2>&1); then
    pass "$(grep -oE 'All [0-9]+ checks passed|skipped.*' <<< "$OUT" | head -1 || echo 'release train suite passed')"
  else
    fail "release train suite failed:"
    grep -E '✗|expected|actual|Error' <<< "$OUT" | sed 's/^/    /'
  fi
else
  fail "tests/test_release_train.py is missing"
fi

# ---------------------------------------------------------------------------
head2 "13. Example CODEOWNERS covers the pipeline wiring"
# A pull request that edits a caller workflow or pipeline.config.yml changes how
# every deploy runs. The example CODEOWNERS a consumer copies must name an owner
# for both, or "Require review from Code Owners" protects nothing.
# ---------------------------------------------------------------------------
if OUT=$(python3 - <<'PY' 2>&1
import fnmatch, sys
PATHS = [".github/workflows/pr-validation.yml", ".github/CODEOWNERS", "sf-pipeline/pipeline.config.yml"]
try:
    lines = open("examples/consumer/.github/CODEOWNERS", encoding="utf-8").read().splitlines()
except FileNotFoundError:
    sys.exit("examples/consumer/.github/CODEOWNERS is missing")
rules = [l.split() for l in lines if l.strip() and not l.lstrip().startswith("#")]

def covers(pattern, path):
    p = pattern.lstrip("/")
    if p in ("*", "**"):
        return True
    if p.endswith("/"):
        return path.startswith(p)
    return fnmatch.fnmatch(path, p) or path.startswith(p + "/")

bad = []
for path in PATHS:
    last = [r for r in rules if covers(r[0], path)]   # the last matching line wins
    if not last or len(last[-1]) < 2:
        bad.append(path)
if bad:
    sys.exit("no owner for: " + ", ".join(bad))
print("owners cover " + ", ".join(PATHS))
PY
); then
  pass "$OUT"
else
  fail "example CODEOWNERS: $OUT"
fi

# ---------------------------------------------------------------------------
# 14. Personalisation placeholders (informational only)
# ---------------------------------------------------------------------------
# The shipped template legitimately still contains these, so this must never
# fail — it exists so that a personalised copy gets told what it missed.
# Tokens are assembled at run time so this script does not match itself.
echo
echo "14. Personalisation (informational)"
L='<'; R='>'
PH=$(grep -rlE "${L}COPYRIGHT HOLDER${R}|${L}SECURITY CONTACT${R}|${L}CONDUCT CONTACT${R}" \
       . --exclude-dir=.git 2>/dev/null | grep -v '^\./scripts/personalize\.sh$' || true)
SLUG_UPSTREAM='your-org/sfdx-'"pipeline-actions"   # split: see note below
SLUG=$(grep -rlF "$SLUG_UPSTREAM" \
         . --exclude-dir=.git 2>/dev/null | grep -v '^\./scripts/validate\.sh$' || true)
# The upstream slug is assembled from two halves on purpose. Written whole, it
# would be a literal occurrence in this file, so set-repo-owner.sh would rewrite
# it to the new slug and this check would then match every personalised file.
if [[ -z "$PH" && -z "$SLUG" ]]; then
  pass "no unfilled placeholders — this copy is personalised"
else
  printf '  \033[33m•\033[0m template still has placeholders (expected upstream).\n'
  [[ -n "$SLUG"  ]] && printf '      repo slug unset in %s file(s)\n' "$(wc -l <<< "$SLUG" | tr -d ' ')"
  [[ -n "$PH"    ]] && printf '      identity placeholders in %s file(s)\n' "$(wc -l <<< "$PH" | tr -d ' ')"
  printf '      Run ./scripts/personalize.sh before publishing. See PUBLISHING.md.\n'
fi

# ---------------------------------------------------------------------------
echo
if [[ $FAIL -eq 0 ]]; then
  printf '\033[32mAll checks passed.\033[0m\n'
else
  printf '\033[31mSome checks failed.\033[0m\n'
fi
exit $FAIL
