#!/usr/bin/env bash
# Build base deployment zips uploaded via CDK to s3://bucket/base/.
#
# Two zips are produced:
#  - base/deployment.zip          — slim; used by Meta-Agent to keep
#                                   AgentCore runtime cold-start < 30s
#                                   (from base/requirements.txt).
#                                   Also contains kiro-cli binaries for the
#                                   kiro_adapter backend, unless SKIP_KIRO=1.
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
KIRO_CACHE_DIR="${BASE_DIR}/.kiro-cache"

KIRO_CHANNEL="${KIRO_CHANNEL:-stable}"
KIRO_ARCH="${KIRO_ARCH:-aarch64}"
KIRO_BASE_URL="https://prod.download.cli.kiro.dev"
KIRO_ZIP_NAME="kirocli-${KIRO_ARCH}-linux.zip"

# Resolve the latest Kiro CLI package, verify sha256, and extract to a cache
# directory. Reuses the cache on subsequent runs when the sha matches.
# Prints the absolute path of the extracted kiro-cli binary to stdout.
# Set SKIP_KIRO=1 to skip entirely (the slim zip will be produced without Kiro).
ensure_kiro_binary() {
  local manifest_url="${KIRO_BASE_URL}/${KIRO_CHANNEL}/latest/manifest.json"
  local zip_url="${KIRO_BASE_URL}/${KIRO_CHANNEL}/latest/${KIRO_ZIP_NAME}"

  echo "--- Resolve Kiro CLI (channel=${KIRO_CHANNEL}, arch=${KIRO_ARCH}) ---" >&2

  local expected_sha
  expected_sha=$(curl -fsSL "$manifest_url" \
    | python3 -c "
import json, sys
m = json.load(sys.stdin)
target = '${KIRO_ZIP_NAME}'
for p in m.get('packages', []):
    if p.get('download', '').endswith(target):
        print(p['sha256']); break
") || {
    echo "ERROR: failed to resolve Kiro sha256 from $manifest_url" >&2
    exit 1
  }
  if [[ -z "$expected_sha" || ! "$expected_sha" =~ ^[a-f0-9]{64}$ ]]; then
    echo "ERROR: manifest did not contain a valid sha256 for ${KIRO_ZIP_NAME}" >&2
    exit 1
  fi

  local cache_key="${expected_sha:0:16}"
  local extracted_dir="${KIRO_CACHE_DIR}/${cache_key}"
  local cached_zip="${KIRO_CACHE_DIR}/${cache_key}.zip"
  local kiro_cli_path="${extracted_dir}/kirocli/bin/kiro-cli"

  if [[ -x "$kiro_cli_path" ]]; then
    echo "  cache hit: ${kiro_cli_path}" >&2
    echo "$extracted_dir"
    return 0
  fi

  mkdir -p "$KIRO_CACHE_DIR"
  echo "  downloading ${zip_url}" >&2
  curl -fsSL -o "$cached_zip" "$zip_url"

  local actual_sha
  actual_sha=$(sha256sum "$cached_zip" | cut -d' ' -f1)
  if [[ "$actual_sha" != "$expected_sha" ]]; then
    rm -f "$cached_zip"
    echo "ERROR: Kiro sha256 mismatch (expected $expected_sha, got $actual_sha)" >&2
    exit 1
  fi

  rm -rf "$extracted_dir"
  mkdir -p "$extracted_dir"
  unzip -q "$cached_zip" -d "$extracted_dir"

  if [[ ! -x "$kiro_cli_path" ]]; then
    echo "ERROR: extracted archive missing expected binary at $kiro_cli_path" >&2
    exit 1
  fi
  echo "  extracted to ${extracted_dir} (sha256=${expected_sha})" >&2
  echo "$extracted_dir"
}

# Copy the Kiro CLI chat binary from the cache into a stage directory under
# ./kiro-bin/.
#
# Only kiro-cli-chat is bundled. The kiro-cli dispatcher (~102MB) is omitted
# because `kiro-cli-chat acp` accepts the same subcommand and responds
# identically — verified by sending initialize JSON-RPC to both binaries.
# kiro-cli-term (pty wrapper, ~74MB) is also omitted; ACP runs headless.
#
# Set KIRO_BUNDLE_DISPATCHER=1 to include kiro-cli as well (e.g. for local
# debugging where you want the `kiro-cli <subcommand>` UX). Set
# KIRO_BUNDLE_TERM=1 to include the pty binary.
install_kiro_into_stage() {
  local stage_dir="$1"
  local cache_extract_dir="$2"

  local src_bin="${cache_extract_dir}/kirocli/bin"
  local dst_dir="${stage_dir}/kiro-bin"
  mkdir -p "$dst_dir"

  cp "${src_bin}/kiro-cli-chat" "${dst_dir}/kiro-cli-chat"
  if [[ "${KIRO_BUNDLE_DISPATCHER:-0}" == "1" ]]; then
    cp "${src_bin}/kiro-cli" "${dst_dir}/kiro-cli"
  fi
  if [[ "${KIRO_BUNDLE_TERM:-0}" == "1" && -f "${src_bin}/kiro-cli-term" ]]; then
    cp "${src_bin}/kiro-cli-term" "${dst_dir}/kiro-cli-term"
  fi
  chmod +x "${dst_dir}"/*
  echo "  kiro-bin installed into ${dst_dir}" >&2
  du -sh "${dst_dir}" >&2
}

# build_zip REQS OUT_ZIP STAGE_NAME [include_kiro]
#   include_kiro: 1 to bundle kiro-cli binaries under kiro-bin/ (slim only)
build_zip() {
  local reqs="$1"; local out_zip="$2"; local stage_name="$3"
  local include_kiro="${4:-0}"
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

  if [[ "$include_kiro" == "1" ]]; then
    if [[ "${SKIP_KIRO:-0}" == "1" ]]; then
      echo "  SKIP_KIRO=1 — skipping kiro-cli bundling" >&2
    else
      local cache_extract_dir
      cache_extract_dir=$(ensure_kiro_binary)
      install_kiro_into_stage "$stage_dir" "$cache_extract_dir"
    fi
  fi

  (cd "$stage_dir" && zip -rqy "$out_zip" .)
  du -sh "$out_zip"
}

# Slim (Meta-Agent): no browser automation deps, includes kiro-cli
if [[ ! -f "${BASE_DIR}/requirements.txt" ]]; then
  echo "ERROR: ${BASE_DIR}/requirements.txt not found" >&2
  exit 1
fi
build_zip "${BASE_DIR}/requirements.txt" "${BASE_DIR}/deployment.zip" "staged-slim" 1

# Fat (sub-agents): adds Playwright + strands-agents-tools for browser_use
SUB_REQS="${BASE_DIR}/sub-agent-requirements.txt"
if [[ -f "$SUB_REQS" ]]; then
  build_zip "$SUB_REQS" "${BASE_DIR}/sub-agent-deployment.zip" "staged-fat" 0
else
  echo "WARNING: $SUB_REQS not found — skipping sub-agent-deployment.zip build" >&2
fi

echo "=== Done ==="
