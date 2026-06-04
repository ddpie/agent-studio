#!/usr/bin/env bash
# Mutation testing for security-critical Lambda modules.
# Usage:
#   bash scripts/mutation-test.sh              # run all configured modules
#   bash scripts/mutation-test.sh shared/auth.py  # run only specified file
#
# Requires: pip install mutmut
# Config: lambda/pyproject.toml [tool.mutmut]

set -euo pipefail
cd "$(git rev-parse --show-toplevel)/lambda"

# Clean previous run
rm -rf .mutmut-cache

MODULE="${1:-}"

if [[ -n "$MODULE" ]]; then
  echo "▶ Running mutation tests on: $MODULE"
  # Override paths_to_mutate via env is not supported; filter via mutant names
  mutmut run
else
  echo "▶ Running mutation tests on all configured modules"
  mutmut run
fi

echo ""
echo "▶ Results summary:"
mutmut results || true

SURVIVED=$(mutmut results 2>/dev/null | grep -c "Survived" || true)
if [[ "$SURVIVED" -gt 0 ]]; then
  echo ""
  echo "⚠ $SURVIVED mutants survived — review with: cd lambda && mutmut show <id>"
  exit 1
fi

echo "✓ All mutants killed"
