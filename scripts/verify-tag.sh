#!/usr/bin/env bash
#
# verify-tag.sh — prove a release tag pins its own internal references.
#
# A tag cut straight from `main` looks fine but is broken: its workflows still
# reference `@main` for every composite action they invoke, so consumers pinning
# that tag are not actually pinned. This script fails loudly on that.
#
# Usage: ./scripts/verify-tag.sh v1.2.0

set -euo pipefail

TAG="${1:-}"

if [[ -z "$TAG" ]]; then
  echo "Usage: $0 <tag>" >&2
  exit 1
fi

cd "$(dirname "$0")/.."

if ! git rev-parse -q --verify "refs/tags/${TAG}" >/dev/null; then
  echo "No such tag: ${TAG}" >&2
  exit 1
fi

echo "Verifying ${TAG} ($(git rev-parse --short "${TAG}^{commit}"))"

REFS=$(
  git ls-tree -r --name-only "$TAG" \
  | grep -E '^(\.github/workflows|sf-pipeline)/.*\.yml$' \
  | while IFS= read -r f; do git show "${TAG}:${f}"; done \
  | grep -oE '[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(\.github/workflows|sf-pipeline)/[^@[:space:]]+@[A-Za-z0-9._-]+' \
  | sed -E 's|.*@||' | sort | uniq -c
)

if [[ -z "$REFS" ]]; then
  echo "  No internal references found — nothing to verify." >&2
  exit 1
fi

echo "$REFS" | sed 's/^/    /'

BAD=$(awk -v t="$TAG" '$2 != t {print $2}' <<< "$REFS")

if [[ -n "$BAD" ]]; then
  echo
  echo "  ✗ ${TAG} contains internal references to: $(tr '\n' ' ' <<< "$BAD")" >&2
  echo "    A release tag must reference only itself. Re-cut it with scripts/cut-release.sh." >&2
  exit 1
fi

echo
echo "  ✓ every internal reference in ${TAG} points at ${TAG}"
