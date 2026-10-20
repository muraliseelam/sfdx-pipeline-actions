#!/usr/bin/env bash
#
# setup-branch-protection.sh — codify REQ-002 / REQ-004 branch protection.
#
# Reads the environment chain from sf-pipeline/pipeline.config.yml and makes
# EVERY pipeline branch pull-request-only, so nobody with write access can push
# straight to an environment branch or to production and skip its deploy and
# guards:
#
#   * ENTRY branch (environments[0].branch, e.g. int)
#       - Pull request required, >= REQUIRED_REVIEWS approvals (default 1)
#       - Review from Code Owners (REQUIRE_CODE_OWNER_REVIEWS) so a PR touching
#         a path in .github/CODEOWNERS — the caller workflows, the pipeline
#         config — needs their approval
#       - Required statuses (commit statuses the pipeline writes; the jobs' own
#         check runs are named "<caller job> / <job>", so they cannot be
#         required by a fixed name):
#           pr-validation   PR Validation, on the PR head (approval-gated entry)
#           deploy-<entry>  written only by a real deploy, just before the
#                           pipeline merges — so the merge button cannot land a
#                           change the org never received
#       - Stale reviews dismissed on new commits, conversation resolution
#
#   * LATER STAGES (qa, stage, ...)
#       - Pull request required with 0 approvals: the pipeline opens the env PR
#         and merges it with the workflow token right after the deploy,
#         unattended. The change was reviewed (and code-owner reviewed) on entry.
#       - Required status: deploy-<env>, so an env PR merges only once that
#         exact commit has really been deployed to that org
#
#   * PRODUCTION branch (production.branch, e.g. main)
#       - Pull request required, with the entry branch's review settings: the
#         production GitHub Environments trust this branch, so what lands on it
#         (including the caller workflows) must be reviewed
#       - No required status: the release train writes none before it merges
#         the release PR. With REQUIRED_REVIEWS >= 1 the release PR therefore
#         needs an Approve review — the prod-approved label alone is not enough
#         for the post-deploy merge
#
#   * Every branch: no force-push, no deletion.
#
# How this fits the pipeline's own writes:
#   - deploy.yml / sf-promote-label.yml merge with GITHUB_TOKEN after writing
#     deploy-<env> on the PR head.
#   - Every required status is pinned to the GitHub Actions app
#     (STATUS_APP_ID, 15368 on github.com): the pipeline writes them with
#     GITHUB_TOKEN, so a status a person POSTs with their own token can't
#     satisfy branch protection. STATUS_APP_ID="" leaves them unpinned
#     (GitHub Enterprise Server: use your instance's GitHub Actions app id).
#   - The post-release realign (sync-branches) snapshots, lifts and restores
#     this protection with PIPELINE_PAT, app pins included.
#   - Sync Env Branches pushes a merge with PIPELINE_PAT: an admin token gets
#     past PR-only branches only while ENFORCE_ADMINS=false. Otherwise it opens
#     a sync PR for an admin to merge.
#
# WORKING ALONE: GitHub forbids approving your own PR, so a solo maintainer runs
#       REQUIRED_REVIEWS=0 bash .../setup-branch-protection.sh
#   approves with the `approved` label and authorizes releases with
#   `prod-approved`. REQUIRE_CODE_OWNER_REVIEWS follows
#   REQUIRED_REVIEWS by default (true for >= 1, false for 0), because a code
#   owner's review is still a review someone else has to give; set it to true
#   with REQUIRED_REVIEWS=0 to require review only for code-owned paths.
#
# ADMIN ENFORCEMENT (REQ-002 "no direct commits", even for admins):
#   enforce_admins=false by default, so a repo admin (and an admin PIPELINE_PAT)
#   keeps an escape hatch. For a real multi-developer team, run with
#   ENFORCE_ADMINS=true to forbid direct commits for EVERYONE.
#
# Requirements: gh CLI authenticated with admin on the repo; python3. Run it
# from the root of the consuming repo, so it reads that repo's config.
#
# Usage:
#   [REPO=owner/repo] [REQUIRED_REVIEWS=1] [ENFORCE_ADMINS=false] \
#     [REQUIRE_CODE_OWNER_REVIEWS=true|false] [STATUS_APP_ID=15368] [DRY_RUN=false] \
#     bash /path/to/sfdx-pipeline-actions/sf-pipeline/scripts/setup-branch-protection.sh
#
# DRY_RUN=true prints each branch's protection payload and changes nothing.
#
set -euo pipefail

REPO="${REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
REQUIRED_REVIEWS="${REQUIRED_REVIEWS:-1}"
ENFORCE_ADMINS="${ENFORCE_ADMINS:-false}"
if [ "$REQUIRED_REVIEWS" = "0" ]; then CODE_OWNER_DEFAULT=false; else CODE_OWNER_DEFAULT=true; fi
REQUIRE_CODE_OWNER_REVIEWS="${REQUIRE_CODE_OWNER_REVIEWS:-$CODE_OWNER_DEFAULT}"
DRY_RUN="${DRY_RUN:-false}"
STATUS_APP_ID="${STATUS_APP_ID-15368}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # sf-pipeline/
PLAN="python3 ${HERE}/scripts/plan.py"

case "$REQUIRED_REVIEWS" in
  [0-6]) ;;
  *) echo "ERROR: REQUIRED_REVIEWS must be 0-6 (got '$REQUIRED_REVIEWS')" >&2; exit 2 ;;
esac
for v in ENFORCE_ADMINS REQUIRE_CODE_OWNER_REVIEWS DRY_RUN; do
  case "${!v}" in
    true|false) ;;
    *) echo "ERROR: $v must be true or false (got '${!v}')" >&2; exit 2 ;;
  esac
done
case "$STATUS_APP_ID" in
  ""|*[!0-9]*) [ -z "$STATUS_APP_ID" ] || { echo "ERROR: STATUS_APP_ID must be a number or empty (got '$STATUS_APP_ID')" >&2; exit 2; } ;;
esac

# Resolve the chain + prod branch from the pipeline config.
# (Read `plan.py outputs` once, then pull individual values with sed. The chain
# comes from the auto_json array, parsed via `python3 -c` + an env var so we
# never mix a data pipe with a heredoc program on the same stdin. `tr -d '\r'`:
# a Windows python3 ends its lines with CRLF.)
CFG_OUT="$($PLAN outputs 2>/dev/null | tr -d '\r')"
ENTRY="$(printf '%s\n' "$CFG_OUT"  | sed -n 's/^[[:space:]]*entry_branch[[:space:]]*=[[:space:]]*//p')"
PROD="$(printf '%s\n' "$CFG_OUT"   | sed -n 's/^[[:space:]]*prod_branch[[:space:]]*=[[:space:]]*//p')"
AUTO_JSON="$(printf '%s\n' "$CFG_OUT" | sed -n 's/^[[:space:]]*auto_json[[:space:]]*=[[:space:]]*//p')"
# One line per environment: <branch> <TAB> <name> <TAB> <gate>, in chain order.
CHAIN="$(AUTO="$AUTO_JSON" python3 -c '
import json, os
for e in json.loads(os.environ.get("AUTO") or "[]"):
    if e.get("branch"):
        print("\t".join((str(e["branch"]), str(e["name"]), str(e.get("gate") or ""))))
' | tr -d '\r')"

if [ -z "$ENTRY" ] || [ -z "$CHAIN" ]; then
  echo "ERROR: could not resolve the environment chain from sf-pipeline/pipeline.config.yml" >&2
  echo "       (run this from the root of the consuming repo)" >&2
  exit 1
fi

# protection_body <contexts,csv> <reviews> <code_owners> <dismiss_stale> <conversation>
# Emits the PUT payload. Every branch is pull-request-only; only the review
# settings and the required statuses differ.
protection_body() {
  CONTEXTS="$1" REVIEWS="$2" CODE_OWNERS="$3" DISMISS="$4" CONVERSATION="$5" \
  ADMINS="$ENFORCE_ADMINS" APP="$STATUS_APP_ID" python3 -c '
import json, os
e = os.environ
contexts = [c for c in e["CONTEXTS"].split(",") if c]
pin = {"app_id": int(e["APP"])} if e["APP"] else {}
checks = [dict({"context": c}, **pin) for c in contexts]
print(json.dumps({
    "required_status_checks": {"strict": False, "checks": checks} if checks else None,
    "enforce_admins": e["ADMINS"] == "true",
    "required_pull_request_reviews": {
        "dismiss_stale_reviews": e["DISMISS"] == "true",
        "require_code_owner_reviews": e["CODE_OWNERS"] == "true",
        "required_approving_review_count": int(e["REVIEWS"]),
    },
    "restrictions": None,
    "allow_force_pushes": False,
    "allow_deletions": False,
    "required_linear_history": False,
    "required_conversation_resolution": e["CONVERSATION"] == "true",
}))'
}

apply_protection() {   # <branch> <payload>
  local branch="$1" body="$2"
  if [ "$DRY_RUN" = "true" ]; then
    echo "  -> $branch (dry run): $body"
    return 0
  fi
  printf '%s\n' "$body" | gh api -X PUT "repos/${REPO}/branches/${branch}/protection" --input - \
    --jq '"  -> '"$branch"': pr-only reviews=\(.required_pull_request_reviews.required_approving_review_count) code-owners=\(.required_pull_request_reviews.require_code_owner_reviews) checks=\([.required_status_checks.checks[]? | "\(.context)@\(.app_id // "any")"]) force=\(.allow_force_pushes.enabled) admins=\(.enforce_admins.enabled)"'
}

echo "Repo:           $REPO"
echo "Reviews:        ${REQUIRED_REVIEWS} approval(s) on the entry and production branches, code-owner reviews: ${REQUIRE_CODE_OWNER_REVIEWS}"
echo "Enforce admins: $ENFORCE_ADMINS"
[ "$DRY_RUN" = "true" ] && echo "DRY RUN — nothing will be changed."
echo

ENV_BRANCHES=" "
echo "Applying environment-branch protection (every branch PR-only)..."
while IFS=$'\t' read -r branch name gate; do
  [ -n "$branch" ] || continue
  ENV_BRANCHES="${ENV_BRANCHES}${branch} "
  if [ "$branch" = "$ENTRY" ]; then
    # pr-validation is written only for an approval-gated stage.
    checks="deploy-${name}"
    [ "$gate" = "approval" ] && checks="pr-validation,${checks}"
    if [ "$gate" = "auto" ]; then
      # gate: auto deploys and merges every push with no approval, so a required
      # review would only make the pipeline's own merge fail after the deploy.
      echo "  $branch  entry '$name'  (gate auto: pipeline-merged, reviews 0, checks: ${checks})"
      apply_protection "$branch" "$(protection_body "$checks" 0 false true true)"
    else
      echo "  $branch  entry '$name'  (reviews ${REQUIRED_REVIEWS}, checks: ${checks})"
      apply_protection "$branch" "$(protection_body "$checks" "$REQUIRED_REVIEWS" "$REQUIRE_CODE_OWNER_REVIEWS" true true)"
    fi
  else
    echo "  $branch  stage '$name'  (pipeline-merged, checks: deploy-${name})"
    apply_protection "$branch" "$(protection_body "deploy-${name}" 0 false false false)"
  fi
done <<< "$CHAIN"

if [ -n "$PROD" ] && [[ "$ENV_BRANCHES" != *" $PROD "* ]]; then
  echo
  echo "Applying production-branch protection..."
  echo "  $PROD  production  (reviews ${REQUIRED_REVIEWS}, no required status)"
  apply_protection "$PROD" "$(protection_body "" "$REQUIRED_REVIEWS" "$REQUIRE_CODE_OWNER_REVIEWS" true false)"
fi

echo
if [ "$DRY_RUN" = "true" ]; then
  echo "Dry run complete. Re-run without DRY_RUN=true to apply."
else
  echo "Done. Branch protection applied."
fi
