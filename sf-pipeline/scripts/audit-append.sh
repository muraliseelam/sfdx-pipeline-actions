#!/usr/bin/env bash
#
# audit-append.sh — append one audit record (JSON line) to the durable audit-log
# branch (REQ-018). Best-effort and race-safe: it retries on a rejected push by
# re-fetching the branch and re-appending. Never fails the calling deploy.
#
# Requires: a checkout with push rights (GITHUB_TOKEN, contents: write) and
# .deploy/audit-record.json produced by audit.py.
#
# Env:
#   AUDIT_BRANCH   durable branch name (default: audit-log)
#   AUDIT_FILE     path within that branch (default: audit/audit-log.jsonl)
set -uo pipefail

RECORD="${AUDIT_DIR:-.deploy}/audit-record.json"
BRANCH="${AUDIT_BRANCH:-audit-log}"
FILE="${AUDIT_FILE:-audit/audit-log.jsonl}"

if [ ! -f "$RECORD" ]; then
  echo "No audit record to append ($RECORD missing)."; exit 0
fi

LINE="$(python3 -c "import json,sys; print(json.dumps(json.load(open('$RECORD')),separators=(',',':')))")"

git config user.name  "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

WT="$(mktemp -d)"
cleanup() { git worktree remove --force "$WT" >/dev/null 2>&1 || true; rm -rf "$WT"; }
trap cleanup EXIT

for attempt in 1 2 3 4 5; do
  git worktree remove --force "$WT" >/dev/null 2>&1 || true
  rm -rf "$WT"; mkdir -p "$WT"

  if git ls-remote --exit-code --heads origin "$BRANCH" >/dev/null 2>&1; then
    git fetch --no-tags origin "$BRANCH" >/dev/null 2>&1 || true
    git worktree add -f "$WT" "origin/$BRANCH" >/dev/null 2>&1 || { echo "worktree add failed"; continue; }
    git -C "$WT" checkout -B "$BRANCH" "origin/$BRANCH" >/dev/null 2>&1 || true
  else
    # First run: create an orphan branch with no history from the baseline.
    git worktree add -f --detach "$WT" >/dev/null 2>&1 || { echo "worktree add failed"; continue; }
    git -C "$WT" checkout --orphan "$BRANCH" >/dev/null 2>&1
    git -C "$WT" rm -rf . >/dev/null 2>&1 || true
  fi

  mkdir -p "$WT/$(dirname "$FILE")"
  printf '%s\n' "$LINE" >> "$WT/$FILE"
  git -C "$WT" add "$FILE"
  git -C "$WT" commit -q -m "audit: ${AUDIT_TYPE:-Deploy} -> ${AUDIT_ENVIRONMENT:-?} (${AUDIT_STATUS:-?}) @ ${AUDIT_SHA:0:8}" || {
    echo "nothing to commit"; exit 0; }

  if git -C "$WT" push origin "HEAD:refs/heads/$BRANCH" >/dev/null 2>&1; then
    echo "Audit record appended to '$BRANCH' ($FILE)."
    exit 0
  fi
  echo "Push race on '$BRANCH' (attempt $attempt) — retrying…"
  sleep $((attempt * 2))
done

echo "::warning::Could not append to audit-log branch after retries (audit artifact still uploaded)."
exit 0
