#!/usr/bin/env bash
#
# bootstrap-from-config.sh — onboard EVERY org for a project by reading the
# single source of truth: the project's sf-pipeline/pipeline.config.yml.
#
# The actual sandbox names + org templates live only in that config, so a new
# project is a one-file change — no orgs.tsv, no per-place edits.
#
# Run it from the PROJECT repo root (so it finds sf-pipeline/pipeline.config.yml),
# pointing at the bootstrap scripts in your checkout of the central actions repo:
#
#   /path/to/central/sf-setup/scripts/bootstrap-from-config.sh \
#     --repo your-org/sfdx-pipeline-example \
#     --contact-email devops@acme.com \
#     [--config sf-pipeline/pipeline.config.yml] [--login web|jwt|skip]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPO=""; CONFIG="sf-pipeline/pipeline.config.yml"; CONTACT="devops@example.com"; LOGIN="web"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)          REPO="$2";    shift 2 ;;
    --config)        CONFIG="$2";  shift 2 ;;
    --contact-email) CONTACT="$2"; shift 2 ;;
    --login)         LOGIN="$2";   shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done
: "${REPO:?--repo is required}"
[[ -f "$CONFIG" ]] || { echo "config not found: $CONFIG (run from the project repo root)" >&2; exit 1; }

ROWS="$(python3 "$HERE/config-orgs.py" "$CONFIG")"
[[ -n "$ROWS" ]] || { echo "no orgs derived from $CONFIG" >&2; exit 1; }

echo "Derived orgs from $CONFIG:"
printf '%s\n' "$ROWS" | awk -F'\t' '{printf "  env=%-14s url=%-55s user=%s\n",$1,$3,$4}'
echo

while IFS=$'\t' read -r ENVN ALIAS URL USERNAME SANDBOX; do
  [[ -z "${ENVN:-}" ]] && continue
  echo "==================================================================="
  echo ">> env=$ENVN  sandbox=${SANDBOX:-n/a}  url=$URL"
  echo "==================================================================="
  bash "$HERE/bootstrap-org.sh" \
    --org-alias "$ALIAS" --instance-url "$URL" \
    --repo "$REPO" --env "$ENVN" \
    --int-username "$USERNAME" --contact-email "$CONTACT" \
    --login "$LOGIN"
done <<< "$ROWS"

echo "All orgs from $CONFIG processed."
