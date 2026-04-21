#!/usr/bin/env bash
# Build base/deployment.zip — the shared dependency layer for every
# sub-agent runtime. Includes strands, bedrock-agentcore, playwright
# (for browser_use), boto3, opentelemetry, etc.
#
# Output: ./base/deployment.zip (tracked by CDK asset)
#
# Target platform: manylinux2014_aarch64 + python 3.10 (AgentCore runtime).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE_DIR="${PROJECT_ROOT}/base"
STAGE_DIR="${BASE_DIR}/staged"
OUT_ZIP="${BASE_DIR}/deployment.zip"
REQS="${BASE_DIR}/requirements.txt"

echo "=== Build base deployment zip ==="
echo "Output: $OUT_ZIP"

if [[ ! -f "$REQS" ]]; then
  echo "ERROR: $REQS not found" >&2
  exit 1
fi

rm -rf "$STAGE_DIR" "$OUT_ZIP"
mkdir -p "$STAGE_DIR"

echo "[1/3] Installing dependencies to $STAGE_DIR..."
pip install -r "$REQS" \
  --target "$STAGE_DIR" \
  --platform manylinux2014_aarch64 \
  --python-version 3.10 \
  --only-binary=:all: \
  --upgrade

echo "[2/3] Pruning incompatible cache files..."
find "$STAGE_DIR" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find "$STAGE_DIR" -name "*.pyc" -delete

echo "[3/3] Zipping..."
cd "$STAGE_DIR"
zip -rq "$OUT_ZIP" .
du -sh "$OUT_ZIP"

echo "=== Done ==="
