#!/usr/bin/env bash
# Run all unit tests for Agent Studio.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR/.."

FAILED=0

echo "=== Backend: meta-agent tests ==="
cd "$PROJECT_ROOT/meta-agent"
PYTHONPATH=. python3 -m pytest tests/ -v --tb=short \
  --cov=tools.validate_agent --cov=templates.prompt_templates \
  --cov-report=term-missing || FAILED=1

echo ""
echo "=== Frontend tests ==="
cd "$PROJECT_ROOT/frontend"
npx vitest run --coverage || FAILED=1

echo ""
if [ "$FAILED" -eq 0 ]; then
  echo "All tests passed."
else
  echo "Some tests failed." >&2
  exit 1
fi
