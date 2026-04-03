#!/usr/bin/env bash
# Deploy all AgentCore-related resources for Agent Studio.
# Reads configuration from .env in the project root.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

# Load .env
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: .env not found at $ENV_FILE"
  echo "Copy .env.example to .env and fill in your values."
  exit 1
fi
set -a; source "$ENV_FILE"; set +a

# Validate required vars
REQUIRED_VARS=(
  AGENT_STUDIO_REGION
  AGENT_STUDIO_ACCOUNT_ID
  AGENT_STUDIO_META_AGENT_ID
  AGENT_STUDIO_S3_BUCKET
)
for var in "${REQUIRED_VARS[@]}"; do
  if [[ -z "${!var:-}" ]]; then
    echo "ERROR: $var is not set in .env"
    exit 1
  fi
done

REGION="$AGENT_STUDIO_REGION"
BUCKET="$AGENT_STUDIO_S3_BUCKET"
META_AGENT_ID="$AGENT_STUDIO_META_AGENT_ID"

echo "=== Agent Studio Deploy ==="
echo "Region:  $REGION"
echo "Account: $AGENT_STUDIO_ACCOUNT_ID"
echo "Bucket:  $BUCKET"
echo "Meta-Agent: $META_AGENT_ID"
echo ""

# --- 1. Initialize skills/index.json if missing ---
echo "[1/2] Checking skills/index.json..."
if aws s3api head-object --bucket "$BUCKET" --key "skills/index.json" --region "$REGION" 2>/dev/null; then
  echo "  skills/index.json exists, skipping."
else
  echo "  Creating empty skills/index.json..."
  echo '[]' | aws s3 cp - "s3://${BUCKET}/skills/index.json" \
    --content-type application/json --region "$REGION"
  echo "  Done."
fi

# --- 2. Deploy Meta-Agent ---
echo ""
echo "[2/2] Deploying Meta-Agent..."
cd "${PROJECT_ROOT}/meta-agent"

python3 - <<'PYEOF'
import io, os, zipfile, time
import boto3

region = os.environ["AGENT_STUDIO_REGION"]
bucket = os.environ["AGENT_STUDIO_S3_BUCKET"]
agent_id = os.environ["AGENT_STUDIO_META_AGENT_ID"]
source_dir = os.path.dirname(os.path.abspath(__file__)) if "__file__" in dir() else os.getcwd()

print(f"  Packaging {source_dir}...")

# Build zip from source directory (flat structure)
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(source_dir):
        # Skip non-deployable directories
        dirs[:] = [d for d in dirs if d not in {
            "__pycache__", ".venv", ".git", ".bedrock_agentcore",
            "agent_studio_meta_agent.egg-info", "node_modules",
        }]
        for f in files:
            if f.endswith((".pyc", ".egg-info")):
                continue
            full = os.path.join(root, f)
            arcname = os.path.relpath(full, source_dir)
            zf.write(full, arcname)

package = buf.getvalue()
print(f"  Package size: {len(package) / 1024:.0f} KB")

# Upload to S3
s3 = boto3.client("s3", region_name=region)
s3_key = f"agents/{agent_id}/deployment.zip"
s3.put_object(Bucket=bucket, Key=s3_key, Body=package)
print(f"  Uploaded to s3://{bucket}/{s3_key}")

# Update agent runtime
control = boto3.client("bedrock-agentcore-control", region_name=region)
control.update_agent_runtime(
    agentRuntimeId=agent_id,
    agentRuntimeArtifact={
        "codeConfiguration": {
            "code": {"s3": {"bucket": bucket, "prefix": s3_key}},
            "runtime": "PYTHON_3_10",
            "entryPoint": ["main.py"],
        }
    },
)
print(f"  Update triggered for {agent_id}")

# Wait for READY
print("  Waiting for READY...", end="", flush=True)
for _ in range(30):
    time.sleep(10)
    resp = control.get_agent_runtime(agentRuntimeId=agent_id)
    status = resp["status"]
    print(".", end="", flush=True)
    if status == "READY":
        print(f"\n  Status: {status}")
        break
    if status in ("FAILED", "DELETING"):
        print(f"\n  Status: {status} — deployment failed!")
        exit(1)
else:
    print("\n  TIMEOUT — agent did not become READY in 5 minutes")
    exit(1)
PYEOF

echo "  Meta-Agent deployed."

echo ""
echo "=== Deploy complete ==="
echo "Meta-Agent runtime: $META_AGENT_ID"
