#!/usr/bin/env bash
#
# delta.sh — compute the incremental (delta) metadata to deploy using sfdx-git-delta.
#
# The delta is ALWAYS computed against the production baseline (config
# `baseline_branch`) so every environment receives the complete change set of the
# branch relative to Prod.
#
# Environment variables:
#   DELTA_FROM   git ref to diff FROM (default: origin/<baseline_branch from config>)
#   DELTA_TO     git ref to diff TO   (default: HEAD)
#   DELTA_OUTPUT output directory     (default: .sgd)
#
# Produces inside $DELTA_OUTPUT:
#   package/package.xml                 - components to deploy
#   destructiveChanges/destructiveChanges.xml - components to delete
#
# Usage: scripts/ci/delta.sh
set -euo pipefail

# When DELTA_FROM isn't supplied by the caller (the deploy action always sets it),
# fall back to the baseline branch declared in the single config file so nothing
# here is tied to a specific repo's branch names.
_CFG="${PIPELINE_CONFIG:-sf-pipeline/pipeline.config.yml}"
if [ -z "${DELTA_FROM:-}" ] && [ -f "$_CFG" ]; then
  _BASE="$(python3 -c "import yaml; print((yaml.safe_load(open('$_CFG')) or {}).get('baseline_branch','main'))" 2>/dev/null || echo main)"
  DELTA_FROM="origin/${_BASE}"
fi
FROM="${DELTA_FROM:-origin/main}"
TO="${DELTA_TO:-HEAD}"
OUTPUT="${DELTA_OUTPUT:-.sgd}"
API_VERSION="${SF_API_VERSION:-66.0}"

rm -rf "$OUTPUT"
mkdir -p "$OUTPUT"

# Make sure the baseline ref is available even on shallow clones.
git fetch --no-tags --prune origin "${FROM#origin/}" >/dev/null 2>&1 || true

# When DELTA_MERGE_BASE=true, diff against the MERGE-BASE of FROM and TO instead
# of FROM's tip (i.e. three-dot `FROM...TO`). This is required for a diff-vs-
# TARGET-env baseline: the target branch (e.g. origin/qa) has drifted from this
# feature branch via other squash-merged stories, so a plain two-dot diff would
# mark those other stories' components as DELETIONS. Diffing against the common
# ancestor yields ONLY this branch's own additions/changes — exactly what the
# environment is missing (the same result Copado's promotion branch produces).
# For a prod baseline (an ancestor of HEAD) the merge-base equals FROM, so this
# is a no-op there and safe to always apply in target mode.
if [ "${DELTA_MERGE_BASE:-false}" = "true" ]; then
  MB="$(git merge-base "$FROM" "$TO" 2>/dev/null || echo "")"
  if [ -n "$MB" ]; then
    echo "Using merge-base ${MB} of ${FROM}...${TO} as delta FROM (three-dot)."
    FROM="$MB"
  else
    echo "::warning::Could not compute merge-base of ${FROM} and ${TO}; using two-dot diff."
  fi
fi

echo "::group::Computing delta ${FROM}..${TO} (API ${API_VERSION})"
sf sgd source delta \
  --from "$FROM" \
  --to "$TO" \
  --output-dir "$OUTPUT" \
  --generate-delta \
  --api-version "$API_VERSION" \
  --ignore-whitespace
echo "::endgroup::"

PKG="$OUTPUT/package/package.xml"
if [ ! -f "$PKG" ]; then
  echo "No package.xml produced by sfdx-git-delta." >&2
fi

# Expose whether there is anything to deploy for downstream steps.
# Either constructive components (package.xml) OR deletions (destructiveChanges)
# count as deployable changes — otherwise a destructive-only rollback is skipped.
DESTRUCT="$OUTPUT/destructiveChanges/destructiveChanges.xml"
HAS_CHANGES="false"
if [ -f "$PKG" ] && grep -q "<types>" "$PKG"; then
  HAS_CHANGES="true"
fi
if [ -f "$DESTRUCT" ] && grep -q "<types>" "$DESTRUCT"; then
  HAS_CHANGES="true"
fi
echo "Delta has deployable changes: $HAS_CHANGES"
if [ -n "${GITHUB_OUTPUT:-}" ]; then
  echo "has_changes=$HAS_CHANGES" >> "$GITHUB_OUTPUT"
  echo "output_dir=$OUTPUT" >> "$GITHUB_OUTPUT"
fi
