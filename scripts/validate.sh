#!/usr/bin/env bash
#
# validate.sh — static checks for this repository.
#
# Everything here is cheap and offline. It catches the failure modes that would
# otherwise only show up at runtime, inside somebody else's repository.
#
#   1. every YAML file parses
#   2. every shell script is syntactically valid
#   3. every Python script compiles
#   4. every internal `uses:` points at a path that actually exists
#   5. no reusable workflow calls sf-pipeline/scripts/ directly
#   6. internal references all use the same owner/repo and the same ref
#
# Usage: ./scripts/validate.sh

set -uo pipefail

cd "$(dirname "$0")/.."

FAIL=0
pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=1; }
head2() { printf '\n\033[1m%s\033[0m\n' "$*"; }

# ---------------------------------------------------------------------------
head2 "1. YAML parses"
# ---------------------------------------------------------------------------
YAML_FILES=$(find .github sf-pipeline examples -name '*.yml' -o -name '*.yaml' 2>/dev/null | sort)
if ! python3 -c 'import yaml' 2>/dev/null; then
  echo "  (skipped — PyYAML not installed: pip install pyyaml)"
else
  while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    if ERR=$(python3 -c 'import yaml,sys; yaml.safe_load(open(sys.argv[1]))' "$f" 2>&1); then
      :
    else
      fail "$f — ${ERR##*$'\n'}"
    fi
  done <<< "$YAML_FILES"
  [[ $FAIL -eq 0 ]] && pass "$(wc -l <<< "$YAML_FILES" | tr -d ' ') YAML files parse"
fi

# ---------------------------------------------------------------------------
head2 "2. Shell syntax"
# ---------------------------------------------------------------------------
SH_COUNT=0
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  SH_COUNT=$((SH_COUNT + 1))
  bash -n "$f" 2>/dev/null || fail "$f has a shell syntax error"
done <<< "$(find . -name '*.sh' -not -path './.git/*' | sort)"
pass "${SH_COUNT} shell scripts are syntactically valid"

# ---------------------------------------------------------------------------
head2 "3. Python compiles"
# ---------------------------------------------------------------------------
PY_COUNT=0
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  PY_COUNT=$((PY_COUNT + 1))
  python3 -m py_compile "$f" 2>/dev/null || fail "$f does not compile"
done <<< "$(find . -name '*.py' -not -path './.git/*' | sort)"
find . -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null
pass "${PY_COUNT} Python scripts compile"

# ---------------------------------------------------------------------------
head2 "4. Internal references resolve to real paths"
# ---------------------------------------------------------------------------
SELF=$(grep -rhoE '[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/(\.github/workflows|sf-pipeline)/[^@]+@[A-Za-z0-9._-]+' \
         --include='*.yml' .github sf-pipeline 2>/dev/null | sort -u)

MISSING=0
TOTAL=0
while IFS= read -r ref; do
  [[ -z "$ref" ]] && continue
  TOTAL=$((TOTAL + 1))
  # strip owner/repo and @ref, leaving the in-repo path
  PATH_PART="${ref%@*}"
  PATH_PART="${PATH_PART#*/}"      # drop owner
  PATH_PART="${PATH_PART#*/}"      # drop repo
  if [[ "$PATH_PART" == .github/workflows/* ]]; then
    [[ -f "$PATH_PART" ]] || { fail "missing workflow: $PATH_PART"; MISSING=1; }
  else
    [[ -f "$PATH_PART/action.yml" ]] || { fail "missing action: $PATH_PART/action.yml"; MISSING=1; }
  fi
done <<< "$SELF"
[[ $MISSING -eq 0 ]] && pass "${TOTAL} distinct internal references all resolve"

# ---------------------------------------------------------------------------
head2 "5. Reusable workflows do not call scripts/ directly"
# ---------------------------------------------------------------------------
# A reusable workflow executes in the CONSUMER's checkout, where this repo's
# sf-pipeline/scripts/ does not exist. Scripts must be reached via a composite
# action using $GITHUB_ACTION_PATH.
LEAK=$(grep -rnE '^\s*(run:|-\s*run:).*sf-pipeline/scripts/' .github/workflows/*.yml 2>/dev/null || true)
if [[ -n "$LEAK" ]]; then
  while IFS= read -r l; do fail "direct script call: $l"; done <<< "$LEAK"
else
  pass "no reusable workflow calls sf-pipeline/scripts/ directly"
fi

# ---------------------------------------------------------------------------
head2 "6. Internal references are consistent"
# ---------------------------------------------------------------------------
OWNERS=$(sed -E 's#/(\.github/workflows|sf-pipeline)/.*##' <<< "$SELF" | sort -u)
REFS=$(sed -E 's|.*@||' <<< "$SELF" | sort -u)

if [[ $(wc -l <<< "$OWNERS" | tr -d ' ') -gt 1 ]]; then
  fail "mixed owner/repo in internal references:"; echo "$OWNERS" | sed 's/^/      /'
else
  pass "all internal references use ${OWNERS}"
fi

if [[ $(wc -l <<< "$REFS" | tr -d ' ') -gt 1 ]]; then
  fail "mixed refs in internal references:"; echo "$REFS" | sed 's/^/      /'
else
  pass "all internal references pin @${REFS}"
fi

# ---------------------------------------------------------------------------
echo
if [[ $FAIL -eq 0 ]]; then
  printf '\033[32mAll checks passed.\033[0m\n'
else
  printf '\033[31mSome checks failed.\033[0m\n'
fi
exit $FAIL
