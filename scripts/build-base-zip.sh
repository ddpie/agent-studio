#!/usr/bin/env bash
# Build base deployment zips uploaded via CDK to s3://bucket/base/.
#
# Two zips are produced:
#  - base/deployment.zip          — slim; used by Meta-Agent to keep
#                                   AgentCore runtime cold-start < 30s
#                                   (from base/requirements.txt).
#  - base/sub-agent-deployment.zip — fat; includes Playwright +
#                                   strands-agents-tools for browser_use
#                                   (from base/sub-agent-requirements.txt,
#                                    falls back to requirements.txt + extras).
#
# Target platform: manylinux2014_aarch64 + python 3.10 (AgentCore runtime).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BASE_DIR="${PROJECT_ROOT}/base"

build_zip() {
  local reqs="$1"; local out_zip="$2"; local stage_name="$3"
  local stage_dir="${BASE_DIR}/${stage_name}"

  echo "--- Build $(basename "$out_zip") from $(basename "$reqs") ---"
  rm -rf "$stage_dir" "$out_zip"
  mkdir -p "$stage_dir"

  pip install -r "$reqs" \
    --target "$stage_dir" \
    --platform manylinux2014_aarch64 \
    --python-version 3.10 \
    --only-binary=:all: \
    --upgrade

  find "$stage_dir" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
  find "$stage_dir" -name "*.pyc" -delete

  if [[ -f "$stage_dir/playwright/driver/node" ]]; then
    chmod +x "$stage_dir/playwright/driver/node"
  fi
  find "$stage_dir" -name "*.so" -exec chmod +x {} \; 2>/dev/null || true

  (cd "$stage_dir" && zip -rqy "$out_zip" .)
  du -sh "$out_zip"
}

# Slim (Meta-Agent): no browser automation deps
if [[ ! -f "${BASE_DIR}/requirements.txt" ]]; then
  echo "ERROR: ${BASE_DIR}/requirements.txt not found" >&2
  exit 1
fi
build_zip "${BASE_DIR}/requirements.txt" "${BASE_DIR}/deployment.zip" "staged-slim"

# Fat (sub-agents): adds Playwright + strands-agents-tools for browser_use
SUB_REQS="${BASE_DIR}/sub-agent-requirements.txt"
if [[ -f "$SUB_REQS" ]]; then
  build_zip "$SUB_REQS" "${BASE_DIR}/sub-agent-deployment.zip" "staged-fat"
else
  echo "WARNING: $SUB_REQS not found — skipping sub-agent-deployment.zip build" >&2
fi

echo "=== Done ==="
