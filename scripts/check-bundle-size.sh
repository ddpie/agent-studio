#!/usr/bin/env bash
# check-bundle-size.sh — Build the frontend and enforce a JS bundle size budget.
#
# Usage:
#   bash scripts/check-bundle-size.sh [--threshold-kb <KB>]
#
# Default threshold: 2048 KB (2 MB). Override with --threshold-kb.
# Exits 1 if the total JS output exceeds the budget.

set -euo pipefail

THRESHOLD_KB=3200

while [[ $# -gt 0 ]]; do
  case "$1" in
    --threshold-kb)
      THRESHOLD_KB="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../frontend" && pwd)"
DIST_DIR="$FRONTEND_DIR/dist/assets"

echo "==> Building frontend..."
cd "$FRONTEND_DIR"
npx vite build --logLevel warn 2>&1

if [[ ! -d "$DIST_DIR" ]]; then
  echo "ERROR: dist/assets not found after build" >&2
  exit 1
fi

echo ""
echo "==> Bundle size report"
echo "---------------------------------------------------"

# Collect JS files with sizes
declare -a sizes=()
declare -a names=()
TOTAL_BYTES=0

while IFS=$'\t' read -r bytes file; do
  sizes+=("$bytes")
  names+=("$(basename "$file")")
  TOTAL_BYTES=$((TOTAL_BYTES + bytes))
done < <(find "$DIST_DIR" -name "*.js" -exec du -b {} \; | sort -rn)

TOTAL_KB=$((TOTAL_BYTES / 1024))
TOTAL_MB=$(awk "BEGIN {printf \"%.2f\", $TOTAL_BYTES / 1048576}")

echo ""
echo "Top 5 largest chunks:"
for i in 0 1 2 3 4; do
  if [[ $i -lt ${#sizes[@]} ]]; then
    chunk_kb=$(awk "BEGIN {printf \"%.1f\", ${sizes[$i]} / 1024}")
    printf "  %6s KB  %s\n" "$chunk_kb" "${names[$i]}"
  fi
done

echo ""
echo "---------------------------------------------------"
echo "Total JS size: ${TOTAL_KB} KB (${TOTAL_MB} MB)"
echo "Budget:        ${THRESHOLD_KB} KB"
echo "---------------------------------------------------"

if [[ $TOTAL_KB -gt $THRESHOLD_KB ]]; then
  OVER=$((TOTAL_KB - THRESHOLD_KB))
  echo ""
  echo "FAIL: Bundle is ${OVER} KB over budget!"
  echo "Consider code-splitting large dependencies with dynamic import()."
  exit 1
else
  UNDER=$((THRESHOLD_KB - TOTAL_KB))
  echo ""
  echo "PASS: Bundle is ${UNDER} KB under budget."
  exit 0
fi
