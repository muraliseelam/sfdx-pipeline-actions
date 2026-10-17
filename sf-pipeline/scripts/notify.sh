#!/usr/bin/env bash
#
# notify.sh — post a short Markdown message to a team chat channel.
#
# Supports four providers, auto-detected from whichever credentials are present:
#
#   slack    incoming webhook        CHAT_WEBHOOK_URL=https://hooks.slack.com/...
#   teams    incoming webhook        CHAT_WEBHOOK_URL=https://....webhook.office.com/...
#   generic  any JSON webhook        CHAT_WEBHOOK_URL=https://example.com/hook
#   api      bot token + channel     CHAT_API_URL + CHAT_API_TOKEN + CHAT_CHANNEL_ID
#
# Set CHAT_PROVIDER to force one of: slack | teams | generic | api.
#
# Contract: notifications are best-effort telemetry, never a deployment gate.
# This script ALWAYS exits 0. Every failure is surfaced as a GitHub Actions
# warning annotation so it is visible in the run summary without failing the
# job that called it. A broken chat integration must never block a release.
#
# Usage: notify.sh "message text"

set -uo pipefail

MESSAGE="${1:-}"

warn() {
  echo "::warning title=Chat notification failed::$*"
}

if [[ -z "$MESSAGE" ]]; then
  warn "no message supplied; nothing to send"
  exit 0
fi

WEBHOOK_URL="${CHAT_WEBHOOK_URL:-}"
API_URL="${CHAT_API_URL:-}"
API_TOKEN="${CHAT_API_TOKEN:-}"
CHANNEL_ID="${CHAT_CHANNEL_ID:-}"
PROVIDER="${CHAT_PROVIDER:-auto}"

# ---------------------------------------------------------------------------
# Resolve the provider
# ---------------------------------------------------------------------------
if [[ "$PROVIDER" == "auto" ]]; then
  if [[ -n "$API_TOKEN" && -n "$CHANNEL_ID" && -n "$API_URL" ]]; then
    PROVIDER="api"
  elif [[ "$WEBHOOK_URL" == *"hooks.slack.com"* ]]; then
    PROVIDER="slack"
  elif [[ "$WEBHOOK_URL" == *"webhook.office.com"* || "$WEBHOOK_URL" == *"logic.azure.com"* ]]; then
    PROVIDER="teams"
  elif [[ -n "$WEBHOOK_URL" ]]; then
    PROVIDER="generic"
  else
    # Nothing configured at all. This is a supported state, not an error:
    # the pipeline is expected to run before chat is wired up.
    echo "Chat notifications are not configured; skipping."
    exit 0
  fi
fi

# ---------------------------------------------------------------------------
# Build the request for the resolved provider
# ---------------------------------------------------------------------------
TARGET=""
declare -a HEADERS=()
PAYLOAD=""

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' <<<"$1"
}

ESCAPED="$(json_escape "$MESSAGE")"

case "$PROVIDER" in
  slack)
    [[ -z "$WEBHOOK_URL" ]] && { warn "CHAT_PROVIDER=slack but CHAT_WEBHOOK_URL is empty"; exit 0; }
    TARGET="$WEBHOOK_URL"
    PAYLOAD="{\"text\": ${ESCAPED}}"
    ;;
  teams)
    [[ -z "$WEBHOOK_URL" ]] && { warn "CHAT_PROVIDER=teams but CHAT_WEBHOOK_URL is empty"; exit 0; }
    TARGET="$WEBHOOK_URL"
    PAYLOAD="{\"@type\": \"MessageCard\", \"@context\": \"https://schema.org/extensions\", \"text\": ${ESCAPED}}"
    ;;
  generic)
    [[ -z "$WEBHOOK_URL" ]] && { warn "CHAT_PROVIDER=generic but CHAT_WEBHOOK_URL is empty"; exit 0; }
    TARGET="$WEBHOOK_URL"
    PAYLOAD="{\"text\": ${ESCAPED}, \"markdown\": ${ESCAPED}}"
    ;;
  api)
    if [[ -z "$API_URL" || -z "$API_TOKEN" || -z "$CHANNEL_ID" ]]; then
      warn "CHAT_PROVIDER=api needs CHAT_API_URL, CHAT_API_TOKEN and CHAT_CHANNEL_ID"
      exit 0
    fi
    TARGET="$API_URL"
    HEADERS+=(-H "Authorization: Bearer ${API_TOKEN}")
    PAYLOAD="{\"roomId\": \"${CHANNEL_ID}\", \"channel\": \"${CHANNEL_ID}\", \"markdown\": ${ESCAPED}, \"text\": ${ESCAPED}}"
    ;;
  *)
    warn "unknown CHAT_PROVIDER '${PROVIDER}' (expected slack, teams, generic or api)"
    exit 0
    ;;
esac

# ---------------------------------------------------------------------------
# Send. --max-time caps the damage from a hung endpoint.
# ---------------------------------------------------------------------------
CODE="$(
  curl -sS -o /tmp/chat-notify-response.txt -w '%{http_code}' \
    --max-time 30 \
    -X POST "$TARGET" \
    -H "Content-Type: application/json" \
    ${HEADERS[@]+"${HEADERS[@]}"} \
    -d "$PAYLOAD" 2>/tmp/chat-notify-error.txt
)" || CODE="000"

BODY="$(head -c 500 /tmp/chat-notify-response.txt 2>/dev/null || true)"

case "$CODE" in
  2*)
    echo "Chat notification sent via ${PROVIDER} (HTTP ${CODE})."
    ;;
  000)
    warn "could not reach the ${PROVIDER} endpoint: $(head -c 200 /tmp/chat-notify-error.txt 2>/dev/null)"
    ;;
  401|403)
    warn "${PROVIDER} rejected the credentials (HTTP ${CODE}). The token is likely expired or revoked. ${BODY}"
    ;;
  404)
    warn "${PROVIDER} endpoint not found (HTTP 404). Check the webhook URL or channel id. ${BODY}"
    ;;
  *)
    warn "${PROVIDER} returned HTTP ${CODE}. ${BODY}"
    ;;
esac

# Always succeed: see the contract note at the top of this file.
exit 0
