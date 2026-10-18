#!/usr/bin/env bash
#
# setup-branch-protection.sh — codify REQ-002 / REQ-004 branch protection.
#
# Reads the environment branches from sf-pipeline/pipeline.config.yml and applies:
#
#   * ENTRY branch (environments[0].branch, e.g. gh-actions-int)
#       - Pull-request-only: no direct commits
#       - >= 1 approving review (configurable via REQUIRED_REVIEWS)
#       - Require review from Code Owners (REQUIRE_CODE_OWNER_REVIEWS=true by
#         default) so any PR touching a path in .github/CODEOWNERS — e.g. the
#         pipeline config or workflows — needs the platform team's approval
#       - Required status check: `pr-validation`, the commit status PR Validation
#         writes on the PR head (its job check runs are named "validate / <job>",
#         so they cannot be required by a fixed name)
#       - Stale reviews dismissed on new commits
#       - Conversation resolution required
#       - No force-push, no deletion
#
#   * DOWNSTREAM + PROD branches (qa, stage, main)
#       - Guard-rails only: no force-push, no deletion.
#       - NOT pull-request-only, because the pipeline PROMOTES to these branches
#         by fast-forward push from sf-promote.yml AFTER a successful org deploy.
#         Requiring PRs here would break automated promotion.
#
# ADMIN ENFORCEMENT (REQ-002 "no direct commits", even for admins):
#   By default enforce_admins=false so a repo admin keeps an escape hatch (needed
#   when working solo, since GitHub forbids approving your own PR, and so the
#   post-approval auto-merge in sf-int-deploy can complete). To strictly forbid
#   direct commits for EVERYONE (recommended for real multi-developer teams), run:
#       ENFORCE_ADMINS=true bash sf-pipeline/scripts/setup-branch-protection.sh
#
# Requirements: gh CLI authenticated with admin on the repo; python3.
#
# Usage:
#   [REPO=owner/repo] [REQUIRED_REVIEWS=1] [ENFORCE_ADMINS=false] \
#     [REQUIRE_CODE_OWNER_REVIEWS=true] \
#     bash sf-pipeline/scripts/setup-branch-protection.sh
#
set -euo pipefail

REPO="${REPO:-$(gh repo view --json nameWithOwner --jq .nameWithOwner)}"
REQUIRED_REVIEWS="${REQUIRED_REVIEWS:-1}"
ENFORCE_ADMINS="${ENFORCE_ADMINS:-false}"
REQUIRE_CODE_OWNER_REVIEWS="${REQUIRE_CODE_OWNER_REVIEWS:-true}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # sf-pipeline/
PLAN="python3 ${HERE}/scripts/plan.py"

# Resolve entry + downstream + prod branches from the pipeline config.
# (Read `plan.py outputs` once, then pull individual values with sed. Downstream
# branches come from the auto_json array, parsed via `python3 -c` + an env var so
# we never mix a data pipe with a heredoc program on the same stdin.)
CFG_OUT="$($PLAN outputs 2>/dev/null)"
ENTRY="$(printf '%s\n' "$CFG_OUT"  | sed -n 's/^[[:space:]]*entry_branch[[:space:]]*=[[:space:]]*//p')"
PROD="$(printf '%s\n' "$CFG_OUT"   | sed -n 's/^[[:space:]]*prod_branch[[:space:]]*=[[:space:]]*//p')"
AUTO_JSON="$(printf '%s\n' "$CFG_OUT" | sed -n 's/^[[:space:]]*auto_json[[:space:]]*=[[:space:]]*//p')"
DOWNSTREAM="$(AUTO="$AUTO_JSON" ENTRY="$ENTRY" python3 -c '
import json, os
envs = json.loads(os.environ.get("AUTO") or "[]")
entry = os.environ.get("ENTRY", "")
print(",".join(e["branch"] for e in envs if e.get("branch") and e["branch"] != entry))
')"

if [ -z "$ENTRY" ]; then
  echo "ERROR: could not resolve the entry branch from pipeline.config.yml" >&2
  exit 1
fi

echo "Repo:        $REPO"
echo "Entry:       $ENTRY  (PR-only, ${REQUIRED_REVIEWS} review(s), code-owner reviews: ${REQUIRE_CODE_OWNER_REVIEWS}, checks: pr-validation)"
echo "Downstream:  ${DOWNSTREAM:-<none>}  (guard-rails)"
echo "Production:  $PROD  (guard-rails)"
echo "Enforce admins: $ENFORCE_ADMINS"
echo

apply_entry() {
  local branch="$1"
  local body
  body="$(cat <<JSON
{
  "required_status_checks": { "strict": false, "contexts": ["pr-validation"] },
  "enforce_admins": ${ENFORCE_ADMINS},
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": ${REQUIRE_CODE_OWNER_REVIEWS},
    "required_approving_review_count": ${REQUIRED_REVIEWS}
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_linear_history": false,
  "required_conversation_resolution": true
}
JSON
)"
  echo "$body" | gh api -X PUT "repos/${REPO}/branches/${branch}/protection" --input - \
    --jq '"  -> '"$branch"': reviews=\(.required_pull_request_reviews.required_approving_review_count) checks=\(.required_status_checks.contexts) force=\(.allow_force_pushes.enabled) admins=\(.enforce_admins.enabled)"'
}

apply_guardrail() {
  local branch="$1"
  local body
  body="$(cat <<JSON
{
  "required_status_checks": null,
  "enforce_admins": ${ENFORCE_ADMINS},
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
)"
  echo "$body" | gh api -X PUT "repos/${REPO}/branches/${branch}/protection" --input - \
    --jq '"  -> '"$branch"': force=\(.allow_force_pushes.enabled) del=\(.allow_deletions.enabled)"'
}

echo "Applying entry-branch protection..."
apply_entry "$ENTRY"

echo "Applying guard-rails to downstream + production..."
IFS=',' read -r -a DOWN <<< "${DOWNSTREAM}"
for b in "${DOWN[@]}"; do
  [ -n "$b" ] && apply_guardrail "$b"
done
[ -n "$PROD" ] && [ "$PROD" != "$ENTRY" ] && apply_guardrail "$PROD"

echo
echo "Done. Branch protection applied."
