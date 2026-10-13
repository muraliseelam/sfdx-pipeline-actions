#!/usr/bin/env bash
#
# set-repo-owner.sh — repoint every internal reference at a new owner/repo.
#
# The reusable workflows in this repository call this repository's own composite
# actions by their full `owner/repo/path@ref` name. After forking, or when you
# first publish this project under your own namespace, those references must be
# rewritten or your workflows will resolve against the upstream repository.
#
# Usage:
#   ./scripts/set-repo-owner.sh <owner>/<repo>
#
# Example:
#   ./scripts/set-repo-owner.sh acme-corp/salesforce-pipeline

set -euo pipefail

NEW="${1:-}"

if [[ -z "$NEW" || "$NEW" != */* ]]; then
  echo "Usage: $0 <owner>/<repo>" >&2
  exit 1
fi

cd "$(dirname "$0")/.."

# Discover the reference currently in use rather than hard-coding it, so this
# script keeps working after it has been run once.
CURRENT="$(
  grep -rhoE '[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(\.github/workflows|sf-pipeline)/' \
    --include='*.yml' --include='*.yaml' .github sf-pipeline examples 2>/dev/null \
  | sed -E 's#/(\.github/workflows|sf-pipeline)/$##' | sort | uniq -c | sort -rn | head -1 \
  | awk '{print $2}'
)"

if [[ -z "$CURRENT" ]]; then
  echo "Could not determine the current owner/repo reference." >&2
  exit 1
fi

if [[ "$CURRENT" == "$NEW" ]]; then
  echo "Already set to ${NEW}; nothing to do."
  exit 0
fi

echo "Rewriting ${CURRENT} -> ${NEW}"

FILES=$(grep -rlF "$CURRENT" \
          --include='*.yml' --include='*.yaml' --include='*.md' --include='*.sh' \
          --include='*.drawio' \
          . 2>/dev/null | grep -v '^\./\.git/' || true)

if [[ -z "$FILES" ]]; then
  echo "No files referenced ${CURRENT}."
  exit 0
fi

COUNT=0
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  python3 - "$f" "$CURRENT" "$NEW" <<'PY'
import sys, pathlib
path, old, new = sys.argv[1], sys.argv[2], sys.argv[3]
p = pathlib.Path(path)
p.write_text(p.read_text().replace(old, new))
PY
  COUNT=$((COUNT + 1))
  echo "  ${f}"
done <<< "$FILES"

echo
echo "Updated ${COUNT} file(s)."
if grep -q '\.drawio$' <<< "$FILES"; then
  echo "Diagram sources changed — re-render docs/diagrams/*.drawio to refresh the PNGs."
fi
echo "Run ./scripts/validate.sh to confirm every reference still resolves."
