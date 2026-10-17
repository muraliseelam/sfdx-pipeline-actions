#!/usr/bin/env bash
#
# auth-jwt.sh — authenticate the Salesforce CLI to one org using the JWT Bearer flow.
#
# Reads the following environment variables (injected from the GitHub Environment
# that maps 1:1 to the target org):
#   SF_CONSUMER_KEY  - the connected app's consumer key (client id)
#   SF_USERNAME      - the integration user to authenticate as
#   SF_INSTANCE_URL  - login URL (https://login.salesforce.com or the My Domain URL)
#   SF_JWT_KEY       - base64-encoded contents of the connected app's server.key
#
# Usage: scripts/ci/auth-jwt.sh <org-alias>
set -euo pipefail

ALIAS="${1:?Usage: auth-jwt.sh <org-alias>}"

: "${SF_CONSUMER_KEY:?SF_CONSUMER_KEY is required}"
: "${SF_USERNAME:?SF_USERNAME is required}"
: "${SF_JWT_KEY:?SF_JWT_KEY is required}"
INSTANCE_URL="${SF_INSTANCE_URL:-https://login.salesforce.com}"

KEY_FILE="$(mktemp)"
trap 'rm -f "$KEY_FILE"' EXIT

# The secret is stored base64-encoded so the PEM newlines survive GitHub secrets.
echo "$SF_JWT_KEY" | base64 --decode > "$KEY_FILE"

# Salesforce derives the JWT audience (the `aud` claim) from the instance URL, but
# that inference is unreliable. Set it explicitly:
#   * My Domain URL (…my.salesforce.com, incl. sandboxes at …sandbox.my.salesforce.com)
#     -> use the My Domain URL ITSELF. This is the universally-accepted audience and is
#        REQUIRED by orgs that enforce My Domain login (which reject the generic
#        https://test.salesforce.com / https://login.salesforce.com audiences with
#        "audience is invalid").
#   * generic sandbox login (https://test.salesforce.com or *.sandbox.*) -> test
#   * everything else (production generic login) -> login
INSTANCE_URL="${INSTANCE_URL%/}"   # normalize: drop any trailing slash
case "$INSTANCE_URL" in
  *.my.salesforce.com*)                       AUDIENCE="$INSTANCE_URL" ;;
  *test.salesforce.com*|*.sandbox.*)          AUDIENCE="https://test.salesforce.com" ;;
  *)                                          AUDIENCE="https://login.salesforce.com" ;;
esac
export SFDX_AUDIENCE_URL="$AUDIENCE"
export SF_AUDIENCE_URL="$AUDIENCE"

echo "::group::Authenticating '$ALIAS' ($SF_USERNAME) at $INSTANCE_URL (aud=$AUDIENCE)"
sf org login jwt \
  --client-id "$SF_CONSUMER_KEY" \
  --jwt-key-file "$KEY_FILE" \
  --username "$SF_USERNAME" \
  --instance-url "$INSTANCE_URL" \
  --alias "$ALIAS" \
  --set-default
echo "::endgroup::"
