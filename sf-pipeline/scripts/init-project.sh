#!/usr/bin/env bash
#
# init-project.sh — provision a consumer repo from its pipeline.config.yml.
#
# Reads sf-pipeline/pipeline.config.yml and creates everything the pipeline
# needs that GitHub will not create on its own:
#
#   1. every environment branch + the production branch
#   2. every label the pipeline reads or writes
#      (approved, hold, <bypass_label>, deploy:<env>, deployed:<env>, and
#      prod-approved when the config has a production block)
#   3. every GitHub Environment named in the config, with the production
#      `deploy_environment` and `validate_environment` restricted to the
#      production branch, so no other branch's workflow can use the production
#      secrets (an existing unrestricted one is tightened the same way; its
#      reviewers and wait timer are kept)
#   4. with --allow-actions-prs, lets GitHub Actions open pull requests, which
#      `deploy:<env>` needs when there is no PIPELINE_PAT
#
# It is IDEMPOTENT and ADDITIVE: it never deletes a branch, label, environment
# or secret, and it never moves a branch that already exists. Re-run it after
# adding or renaming an environment in the config.
#
# Dry run is the DEFAULT. Nothing is created until you pass --apply.
#
# Usage:
#   bash init-project.sh --repo owner/repo [--config PATH] [--apply] [--allow-actions-prs]
#
# Requires: gh (authenticated), python3.
#
# Token scopes:
#   branches + labels  -> a normal repo-write token is enough
#   environments and   -> need ADMIN on the repo. Without admin the script
#   Actions settings      still does branches + labels and tells you what to
#                         set by hand; it does not fail.
set -uo pipefail

REPO=""; CONFIG="sf-pipeline/pipeline.config.yml"; APPLY=false; ALLOW_ACTIONS_PRS=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)   REPO="$2"; shift 2 ;;
    --config) CONFIG="$2"; shift 2 ;;
    --apply)  APPLY=true; shift ;;
    --allow-actions-prs) ALLOW_ACTIONS_PRS=true; shift ;;
    -h|--help) sed -n '2,/^set -uo/{/^#/p;}' "$0"; exit 0 ;;
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
chain_envs = set(ghenvs)
prod_envs, shared = [], []
for v in (prod.get("deploy_environment"), prod.get("validate_environment")):
    if v:
        ghenvs.append(str(v))
        # A stage that shares a production environment deploys from PR refs,
        # so that environment cannot be limited to the production branch.
        (shared if str(v) in chain_envs else prod_envs).append(str(v))

labels = ["approved", "hold", str(cfg.get("bypass_label") or "bypass-quality-gates")]
for n in names:
    labels += [f"deploy:{n}", f"deployed:{n}"]
if prod_branch:
    # Release preflight accepts this label as authorization (a solo maintainer
    # can't approve their own release PR), so it has to exist before Part 3.
    labels.append("prod-approved")

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
print("PRODBRANCH\t" + prod_branch)
print("PRODENVS\t" + " ".join(dict.fromkeys(prod_envs)))
print("SHAREDENVS\t" + " ".join(dict.fromkeys(shared)))
PY
)" || { echo "::error::$PARSED"; exit 1; }
PARSED="$(printf '%s\n' "$PARSED" | tr -d '\r')"   # a Windows python3 writes CRLF

field() { printf '%s\n' "$PARSED" | sed -n "s/^$1"$'\t'"//p"; }
ENTRY="$(field ENTRY)"; BASELINE="$(field BASELINE)"
BRANCHES="$(field BRANCHES)"; GHENVS="$(field GHENVS)"; NAMES="$(field NAMES)"
PROD_BRANCH="$(field PRODBRANCH)"; PROD_ENVS="$(field PRODENVS)"; SHARED_ENVS="$(field SHAREDENVS)"
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
step "1/4  Branches"
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
step "2/4  Labels"
colour_for() {
  case "$1" in
    deploy:*)   printf '1d76db' ;;   # blue  — an action you take
    deployed:*) printf '0e8a16' ;;   # green — a state the pipeline stamped
    approved|prod-approved) printf '0e8a16' ;;
    hold)       printf 'd93f0b' ;;   # red   — holds a change back
    *)          printf 'fbca04' ;;   # amber — bypass / other
  esac
}
desc_for() {
  case "$1" in
    deploy:*)   printf 'Promote this change to %s' "${1#deploy:}" ;;
    deployed:*) printf 'Deployed to %s by the pipeline' "${1#deployed:}" ;;
    approved)   printf 'Approve this PR for deployment' ;;
    prod-approved) printf 'Authorize this release PR for production' ;;
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
step "3/4  GitHub Environments"

# The production environments hold the production org's credentials. With no
# deployment branch policy, a workflow pushed to ANY branch can name them and
# read those secrets. Prod Release runs from the production branch (it is
# dispatched there), so that branch is the only one allowed. release/* is
# deliberately NOT allowed: any writer can push a release/ branch.
restrict_to_prod_branch() {   # <environment>
  local e="$1" envjson pol body names others
  envjson="$(gh api "repos/$REPO/environments/$e" 2>/dev/null || echo "")"
  if [[ -z "$envjson" ]]; then
    if $APPLY; then warn "environment $e — not found, so deployments could not be limited to '$PROD_BRANCH'"
    else ok "environment $e — deployments limited to branch '$PROD_BRANCH' (would set)"; fi
    return
  fi
  pol="$(printf '%s' "$envjson" | python3 -c '
import json, sys
p = json.load(sys.stdin).get("deployment_branch_policy") or {}
print("custom" if p.get("custom_branch_policies") else "protected" if p.get("protected_branches") else "any")
' | tr -d '\r')"
  case "$pol" in
    any)
      # PUT the environment's full current state plus the policy, so existing
      # reviewers, prevent_self_review, wait timer and admin bypass are kept.
      body="$(printf '%s' "$envjson" | python3 -c '
import json, sys
d = json.load(sys.stdin)
body = {"deployment_branch_policy": {"protected_branches": False, "custom_branch_policies": True}}
if d.get("can_admins_bypass") is False:   # only carry the non-default value
    body["can_admins_bypass"] = False
for r in d.get("protection_rules") or []:
    if r.get("type") == "wait_timer":
        body["wait_timer"] = int(r.get("wait_timer") or 0)
    elif r.get("type") == "required_reviewers":
        body["prevent_self_review"] = bool(r.get("prevent_self_review"))
        body["reviewers"] = [{"type": x["type"], "id": x["reviewer"]["id"]} for x in r.get("reviewers") or []]
print(json.dumps(body))
')"
      if run gh api -X PUT "repos/$REPO/environments/$e" --input - <<< "$body" >/dev/null 2>&1 \
         && run gh api -X POST "repos/$REPO/environments/$e/deployment-branch-policies" \
              -f name="$PROD_BRANCH" -f type=branch >/dev/null 2>&1; then
        ok "environment $e — deployments limited to branch '$PROD_BRANCH' ($($APPLY && echo set || echo 'would set'))"
      else
        warn "environment $e — could not limit deployments to '$PROD_BRANCH'. Set it by hand:"
        warn "    Settings → Environments → $e → Deployment branches → Selected branches → add '$PROD_BRANCH'"
      fi
      ;;
    custom)
      names="$(gh api "repos/$REPO/environments/$e/deployment-branch-policies" --paginate \
                 --jq '.branch_policies[] | select((.type // "branch") == "branch") | .name' 2>/dev/null | tr -d '\r')"
      others="$(printf '%s\n' "$names" | grep -vxF -- "$PROD_BRANCH" | grep -v '^$' | tr '\n' ' ' || true)"
      if printf '%s\n' "$names" | grep -qxF -- "$PROD_BRANCH"; then
        skip "environment $e — deployments limited to selected branches (has '$PROD_BRANCH')"
      elif run gh api -X POST "repos/$REPO/environments/$e/deployment-branch-policies" \
             -f name="$PROD_BRANCH" -f type=branch >/dev/null 2>&1; then
        ok "environment $e — branch '$PROD_BRANCH' allowed to deploy ($($APPLY && echo added || echo 'would add'))"
      else
        warn "environment $e — could not allow branch '$PROD_BRANCH' to deploy"
      fi
      [[ -n "$others" ]] && warn "environment $e also allows: ${others}— remove any you do not need; each can read the production secrets."
      ;;
    protected)
      warn "environment $e allows every PROTECTED branch to deploy — that includes the env branches."
      warn "    Settings → Environments → $e → Deployment branches → Selected branches → add '$PROD_BRANCH'"
      ;;
  esac
}

ADMIN="$(gh api "repos/$REPO" --jq '.permissions.admin // false' 2>/dev/null || echo false)"
ADMIN="${ADMIN//$'\r'/}"
if [[ "$ADMIN" != "true" ]]; then
  warn "You do not have admin on $REPO, so Environments cannot be created here."
  warn "Ask an admin to create these under Settings → Environments:"
  for e in $GHENVS; do warn "    $e"; done
  if [[ -n "$PROD_ENVS" && -n "$PROD_BRANCH" ]]; then
    warn "and to limit deployments of ${PROD_ENVS% } to the '$PROD_BRANCH' branch"
    warn "(Deployment branches → Selected branches), so no other branch can use the production secrets."
  fi
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
  if [[ -n "$PROD_ENVS" ]]; then
    if [[ -z "$PROD_BRANCH" ]]; then
      warn "production.branch is not set, so ${PROD_ENVS% } cannot be limited to it."
    else
      for e in $PROD_ENVS; do restrict_to_prod_branch "$e"; done
    fi
  fi
fi
for e in $SHARED_ENVS; do
  warn "environment $e is used by a stage AND by production: stages deploy from pull requests, so it"
  warn "    cannot be limited to '$PROD_BRANCH'. Give production its own environments."
done

# ---------------------------------------------------------------------------
# 4. Pull requests opened by Actions. `deploy:<env>` opens the feature→<env> PR
#    with the workflow token unless PIPELINE_PAT is set, and new repositories
#    forbid that. Turning it on also lets a workflow APPROVE a PR, which counts
#    towards required reviews — so it is opt-in, and code-owner reviews matter.
# ---------------------------------------------------------------------------
step "4/4  Pull requests opened by GitHub Actions"
if [[ "$ADMIN" != "true" ]]; then
  warn "Needs admin to check. Without a PIPELINE_PAT, deploy:<env> needs Settings → Actions → General →"
  warn "    Workflow permissions → Allow GitHub Actions to create and approve pull requests."
else
  WF_PERMS="$(gh api "repos/$REPO/actions/permissions/workflow" \
                --jq '"\(.default_workflow_permissions) \(.can_approve_pull_request_reviews)"' 2>/dev/null | tr -d '\r' || echo "")"
  if [[ "${WF_PERMS#* }" == "true" ]]; then
    skip "Actions may create pull requests (already allowed)"
  elif ! $ALLOW_ACTIONS_PRS; then
    warn "Actions may not create pull requests, so deploy:<env> needs a PIPELINE_PAT secret."
    warn "    Or re-run with --allow-actions-prs (it also lets a workflow approve a PR, so keep code-owner reviews on)."
  elif run gh api -X PUT "repos/$REPO/actions/permissions/workflow" \
         -f default_workflow_permissions="$( [[ "${WF_PERMS%% *}" == write ]] && echo write || echo read )" \
         -F can_approve_pull_request_reviews=true >/dev/null 2>&1; then
    ok "Actions may create pull requests ($($APPLY && echo enabled || echo 'would enable'))"
  else
    warn "Could not allow Actions to create pull requests (an organization policy may forbid it). Use PIPELINE_PAT."
  fi
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
     With two or more reviewers, also tick "Prevent self-review" so whoever
     starts a release cannot approve it. Working alone, leave it off — or
     nobody can ever approve your releases.

  3. Set the entry branch in .github/workflows/pr-validation.yml:
         on: pull_request: branches: [ $ENTRY ]
     This is the only environment name outside pipeline.config.yml.

  4. Copy examples/consumer/.github/CODEOWNERS to .github/CODEOWNERS and name
     the people (or team) who own the pipeline wiring in it.

  5. Apply branch protection, from the root of this repo, using your checkout
     of the central pipeline repo:
         REPO=$REPO bash <central checkout>/sf-pipeline/scripts/setup-branch-protection.sh
     Working alone? Prefix it with REQUIRED_REVIEWS=0.
EOF

if ! $APPLY; then
  printf '\n\033[33mThis was a DRY RUN.\033[0m Re-run with --apply to create the items marked above.\n'
fi
