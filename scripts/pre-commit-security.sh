#!/usr/bin/env bash
# Security scan for the patterns that caused the 2026-04-19 public-access TT.
#
# Installed as a git pre-commit hook via:
#   ln -sf ../../scripts/pre-commit-security.sh .git/hooks/pre-commit
#
# Runs against staged files only. Exits non-zero on any hit.

set -eu

# Collect staged files that could introduce the pattern.
# Exclude the guard itself (its error messages necessarily contain the
# forbidden patterns) and any file explicitly marked as a test fixture.
files=$(git diff --cached --name-only --diff-filter=ACMR \
  -- 'infra/**/*.ts' 'infra/*.ts' 'lambda/**/*.mjs' 'lambda/**/*.ts' 2>/dev/null \
  | grep -vE 'public-access-guard\.ts$|__tests__/|\.test\.(ts|mjs)$' \
  || true)

[ -z "$files" ] && exit 0

fail=0

check() {
  local pattern="$1"
  local message="$2"
  local hits
  # Skip lines that look like comments (JS/TS `//`, `*`, `/*`, `#`).
  # This lets docs/tests reference the forbidden patterns textually
  # (e.g. this script itself, or a unit test asserting the check fires).
  hits=$(echo "$files" | xargs -r grep -nE "$pattern" 2>/dev/null \
    | grep -vE ':[[:space:]]*(//|\*|#)' \
    || true)
  if [ -n "$hits" ]; then
    echo "✗ $message"
    echo "$hits" | sed 's/^/    /'
    fail=1
  fi
}

check 'FunctionUrlAuthType\.NONE|authType:[[:space:]]*["'\'']NONE["'\'']' \
  "Lambda Function URL AuthType=NONE is a red line. Use AWS_IAM + CloudFront OAC."

check 'principal:[[:space:]]*new[[:space:]]+[A-Za-z_.]*AnyPrincipal|Principal:[[:space:]]*["'\'']\*["'\'']' \
  'Lambda resource policy Principal:"*" triggers public-access TT regardless of conditions. Use a Service principal with SourceArn.'

if [ "$fail" -ne 0 ]; then
  echo ""
  echo "Blocked by pre-commit-security.sh. See memory/feedback_infra_constraints.md"
  echo "for the canonical pattern (header-rename via CloudFront Function + OAC)."
  exit 1
fi

exit 0
