#!/usr/bin/env bash
#
# push-env-secrets.sh — write the 4 JWT secrets into a GitHub Environment so the
# pipeline can authenticate. Removes the manual "paste 4 secrets per org" step.
#
# Requires: gh CLI authenticated (gh auth status) with admin on the repo.
#
# Usage:
#   push-env-secrets.sh --repo owner/repo --env int \
#     --consumer-key <key> --username <user> \
#     --instance-url https://test.salesforce.com --key-b64-file .secrets/server.key.b64
set -euo pipefail

REPO=""; ENVN=""; CKEY=""; USERNAME=""; URL="https://login.salesforce.com"; KEYB64FILE=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)         REPO="$2";       shift 2 ;;
    --env)          ENVN="$2";       shift 2 ;;
    --consumer-key) CKEY="$2";       shift 2 ;;
    --username)     USERNAME="$2";   shift 2 ;;
    --instance-url) URL="$2";        shift 2 ;;
    --key-b64-file) KEYB64FILE="$2"; shift 2 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

: "${REPO:?--repo is required}"
: "${ENVN:?--env is required}"
: "${CKEY:?--consumer-key is required}"
: "${USERNAME:?--username is required}"
: "${KEYB64FILE:?--key-b64-file is required}"
[[ -f "$KEYB64FILE" ]] || { echo "key file not found: $KEYB64FILE" >&2; exit 1; }

# Retry wrapper — GitHub's API occasionally returns transient 5xx/timeout errors
# (e.g. "HTTP 503: No server is currently available"). Retry a few times with
# backoff so a momentary blip doesn't abort a multi-org bootstrap mid-run.
retry() {
  local n=0 max=5 delay=3
  until "$@"; do
    n=$((n+1))
    if [[ $n -ge $max ]]; then
      echo "!! command failed after $max attempts: $*" >&2
      return 1
    fi
    echo "   transient failure (attempt $n/$max); retrying in ${delay}s..." >&2
    sleep "$delay"; delay=$((delay*2))
  done
}

# Create the environment only when it is missing. Secrets need no PUT, and an
# existing environment (e.g. prod, with its reviewers and deployment branch
# policy) must never be rewritten by a bare PUT.
if ! gh api "repos/$REPO/environments/$ENVN" >/dev/null 2>&1; then
  retry gh api -X PUT "repos/$REPO/environments/$ENVN" >/dev/null
fi

retry gh secret set SF_CONSUMER_KEY --repo "$REPO" --env "$ENVN" --body "$CKEY"
retry gh secret set SF_USERNAME     --repo "$REPO" --env "$ENVN" --body "$USERNAME"
retry gh secret set SF_INSTANCE_URL --repo "$REPO" --env "$ENVN" --body "$URL"
retry gh secret set SF_JWT_KEY      --repo "$REPO" --env "$ENVN" --body "$(cat "$KEYB64FILE")"

echo "Secrets set for environment '$ENVN' in $REPO."
