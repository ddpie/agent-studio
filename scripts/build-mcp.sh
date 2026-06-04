#!/usr/bin/env bash
# Build and push MCP Runtime Docker images to ECR.
# Reads mcp-runtime/mcp-registry.yaml and builds enabled targets.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

# Load .env if it exists
if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi

# Default values
REGION="${AGENT_STUDIO_REGION:-us-east-1}"
ONLY_TARGET=""
LIST_ONLY=false

# Parse flags
while [[ $# -gt 0 ]]; do
  case $1 in
    --only)
      ONLY_TARGET="$2"
      shift 2
      ;;
    --list)
      LIST_ONLY=true
      shift
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--only <name>] [--list] [--region <region>]"
      exit 1
      ;;
  esac
done

ACCOUNT_ID="${AGENT_STUDIO_ACCOUNT_ID:?AGENT_STUDIO_ACCOUNT_ID is required}"
REGISTRY_FILE="${PROJECT_ROOT}/mcp-runtime/mcp-registry.yaml"
DOCKERFILE="${PROJECT_ROOT}/mcp-runtime/Dockerfile.template"
MCP_PROXY_VERSION="0.11.0"

echo "=== MCP Runtime Builder ==="
echo "Region:  $REGION"
echo "Account: $ACCOUNT_ID"
echo "Registry: $REGISTRY_FILE"
echo ""

# Export variables for Python scripts
export REGISTRY_FILE
export ONLY_TARGET

# Parse YAML and get targets
TARGETS=$(python3 - <<'PYEOF'
import sys
import json

try:
    import yaml
except ImportError:
    print("Error: pyyaml not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

import os

registry_file = os.environ["REGISTRY_FILE"]
only_target = os.environ.get("ONLY_TARGET", "")

with open(registry_file, "r") as f:
    data = yaml.safe_load(f)

runtime_targets = data.get("runtime_targets", [])

# Filter: enabled=true, vpc_required!=true, deprecated!=true
filtered = []
for target in runtime_targets:
    if not target.get("enabled", False):
        continue
    if target.get("vpc_required", False):
        continue
    if target.get("deprecated", False):
        continue
    if only_target and target["name"] != only_target:
        continue

    filtered.append({
        "name": target["name"],
        "package": target["package"],
        "command": target["command"],
        "version": target["version"],
        "extra_packages": target.get("extra_packages", ""),
    })

print(json.dumps(filtered, indent=2))
PYEOF
)

if [[ -z "$TARGETS" || "$TARGETS" == "[]" ]]; then
  echo "No targets to build."
  exit 0
fi

# Parse targets
TARGET_COUNT=$(echo "$TARGETS" | python3 -c "import sys, json; print(len(json.load(sys.stdin)))")

echo "Found $TARGET_COUNT target(s) to build:"
echo "$TARGETS" | python3 -c "
import sys, json
targets = json.load(sys.stdin)
for t in targets:
    print(f\"  - {t['name']} ({t['package']}=={t['version']})\")
"
echo ""

if [[ "$LIST_ONLY" == "true" ]]; then
  echo "List mode: exiting without building."
  exit 0
fi

# ECR login
echo "Logging in to ECR..."
aws ecr get-login-password --region "$REGION" | \
  docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com"
echo ""

# Build counters
BUILT=0
SKIPPED=0
FAILED=0

# Build each target
for i in $(seq 0 $((TARGET_COUNT - 1))); do
  TARGET=$(echo "$TARGETS" | python3 -c "import sys, json; print(json.dumps(json.load(sys.stdin)[$i]))")

  NAME=$(echo "$TARGET" | python3 -c "import sys, json; print(json.load(sys.stdin)['name'])")
  PACKAGE=$(echo "$TARGET" | python3 -c "import sys, json; print(json.load(sys.stdin)['package'])")
  COMMAND=$(echo "$TARGET" | python3 -c "import sys, json; print(json.load(sys.stdin)['command'])")
  VERSION=$(echo "$TARGET" | python3 -c "import sys, json; print(json.load(sys.stdin)['version'])")
  EXTRA_PACKAGES=$(echo "$TARGET" | python3 -c "import sys, json; print(json.load(sys.stdin).get('extra_packages',''))")

  REPO_NAME="mcp-${NAME}"
  IMAGE_TAG="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}:${VERSION}"
  IMAGE_TAG_LATEST="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}:latest"

  echo "[$((i + 1))/$TARGET_COUNT] Building $NAME..."
  echo "  Package: $PACKAGE==$VERSION"
  echo "  Command: $COMMAND"
  echo "  Image:   $IMAGE_TAG"

  # Create ECR repo if not exists
  if aws ecr describe-repositories --repository-names "$REPO_NAME" --region "$REGION" &>/dev/null; then
    echo "  ECR repo exists: $REPO_NAME"
  else
    echo "  Creating ECR repo: $REPO_NAME"
    if ! aws ecr create-repository \
      --repository-name "$REPO_NAME" \
      --region "$REGION" \
      --image-scanning-configuration scanOnPush=true &>/dev/null; then
      echo "  ERROR: Failed to create ECR repo"
      FAILED=$((FAILED + 1))
      echo ""
      continue
    fi
    echo "  ECR repo created"
  fi

  # Build Docker image
  echo "  Building Docker image..."
  if ! docker build \
    --build-arg MCP_PACKAGE="$PACKAGE" \
    --build-arg MCP_PACKAGE_VERSION="$VERSION" \
    --build-arg MCP_COMMAND="$COMMAND" \
    --build-arg MCP_PROXY_VERSION="$MCP_PROXY_VERSION" \
    --build-arg EXTRA_PACKAGES="$EXTRA_PACKAGES" \
    -t "$IMAGE_TAG" \
    -t "$IMAGE_TAG_LATEST" \
    -f "$DOCKERFILE" \
    "${PROJECT_ROOT}/mcp-runtime" 2>&1 | sed 's/^/    /'; then
    echo "  ERROR: Docker build failed"
    FAILED=$((FAILED + 1))
    echo ""
    continue
  fi

  # Push to ECR
  echo "  Pushing to ECR..."
  if ! docker push "$IMAGE_TAG" 2>&1 | sed 's/^/    /'; then
    echo "  ERROR: Docker push failed"
    FAILED=$((FAILED + 1))
    echo ""
    continue
  fi

  if ! docker push "$IMAGE_TAG_LATEST" 2>&1 | sed 's/^/    /'; then
    echo "  WARNING: Failed to push latest tag"
  fi

  echo "  SUCCESS"
  BUILT=$((BUILT + 1))
  echo ""
done

# Summary
echo "=== Build Summary ==="
echo "Built:   $BUILT"
echo "Skipped: $SKIPPED"
echo "Failed:  $FAILED"
echo ""

if [[ $FAILED -gt 0 ]]; then
  echo "Some builds failed. Check logs above."
  exit 1
fi

echo "All builds completed successfully."
