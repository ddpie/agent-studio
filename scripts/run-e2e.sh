#!/usr/bin/env bash
# Run E2E tests for Agent Studio frontend.
# Usage:
#   bash scripts/run-e2e.sh              # headless, all tests
#   bash scripts/run-e2e.sh --headed     # with browser visible
#   bash scripts/run-e2e.sh --ui         # Playwright UI mode
#   bash scripts/run-e2e.sh --fast       # skip slow lifecycle tests
#   bash scripts/run-e2e.sh chat-flow    # run specific spec
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

cd "$FRONTEND_DIR"

# Parse args
HEADED=""
UI_MODE=""
FAST=""
SPEC=""

for arg in "$@"; do
  case "$arg" in
    --headed) HEADED="--headed" ;;
    --ui) UI_MODE="1" ;;
    --fast) FAST="1" ;;
    --help|-h)
      echo "Usage: $0 [options] [spec-name]"
      echo ""
      echo "Options:"
      echo "  --headed   Run with visible browser"
      echo "  --ui       Open Playwright UI mode"
      echo "  --fast     Skip slow lifecycle tests (agent creation/deploy)"
      echo "  -h,--help  Show this help"
      echo ""
      echo "Examples:"
      echo "  $0                    # Run all tests headless"
      echo "  $0 --headed           # Run all tests with browser"
      echo "  $0 --fast             # Skip slow tests"
      echo "  $0 chat-flow          # Run only chat-flow spec"
      echo "  $0 --headed skill     # Run skill-related specs with browser"
      exit 0
      ;;
    -*) echo "Unknown option: $arg"; exit 1 ;;
    *) SPEC="$arg" ;;
  esac
done

# Check .env.e2e exists
if [[ ! -f "e2e/.env.e2e" ]]; then
  echo "ERROR: e2e/.env.e2e not found."
  echo "Copy e2e/.env.e2e.example to e2e/.env.e2e and fill in credentials."
  exit 1
fi

# Check credentials are set
if grep -q "^E2E_USERNAME=$" e2e/.env.e2e 2>/dev/null; then
  echo "ERROR: E2E_USERNAME is empty in e2e/.env.e2e"
  exit 1
fi

echo "=== Agent Studio E2E Tests ==="
echo "Mode: ${HEADED:+headed }${UI_MODE:+ui }${FAST:+fast }${SPEC:+spec=$SPEC}"
echo ""

# Build command
CMD="npx playwright test"

if [[ -n "$UI_MODE" ]]; then
  CMD="npx playwright test --ui"
elif [[ -n "$HEADED" ]]; then
  CMD="$CMD --headed"
fi

if [[ -n "$FAST" ]]; then
  CMD="$CMD --grep-invert 'Full Closed Loop'"
fi

if [[ -n "$SPEC" ]]; then
  CMD="$CMD $SPEC"
fi

echo "Running: $CMD"
echo ""
eval "$CMD"
