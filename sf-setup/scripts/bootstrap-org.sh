#!/usr/bin/env bash
#
# bootstrap-org.sh — one command to prepare a single Salesforce org for the
# pipeline, minimizing manual clicks. It:
#
#   1. Ensures a JWT keypair exists (generates one, reused across orgs).
#   2. Authenticates to the org as an admin (one-time interactive web login, or
#      a pre-supplied SFDX auth URL / already-authenticated alias for CI).
#   3. Deploys the SF_CICD_Deploy Connected App (cert injected) + permission set
#      — no manual "New Connected App" or certificate upload. Orgs that refuse
#      new Connected Apps (the default since Spring '26) get the same app as an
#      External Client App instead (metadata-eca/), which JWT logs in through
#      the same way.
#   4. Reads back the org-generated Consumer Key automatically (an External
#      Client App gets a generated key, kept in --key-dir for re-runs).
#   5. Creates the integration user + assigns the permission set (idempotent).
#   6. Writes SF_CONSUMER_KEY / SF_USERNAME / SF_INSTANCE_URL / SF_JWT_KEY into
#      the matching GitHub Environment.
#
# The ONLY step that may still need a human is a one-time admin login to the org
# (platform requires an authenticated session to deploy the first app), and — if
# JWT login is refused — pre-authorizing the app for the integration user's
# profile (Setup > Connected Apps > Manage). Everything else is automated.
#
# Usage:
#   bootstrap-org.sh \
#     --org-alias int-admin \
#     --instance-url https://test.salesforce.com \
#     --repo your-org/sfdx-pipeline-example --env int \
#     --int-username cicd@acme.com.int --contact-email devops@acme.com \
#     [--login web|jwt|skip] [--profile "System Administrator"] \
#     [--app-type auto|connected|external] [--key-dir .secrets] [--no-secrets]
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETUP_ROOT="$(cd "$HERE/.." && pwd)"

ORG_ALIAS=""; INSTANCE_URL="https://login.salesforce.com"; REPO=""; ENVN=""
INT_USERNAME=""; CONTACT_EMAIL="devops@example.com"; LOGIN="web"
PROFILE="System Administrator"; PERMSET="SF_CICD_Deploy"; KEY_DIR=".secrets"
PUSH_SECRETS=1; APP_TYPE="auto"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --org-alias)     ORG_ALIAS="$2";     shift 2 ;;
    --instance-url)  INSTANCE_URL="$2";  shift 2 ;;
    --repo)          REPO="$2";          shift 2 ;;
    --env)           ENVN="$2";          shift 2 ;;
    --int-username)  INT_USERNAME="$2";  shift 2 ;;
    --contact-email) CONTACT_EMAIL="$2"; shift 2 ;;
    --login)         LOGIN="$2";         shift 2 ;;
    --profile)       PROFILE="$2";       shift 2 ;;
    --permset)       PERMSET="$2";       shift 2 ;;
    --key-dir)       KEY_DIR="$2";       shift 2 ;;
    --app-type)      APP_TYPE="$2";      shift 2 ;;
    --no-secrets)    PUSH_SECRETS=0;     shift 1 ;;
    *) echo "Unknown arg: $1" >&2; exit 1 ;;
  esac
done

: "${ORG_ALIAS:?--org-alias is required}"
: "${INT_USERNAME:?--int-username is required}"
case "$APP_TYPE" in auto|connected|external) ;; *) echo "Unknown --app-type value: $APP_TYPE" >&2; exit 1 ;; esac

# ---- 1. keypair ------------------------------------------------------------
if [[ ! -f "$KEY_DIR/cert.body" ]]; then
  echo ">> Generating JWT keypair in $KEY_DIR"
  bash "$HERE/gen-jwt-keypair.sh" --out "$KEY_DIR"
fi
CERT_BODY="$(cat "$KEY_DIR/cert.body")"

# ---- 2. auth ---------------------------------------------------------------
case "$LOGIN" in
  web)
    if ! sf org display --target-org "$ORG_ALIAS" >/dev/null 2>&1; then
      echo ">> Opening browser for one-time admin login to '$ORG_ALIAS'"
      sf org login web --alias "$ORG_ALIAS" --instance-url "$INSTANCE_URL"
    else
      echo ">> Reusing existing authenticated session for '$ORG_ALIAS'"
    fi
    ;;
  jwt)
    # For CI: expects SFDX_AUTH_URL in the environment.
    : "${SFDX_AUTH_URL:?--login jwt needs SFDX_AUTH_URL in the environment}"
    printf '%s' "$SFDX_AUTH_URL" > "$KEY_DIR/authurl.txt"
    sf org login sfdx-url --sfdx-url-file "$KEY_DIR/authurl.txt" --alias "$ORG_ALIAS"
    rm -f "$KEY_DIR/authurl.txt"
    ;;
  skip) echo ">> Skipping auth (assuming '$ORG_ALIAS' is already authenticated)" ;;
  *) echo "Unknown --login value: $LOGIN" >&2; exit 1 ;;
esac

# ---- 3. build + deploy metadata (cert injected) ----------------------------
WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
cp -R "$SETUP_ROOT/metadata/." "$WORK/"
APP="$WORK/force-app/main/default/connectedApps/SF_CICD_Deploy.connectedApp-meta.xml"

# Inject cert + contact email. Use a python one-liner to avoid sed escaping woes.
CERT_BODY="$CERT_BODY" CONTACT_EMAIL="$CONTACT_EMAIL" python3 - "$APP" <<'PY'
import os, sys
path = sys.argv[1]
s = open(path).read()
s = s.replace('__CERT_CONTENT__', os.environ['CERT_BODY'])
s = s.replace('__CONTACT_EMAIL__', os.environ['CONTACT_EMAIL'])
open(path, 'w').write(s)
PY

# Run from inside $WORK so the CLI uses this self-contained project as the root.
# Deploying with an absolute --source-dir while cwd is a different SFDX project
# makes the CLI emit an unsafe "../../.." relative path that the Metadata API rejects.
deploy_work() {
  local rc=0
  DEPLOY_OUT="$( cd "$WORK" && sf project deploy start --target-org "$ORG_ALIAS" \
      --source-dir force-app --ignore-conflicts --wait 15 2>&1 )" || rc=$?
  printf '%s\n' "$DEPLOY_OUT"
  return "$rc"
}

USE_ECA=0
[[ "$APP_TYPE" == external ]] && USE_ECA=1
if [[ "$USE_ECA" -eq 0 ]]; then
  echo ">> Deploying Connected App + permission set to '$ORG_ALIAS'"
  if ! deploy_work; then
    if [[ "$APP_TYPE" == auto ]] && grep -qi "create a connected app" <<<"$DEPLOY_OUT"; then
      echo ">> '$ORG_ALIAS' doesn't allow new Connected Apps; deploying it as an External Client App instead"
      USE_ECA=1
    else
      exit 1
    fi
  fi
fi

if [[ "$USE_ECA" -eq 1 ]]; then
  # The org doesn't generate an External Client App's consumer key, so make one,
  # and keep it so a re-run deploys the same key the GitHub secret holds.
  KEY_FILE="$KEY_DIR/eca-consumer-key.$ORG_ALIAS"
  [[ -s "$KEY_FILE" ]] || python3 -c 'import secrets; print("SFCICD" + secrets.token_hex(24))' > "$KEY_FILE"
  CKEY="$(tr -d '\r\n' < "$KEY_FILE")"
  rm -rf "$WORK/force-app/main/default/connectedApps"
  cp -R "$SETUP_ROOT/metadata-eca/force-app/." "$WORK/force-app/"
  CERT_BODY="$CERT_BODY" CONTACT_EMAIL="$CONTACT_EMAIL" CKEY="$CKEY" PROFILE="$PROFILE" \
    python3 - "$WORK/force-app/main/default" <<'PY'
import os, sys
from pathlib import Path
from xml.sax.saxutils import escape
values = {"__CERT_CONTENT__": os.environ["CERT_BODY"], "__CONTACT_EMAIL__": os.environ["CONTACT_EMAIL"],
          "__CONSUMER_KEY__": os.environ["CKEY"], "__PROFILE__": escape(os.environ["PROFILE"])}
for path in Path(sys.argv[1]).glob("ext*/*.xml"):
    s = path.read_text()
    for k, v in values.items():
        s = s.replace(k, v)
    path.write_text(s)
PY
  echo ">> Deploying External Client App + permission set to '$ORG_ALIAS'"
  deploy_work
fi

# ---- 4. read back the generated Consumer Key -------------------------------
if [[ "$USE_ECA" -eq 0 ]]; then
  echo ">> Retrieving generated Consumer Key"
  RET="$(mktemp -d)"
  ( cd "$WORK" && sf project retrieve start --target-org "$ORG_ALIAS" \
      --metadata "ConnectedApp:SF_CICD_Deploy" >/dev/null )
  CKEY="$(grep -o '<consumerKey>[^<]*</consumerKey>' "$APP" | sed 's/<[^>]*>//g' || true)"
  rm -rf "$RET"
  if [[ -z "$CKEY" ]]; then
    echo "!! Could not read consumerKey automatically. Retrieve it from Setup > App Manager > SF CICD Deploy > View, and set SF_CONSUMER_KEY manually." >&2
  fi
fi

# ---- 5. integration user + permission set ----------------------------------
# An External Client App pre-authorizes --profile through its own OAuth policy,
# so only a Connected App needs the permission-set link (an empty label skips it).
APP_LABEL="SF CICD Deploy"; [[ "$USE_ECA" -eq 1 ]] && APP_LABEL=""
bash "$HERE/create-integration-user.sh" \
  --org "$ORG_ALIAS" --username "$INT_USERNAME" --email "$CONTACT_EMAIL" \
  --profile "$PROFILE" --permset "$PERMSET" --app-label "$APP_LABEL"

# ---- 6. push GitHub Environment secrets ------------------------------------
if [[ "$PUSH_SECRETS" -eq 1 && -n "$REPO" && -n "$ENVN" && -n "$CKEY" ]]; then
  bash "$HERE/push-env-secrets.sh" \
    --repo "$REPO" --env "$ENVN" \
    --consumer-key "$CKEY" --username "$INT_USERNAME" \
    --instance-url "$INSTANCE_URL" --key-b64-file "$KEY_DIR/server.key.b64"
else
  echo ">> Skipping GitHub secret push (missing --repo/--env, --no-secrets, or no Consumer Key)."
  [[ -n "$CKEY" ]] && echo "   SF_CONSUMER_KEY = $CKEY"
fi

echo
echo "✅ Bootstrap complete for org '$ORG_ALIAS' (env '${ENVN:-n/a}')."
if [[ "$USE_ECA" -eq 1 ]]; then
  echo "   'SF CICD Deploy' is an External Client App, pre-authorized for profile '$PROFILE'."
  echo "   A JWT login can take a few minutes to start working after the first deploy."
else
  echo "   If a later JWT login is refused, pre-authorize 'SF CICD Deploy' for the"
  echo "   integration user's profile once: Setup > App Manager > SF CICD Deploy >"
  echo "   Manage > Edit Policies (Permitted Users = Admin approved) > add profile."
fi
