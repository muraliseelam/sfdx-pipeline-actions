#!/usr/bin/env bash
#
# init-project.sh — provision a consumer repo from its pipeline.config.yml.
#
# Reads sf-pipeline/pipeline.config.yml and creates everything the pipeline
# needs that GitHub will not create on its own:
#
#   1. every environment branch + the production branch
#   2. every label the pipeline reads or writes
#      (approved, hold, <bypass_label>, deploy:<env>, deployed:<env>)
#   3. every GitHub Environment named in the config
#
# It is IDEMPOTENT and ADDITIVE: it never deletes a branch, label, environment
# or secret, and it never moves a branch that already exists. Re-run it after
# adding or renaming an environment in the config.
#
# Dry run is the DEFAULT. Nothing is created until you pass --apply.
#
# Usage:
#   bash init-project.sh --repo owner/repo [--config PATH] [--apply]
#
# Requires: gh (authenticated), python3.
#
# Token scopes:
#   branches + labels  -> a normal repo-write token is enough
#   environments       -> needs ADMIN on the repo. Without admin the script
#                         still does branches + labels and tells you which
#                         environments to add by hand; it does not fail.
set -uo pipefail

REPO=""; CONFIG="sf-pipeline/pipeline.config.yml"; APPLY=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)   REPO="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --apply)  APPLY=true; shift ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $1" >&2; exit 2 ;;
  esac
done

command -v gh      >/dev/null || { echo "::error::'gh' is not installed. See https://cli.github.com"; exit 1; }
command -v python3 >/dev/null || { echo "::error::'python3' is not installed."; exit 1; }

if [[ -z "$REPO" ]]; then
  REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo "")"
  [[ -n "$REPO" ]] || { echo "::error::--repo owner/name is required (could not infer it)."; exit 1; }
fi
[[ -f "$CONFIG" ]] || { echo "::error::config not found: $CONFIG — run this from the consumer repo root."; exit 1; }

say()  { printf '%s\n' "$*"; }
step() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✔\033[0m %s\n' "$*"; }
skip() { printf '  \033[90m·\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
run()  { if $APPLY; then "$@"; else return 0; fi; }
# "created" when applying, "would create" when not — never claim work we skipped.
did()  { if $APPLY; then printf 'created'; else printf 'would create'; fi; }

# ---------------------------------------------------------------------------
# Parse the config. One python call emits a stable, tab-separated contract so
# the shell never has to parse YAML.
# ---------------------------------------------------------------------------
PARSED="$(python3 - "$CONFIG" <<'PY'
import sys, yaml
cfg = yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}
envs = cfg.get("environments") or []
if not envs:
    sys.exit("pipeline.config.yml defines no `environments`")

prod = cfg.get("production") or {}
names, branches, ghenvs = [], [], []
for e in envs:
    for key in ("name", "branch"):
        if not e.get(key):
            sys.exit(f"environment entry {e!r} is missing `{key}`")
    names.append(str(e["name"]))
    branches.append(str(e["branch"]))
    ghenvs.append(str(e.get("gh_environment") or e["name"]))

dupes = {x for x in names if names.count(x) > 1}
if dupes:
    sys.exit(f"duplicate environment name(s): {', '.join(sorted(dupes))}")

baseline = str(cfg.get("baseline_branch") or prod.get("branch") or "main")
prod_branch = str(prod.get("branch") or "")
for v in (prod.get("deploy_environment"), prod.get("validate_environment")):
    if v:
        ghenvs.append(str(v))

labels = ["approved", "hold", str(cfg.get("bypass_label") or "bypass-quality-gates")]
for n in names:
    labels += [f"deploy:{n}", f"deployed:{n}"]

all_branches = list(dict.fromkeys(branches + ([prod_branch] if prod_branch else [])))
# The baseline is what every other branch is cut FROM, so it must already exist.
# Listing it here would mean "create main from main", which is incoherent; a
# missing baseline is reported separately by the branch phase.
all_branches = [b for b in all_branches if b != baseline]
print("ENTRY\t"    + branches[0])
print("BASELINE\t" + baseline)
print("BRANCHES\t" + " ".join(all_branches))
print("GHENVS\t"   + " ".join(dict.fromkeys(ghenvs)))
print("LABELS\t"   + " ".join(dict.fromkeys(labels)))
print("NAMES\t"    + " ".join(names))
PY
)" || { echo "::error::$PARSED"; exit 1; }

field() { printf '%s\n' "$PARSED" | sed -n "s/^$1"$'\t'"//p"; }
ENTRY="$(field ENTRY)"; BASELINE="$(field BASELINE)"
BRANCHES="$(field BRANCHES)"; GHENVS="$(field GHENVS)"; NAMES="$(field NAMES)"
read -r -a LABELS <<< "$(field LABELS)"

say "Repository   : $REPO"
say "Config       : $CONFIG"
say "Environments : $NAMES"
say "Entry branch : $ENTRY"
say "Baseline     : $BASELINE"
$APPLY || say $'\n\033[33mDRY RUN\033[0m — nothing will be created. Re-run with --apply to make changes.'

# ---------------------------------------------------------------------------
# 1. Branches. Each missing branch is created from the baseline so every
#    environment starts from the same commit.
# ---------------------------------------------------------------------------
step "1/3  Branches"
BASE_SHA="$(gh api "repos/$REPO/git/ref/heads/$BASELINE" --jq .object.sha 2>/dev/null || echo "")"
if [[ -z "$BASE_SHA" ]]; then
  DEF="$(gh repo view "$REPO" --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || echo "")"
  BASE_SHA="$(gh api "repos/$REPO/git/ref/heads/$DEF" --jq .object.sha 2>/dev/null || echo "")"
  if [[ -z "$BASE_SHA" ]]; then
    warn "Baseline branch '$BASELINE' does not exist and the default branch could not be read."
    warn "Push an initial commit first, then re-run."
  else
    warn "Baseline '$BASELINE' does not exist — new branches will start from '$DEF'."
  fi
fi

for b in $BRANCHES; do
  if gh api "repos/$REPO/git/ref/heads/$b" >/dev/null 2>&1; then
    skip "branch $b (exists)"
  elif [[ -z "$BASE_SHA" ]]; then
    warn "branch $b — cannot create, no base commit"
  elif run gh api -X POST "repos/$REPO/git/refs" -f "ref=refs/heads/$b" -f "sha=$BASE_SHA" >/dev/null 2>&1; then
    ok "branch $b ($(did), from ${BASELINE})"
  else
    warn "branch $b — could not create (permissions?)"
  fi
done

# ---------------------------------------------------------------------------
# 2. Labels. deployed:<env> is also created on demand at deploy time, but the
#    deploy:<env> triggers must exist up front or nobody can promote.
# ---------------------------------------------------------------------------
step "2/3  Labels"
colour_for() {
  case "$1" in
    deploy:*)   printf '1d76db' ;;   # blue  — an action you take
    deployed:*) printf '0e8a16' ;;   # green — a state the pipeline stamped
    approved)   printf '0e8a16' ;;
    hold)       printf 'd93f0b' ;;   # red   — holds a change back
    *)          printf 'fbca04' ;;   # amber — bypass / other
  esac
}
desc_for() {
  case "$1" in
    deploy:*)   printf 'Promote this change to %s' "${1#deploy:}" ;;
    deployed:*) printf 'Deployed to %s by the pipeline' "${1#deployed:}" ;;
    approved)   printf 'Approve this PR for deployment' ;;
    hold)       printf 'Hold this change back from the next release' ;;
    *)          printf 'Bypass the quality gates (admins only)' ;;
  esac
}
for l in "${LABELS[@]}"; do
  [[ -z "$l" ]] && continue
  if gh label list --repo "$REPO" --limit 200 --json name -q '.[].name' 2>/dev/null | grep -qxF "$l"; then
    skip "label $l (exists)"
  elif run gh label create "$l" --repo "$REPO" --color "$(colour_for "$l")" \
         --description "$(desc_for "$l")" >/dev/null 2>&1; then
    ok "label $l ($(did))"
  else
    warn "label $l — could not create"
  fi
done

# ---------------------------------------------------------------------------
# 3. GitHub Environments. Creating one needs repo ADMIN; without it we report
#    instead of failing, because branches + labels are still worth doing.
# ---------------------------------------------------------------------------
step "3/3  GitHub Environments"
ADMIN="$(gh api "repos/$REPO" --jq '.permissions.admin // false' 2>/dev/null || echo false)"
if [[ "$ADMIN" != "true" ]]; then
  warn "You do not have admin on $REPO, so Environments cannot be created here."
  warn "Ask an admin to create these under Settings → Environments:"
  for e in $GHENVS; do warn "    $e"; done
else
  for e in $GHENVS; do
    if gh api "repos/$REPO/environments/$e" >/dev/null 2>&1; then
      skip "environment $e (exists)"
    elif run gh api -X PUT "repos/$REPO/environments/$e" >/dev/null 2>&1; then
      ok "environment $e ($(did))"
    else
      warn "environment $e — could not create"
    fi
  done
fi

# ---------------------------------------------------------------------------
# What a human still has to do. These need values only you have.
# ---------------------------------------------------------------------------
PROD_DEPLOY="$(python3 - "$CONFIG" <<'PY'
import sys, yaml
print(((yaml.safe_load(open(sys.argv[1], encoding="utf-8")) or {}).get("production") or {}).get("deploy_environment") or "")
PY
)"

step "Remaining manual steps"
cat <<EOF
  1. Add four secrets to EACH environment (Settings → Environments → <env> → Secrets):
         SF_CONSUMER_KEY   connected-app consumer key
         SF_USERNAME       integration user for that org
         SF_INSTANCE_URL   login / My Domain URL
         SF_JWT_KEY        base64-encoded server.key
     The Org Bootstrap workflow can create the connected app, certificate and
     integration user and write these for you.

  2. Add required reviewers to '${PROD_DEPLOY:-your production environment}'
     ONLY. That approval click is the production gate. Leave every other
     environment ungated — the PR approval is already the gate.

  3. Set the entry branch in .github/workflows/pr-validation.yml:
         on: pull_request: branches: [ $ENTRY ]
     This is the only environment name outside pipeline.config.yml.

  4. Apply branch protection:
         REPO=$REPO bash sf-pipeline/scripts/setup-branch-protection.sh
EOF

if ! $APPLY; then
  printf '\n\033[33mThis was a DRY RUN.\033[0m Re-run with --apply to create the items marked above.\n'
fi
