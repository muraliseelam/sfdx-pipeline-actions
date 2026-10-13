#!/usr/bin/env bash
#
# personalize.sh — make this repository yours, in one command.
#
# Three kinds of placeholder ship with the template and every one of them has to
# be replaced before the pipeline works for anybody else:
#
#   1. The repository slug the workflows refer to. The reusable workflows call
#      this repository's own composite actions by full name, so if the slug is
#      wrong they silently resolve against the upstream project.
#   2. The copyright holder in LICENSE.
#   3. The security and conduct contact addresses.
#
# This wraps set-repo-owner.sh (which handles 1) and fills in 2 and 3.
#
# Usage:
#   ./scripts/personalize.sh <owner>/<repo> "<copyright holder>" <contact email>
#
# Example:
#   ./scripts/personalize.sh acme/salesforce-pipeline "Acme Inc." devops@acme.com
#
# Idempotent: re-running with different values updates them again.

set -euo pipefail

REPO="${1:-}"
HOLDER="${2:-}"
CONTACT="${3:-}"

usage() {
  cat >&2 <<'EOF'
Usage: ./scripts/personalize.sh <owner>/<repo> "<copyright holder>" <contact email>

  owner/repo        where YOU will publish this, e.g. acme/salesforce-pipeline
  copyright holder  name for the LICENSE, e.g. "Acme Inc." or "Jane Doe"
  contact email     address for security reports and conduct reports

Example:
  ./scripts/personalize.sh acme/salesforce-pipeline "Acme Inc." devops@acme.com
EOF
  exit 1
}

[[ -z "$REPO" || -z "$HOLDER" || -z "$CONTACT" ]] && usage
[[ "$REPO" == */* ]] || { echo "error: '<owner>/<repo>' must contain a slash" >&2; usage; }
[[ "$CONTACT" == *@*.* ]] || { echo "error: '$CONTACT' does not look like an email address" >&2; usage; }

cd "$(dirname "$0")/.."

echo "==> 1/3  Repointing internal references at ${REPO}"
./scripts/set-repo-owner.sh "$REPO" | sed 's/^/    /'

echo
echo "==> 2/3  Filling in identity placeholders"

# Replace a literal placeholder across the repo. Reports what it touched so a
# silent no-op is never mistaken for success.
fill() {
  local token="$1" value="$2" label="$3" files n
  files=$(grep -rlF "$token" . --exclude-dir=.git 2>/dev/null || true)
  if [[ -z "$files" ]]; then
    echo "    - ${label}: already set (no '${token}' remaining)"
    return
  fi
  n=0
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    python3 - "$f" "$token" "$value" <<'PY'
import pathlib, sys
p = pathlib.Path(sys.argv[1])
p.write_text(p.read_text().replace(sys.argv[2], sys.argv[3]))
PY
    n=$((n + 1))
  done <<< "$files"
  echo "    ✔ ${label}: ${value}   (${n} file(s))"
}

# Assembled from pieces so the literal tokens never appear in this file. If
# they did, this script would match and rewrite itself as it ran.
L='<'; R='>'
fill "${L}COPYRIGHT HOLDER${R}" "$HOLDER"  "copyright holder"
fill "${L}SECURITY CONTACT${R}" "$CONTACT" "security contact"
fill "${L}CONDUCT CONTACT${R}"  "$CONTACT" "conduct contact"

# ---------------------------------------------------------------------------
# Commit identity
# ---------------------------------------------------------------------------
# Without a repository-local identity, git falls back to the machine's global
# one. That is how an employer address ends up as the author of every commit in
# a public repository — silently, and visible forever in the history. Setting it
# here is local to this repository only and overrides nothing else on the
# machine.
echo
echo "==> 3/3  Setting the commit identity for this repository"
if git rev-parse --git-dir >/dev/null 2>&1; then
  git config user.name  "$HOLDER"
  git config user.email "$CONTACT"
  echo "    ✔ commits from this repository will be authored by ${HOLDER} <${CONTACT}>"
  GLOBAL_EMAIL=$(git config --global user.email 2>/dev/null || true)
  if [[ -n "$GLOBAL_EMAIL" && "$GLOBAL_EMAIL" != "$CONTACT" ]]; then
    echo "      (the machine-wide identity is ${GLOBAL_EMAIL}; it is not used here)"
  fi
else
  echo "    • not a git repository yet — after 'git init', run:"
  echo "        git config user.name  \"${HOLDER}\""
  echo "        git config user.email \"${CONTACT}\""
fi

echo
# Split literal: written whole, set-repo-owner.sh would have rewritten it in
# this file and the check would match every personalised file instead.
UPSTREAM='your-org/sfdx-'"pipeline-actions"
LEFT=$(grep -rnE "${L}COPYRIGHT HOLDER${R}|${L}SECURITY CONTACT${R}|${L}CONDUCT CONTACT${R}|${UPSTREAM}" \
         . --exclude-dir=.git --exclude-dir=scripts 2>/dev/null || true)
if [[ -n "$LEFT" ]]; then
  echo "WARNING — placeholders still present:"
  echo "$LEFT" | sed 's/^/    /'
  exit 1
fi

cat <<EOF
Done. This repository is now ${REPO}.

Next:
  1. ./scripts/validate.sh                      confirm everything still resolves
  2. commit and push 'main'
  3. ./scripts/cut-release.sh v1.0.0 --push     publish the first release
     ./scripts/cut-release.sh v1     --push     move the major alias

Step 3 is NOT optional. The example consumer callers pin @v1, so until that tag
exists every consumer workflow fails to resolve. See PUBLISHING.md.
EOF
