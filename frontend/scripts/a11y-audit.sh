#!/usr/bin/env bash
# Accessibility Audit Runner
#
# Usage: bash scripts/a11y-audit.sh [--max-stories N]
#
# Prerequisites:
#   npm install (frontend deps)
#   npx playwright install chromium
#
# This script:
#   1. Builds storybook if storybook-static/ doesn't exist
#   2. Runs axe-core against each story via Playwright
#   3. Reports violations grouped by severity

set -euo pipefail
cd "$(dirname "$0")/.."

# Build storybook if not already built
if [ ! -d "storybook-static" ]; then
  echo "Building Storybook..."
  npx storybook build --output-dir storybook-static
fi

echo "Running a11y audit..."
node scripts/a11y-audit.mjs "$@"
