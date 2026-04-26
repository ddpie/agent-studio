#!/usr/bin/env bash
# Build the Agent Studio frontend.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FRONTEND_DIR="$(cd "$SCRIPT_DIR/../frontend" && pwd)"

echo "=== Build Frontend ==="
cd "$FRONTEND_DIR"

if [[ ! -d "node_modules" ]]; then
  echo "Installing dependencies..."
  npm install
fi

npm run build
echo "Done — output: ${FRONTEND_DIR}/dist/"
echo ""
echo "Note: this script only BUILDS. To deploy to CloudFront, run:"
echo "  bash scripts/deploy-all.sh --only-frontend"
