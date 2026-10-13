#!/usr/bin/env bash
#
# create-integration-user.sh — run createIntegrationUser.apex against an org,
# substituting the placeholders. Idempotent.
#
# Usage:
#   create-integration-user.sh --org <alias> --username <u> --email <e> \
#       [--alias <a>] [--lastname <n>] [--profile "System Administrator"] \
#       [--permset SF_CICD_Deploy] [--app-label "SF CICD Deploy"]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ORG=""; USERNAME=""; EMAIL=""; UALIAS="cicduser"; LASTNAME="CICD"
PROFILE="System Administrator"; PERMSET="SF_CICD_Deploy"; APPLABEL="SF CICD Deploy"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --org)      ORG="$2";      shift 2 ;;
    --username) USERNAME="$2"; shift 2 ;;
    --email)    EMAIL="$2";    shift 2 ;;
    --alias)    UALIAS="$2";   shift 2 ;;
    --lastname) LASTNAME="$2"; shift 2 ;;
    --profile)  PROFILE="$2";  shift 2 ;;
    --permset)  PERMSET="$2";  shift 2 ;;
    --app-label) APPLABEL="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

: "${ORG:?--org is required}"
: "${USERNAME:?--username is required}"
: "${EMAIL:?--email is required}"

TMP="$(mktemp)"; trap 'rm -f "$TMP"' EXIT
sed -e "s|__USERNAME__|$USERNAME|g" \
    -e "s|__EMAIL__|$EMAIL|g" \
    -e "s|__ALIAS__|${UALIAS:0:8}|g" \
    -e "s|__LASTNAME__|$LASTNAME|g" \
    -e "s|__PROFILE__|$PROFILE|g" \
    -e "s|__PERMSET__|$PERMSET|g" \
    -e "s|__APPLABEL__|$APPLABEL|g" \
    "$HERE/apex/createIntegrationUser.apex" > "$TMP"

echo "::group::Ensuring integration user $USERNAME in org '$ORG'"
sf apex run --target-org "$ORG" --file "$TMP"
echo "::endgroup::"
