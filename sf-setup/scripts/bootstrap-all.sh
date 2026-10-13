#!/usr/bin/env bash
#
# bootstrap-all.sh — bootstrap every org in one pass by reading a simple table.
#
# Edit orgs.tsv (tab- or space-separated), one org per line:
#   <env>  <org-alias>  <instance-url>  <integration-username>
# Lines starting with # are ignored. Example:
#   int    int-admin    https://test.salesforce.com   cicd@acme.com.int
#   qa     qa-admin     https://test.salesforce.com   cicd@acme.com.qa
#   prod   prod-admin   https://login.salesforce.com  cicd@acme.com
#
# Usage: bootstrap-all.sh --repo owner/repo [--table orgs.tsv] [--login web|jwt|skip] \
#                         [--contact-email you@acme.com]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

REPO=""; TABLE="orgs.tsv"; LOGIN="web"; CONTACT="devops@example.com"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)          REPO="$2";    shift 2 ;;
    --table)         TABLE="$2";   shift 2 ;;
    --login)         LOGIN="$2";   shift 2 ;;
    --contact-email) CONTACT="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done
: "${REPO:?--repo is required}"
[[ -f "$TABLE" ]] || { echo "table not found: $TABLE" >&2; exit 1; }

while read -r ENVN ALIAS URL USERNAME _rest; do
  [[ -z "${ENVN:-}" || "${ENVN:0:1}" == "#" ]] && continue
  echo "==================================================================="
  echo ">> Bootstrapping env=$ENVN org=$ALIAS"
  echo "==================================================================="
  bash "$HERE/bootstrap-org.sh" \
    --org-alias "$ALIAS" --instance-url "$URL" \
    --repo "$REPO" --env "$ENVN" \
    --int-username "$USERNAME" --contact-email "$CONTACT" \
    --login "$LOGIN"
done < "$TABLE"

echo "All orgs processed."
