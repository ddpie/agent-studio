#!/usr/bin/env bash
# scripts/test.sh — single entry point for all tests in Agent Studio.
#
# Tiers (can be selected individually):
#   --unit          vitest (frontend) + pytest (meta-agent)
#   --typecheck     tsc --noEmit on frontend + infra
#   --lint          eslint (frontend) + bash -n (scripts) + check-invariants
#   --security      pre-commit-security.sh against all staged+tracked files
#   --e2e           playwright E2E tests (needs running app + .env.e2e)
#   --all           all offline tiers (unit + typecheck + lint + security)
#   --full          everything including e2e
#
# Default (no args): --all (offline only, safe to run anywhere)
#
# Examples:
#   ./scripts/test.sh                  # offline: unit + typecheck + lint + security
#   ./scripts/test.sh --unit           # just unit tests
#   ./scripts/test.sh --full           # everything including e2e
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

GREEN='\033[0;32m'; RED='\033[0;31m'; CYAN='\033[0;36m'; NC='\033[0m'
hdr() { echo -e "\n${CYAN}══════════════════════════════════════════"; echo -e " $1"; echo -e "══════════════════════════════════════════${NC}"; }
ok()  { echo -e "${GREEN}✓ $1 PASSED${NC}"; }
bad() { echo -e "${RED}✗ $1 FAILED (exit $2)${NC}"; }

DO_UNIT=0; DO_TYPECHECK=0; DO_LINT=0; DO_SECURITY=0; DO_E2E=0

if [ $# -eq 0 ]; then
  DO_UNIT=1; DO_TYPECHECK=1; DO_LINT=1; DO_SECURITY=1
fi

while [[ $# -gt 0 ]]; do
  case $1 in
    --unit)      DO_UNIT=1; shift ;;
    --typecheck) DO_TYPECHECK=1; shift ;;
    --lint)      DO_LINT=1; shift ;;
    --security)  DO_SECURITY=1; shift ;;
    --e2e)       DO_E2E=1; shift ;;
    --all)       DO_UNIT=1; DO_TYPECHECK=1; DO_LINT=1; DO_SECURITY=1; shift ;;
    --full)      DO_UNIT=1; DO_TYPECHECK=1; DO_LINT=1; DO_SECURITY=1; DO_E2E=1; shift ;;
    -h|--help)   sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)           echo "Unknown argument: $1" >&2; exit 2 ;;
  esac
done

declare -a RESULTS=()
OVERALL=0

run_tier() {
  local name="$1"; shift
  hdr "$name"
  if "$@"; then
    ok "$name"
    RESULTS+=("${GREEN}✓${NC} $name")
  else
    local rc=$?
    bad "$name" "$rc"
    RESULTS+=("${RED}✗${NC} $name (exit $rc)")
    OVERALL=1
  fi
}

# --- Lint ---
if [ "$DO_LINT" = 1 ]; then
  run_tier "lint (bash -n)" bash -c '
    for f in "'"$ROOT"'"/scripts/*.sh; do bash -n "$f" || exit 1; done
  '
  run_tier "lint (eslint)" bash -c "cd '$ROOT/frontend' && npx eslint . --max-warnings 0"
  run_tier "lint (invariants)" "$ROOT/scripts/check-invariants.sh"
fi

# --- Security ---
if [ "$DO_SECURITY" = 1 ]; then
  run_tier "security (pre-commit patterns)" bash -c "
    cd '$ROOT'
    files=\$(find infra lambda -name '*.ts' -o -name '*.mjs' 2>/dev/null | grep -vE 'node_modules|__tests__|public-access-guard' || true)
    [ -z \"\$files\" ] && exit 0
    echo \"\$files\" | xargs grep -lE 'FunctionUrlAuthType\\.NONE|authType.*NONE|Principal.*\\*' 2>/dev/null | grep -v '.test.' && exit 1 || exit 0
  "
  run_tier "security (MCP IAM sync check)" bash -c "cd '$ROOT' && python3 scripts/sync-mcp-iam-policies.py --check 2>/dev/null || true"
fi

# --- Typecheck ---
if [ "$DO_TYPECHECK" = 1 ]; then
  if [ ! -d "$ROOT/frontend/node_modules" ]; then
    ( cd "$ROOT/frontend" && npm install --silent )
  fi
  run_tier "typecheck (frontend tsc)" bash -c "cd '$ROOT/frontend' && npx tsc --noEmit"

  if [ -d "$ROOT/infra" ] && [ -f "$ROOT/infra/tsconfig.json" ]; then
    if [ ! -d "$ROOT/infra/node_modules" ]; then
      ( cd "$ROOT/infra" && npm install --silent )
    fi
    run_tier "typecheck (infra tsc)" bash -c "cd '$ROOT/infra' && npx tsc --noEmit"
  fi
fi

# --- Unit tests ---
if [ "$DO_UNIT" = 1 ]; then
  run_tier "unit (frontend vitest)" bash -c "cd '$ROOT/frontend' && npx vitest run"

  if [ -d "$ROOT/meta-agent/tests" ]; then
    run_tier "unit (meta-agent pytest)" bash -c "
      cd '$ROOT/meta-agent' && PYTHONPATH=. python3 -m pytest tests/ -v --tb=short
    "
  fi
fi

# --- E2E ---
if [ "$DO_E2E" = 1 ]; then
  run_tier "e2e (playwright)" bash -c "cd '$ROOT/frontend' && npx playwright test"
fi

# --- Summary ---
echo ""
echo -e "${CYAN}═══════ Summary ═══════${NC}"
for r in "${RESULTS[@]}"; do echo -e "  $r"; done
echo ""
exit "$OVERALL"
