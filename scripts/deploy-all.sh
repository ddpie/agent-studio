#!/usr/bin/env bash
# Agent Studio — One-Click Deploy
# Usage: ./deploy-all.sh [--region us-east-1] [FLAGS]
#
# Flags:
#   --skip-infra       Skip CDK deploy (only update meta-agent + frontend)
#   --skip-frontend    Skip frontend build + deploy
#   --only-frontend    Only build + deploy frontend
#   --only-agent       Only update meta-agent code (≈ deploy-agentcore.sh)
#   --dry-run          Preflight checks + cdk diff only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
INFRA_DIR="$PROJECT_ROOT/infra"
ENV_FILE="$PROJECT_ROOT/.env"

# Shared .env helpers (update_env, load_env_as_old)
source "${SCRIPT_DIR}/lib/env-utils.sh"
FRONTEND_DIR="$PROJECT_ROOT/frontend"

# --- Parse flags ---
REGION=""
SKIP_INFRA=false
SKIP_FRONTEND=false
ONLY_FRONTEND=false
ONLY_AGENT=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --region) REGION="$2"; shift 2 ;;
    --skip-infra) SKIP_INFRA=true; shift ;;
    --skip-frontend) SKIP_FRONTEND=true; shift ;;
    --only-frontend) ONLY_FRONTEND=true; shift ;;
    --only-agent) ONLY_AGENT=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "Unknown flag: $1"; exit 1 ;;
  esac
done

# ============================================================
# Phase 0: Preflight checks + existing resource detection
# ============================================================
echo "=== Phase 0: Preflight checks ==="

# AWS credentials
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) || {
  echo "ERROR: AWS credentials not configured. Run 'aws configure' first."; exit 1
}
REGION="${REGION:-$(aws configure get region 2>/dev/null || echo "us-east-1")}"
S3_BUCKET="bedrock-agentcore-codebuild-sources-${ACCOUNT_ID}-${REGION}"

echo "  Region:  $REGION"
echo "  Account: $ACCOUNT_ID"
echo "  Bucket:  $S3_BUCKET"

# Version checks
check_version() {
  local cmd="$1" min="$2"
  if ! command -v "$cmd" &>/dev/null; then
    echo "ERROR: $cmd not found. Please install it."; exit 1
  fi
}
check_version node ""
check_version python3 ""
check_version aws ""
check_version npm ""
check_version jq ""

NODE_VER=$(node --version | sed 's/v//' | cut -d. -f1)
if [[ "$NODE_VER" -lt 18 ]]; then
  echo "ERROR: Node.js >= 18 required (found v${NODE_VER})"; exit 1
fi

# Docker check (needed for CDK Lambda bundling)
if [[ "$ONLY_FRONTEND" == false && "$ONLY_AGENT" == false && "$DRY_RUN" == false ]]; then
  if ! docker info &>/dev/null 2>&1; then
    echo "ERROR: Docker is not running. CDK Lambda bundling requires Docker."; exit 1
  fi
fi

# boto3 check
python3 -c "import boto3" 2>/dev/null || {
  echo "ERROR: boto3 not installed. Run 'pip install boto3'."; exit 1
}

# ORIGIN_VERIFY_SECRET — generate if not set, persist immediately
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a; source "$PROJECT_ROOT/.env"; set +a
fi
if [[ -z "${ORIGIN_VERIFY_SECRET:-}" ]]; then
  ORIGIN_VERIFY_SECRET=$(openssl rand -hex 32)
  echo "  Generated ORIGIN_VERIFY_SECRET"
  update_env "$ENV_FILE" "ORIGIN_VERIFY_SECRET" "$ORIGIN_VERIFY_SECRET"
fi
export ORIGIN_VERIFY_SECRET

# Detect existing resources from .env
EXISTING_META_AGENT_ID="${AGENT_STUDIO_META_AGENT_ID:-}"
EXISTING_COGNITO_USER_POOL_ID="${AGENT_STUDIO_COGNITO_USER_POOL_ID:-}"
EXISTING_COGNITO_CLIENT_ID="${AGENT_STUDIO_COGNITO_CLIENT_ID:-}"

# Safety check: existing deployment with missing Cognito
if [[ -n "$EXISTING_META_AGENT_ID" && -z "$EXISTING_COGNITO_USER_POOL_ID" ]]; then
  echo "ERROR: Existing META_AGENT_ID found but COGNITO_USER_POOL_ID is missing."
  echo "This would create a new Cognito pool, orphaning existing users."
  echo "Please set AGENT_STUDIO_COGNITO_USER_POOL_ID in .env."
  exit 1
fi

# Flag compatibility checks
if [[ "$ONLY_AGENT" == true || "$ONLY_FRONTEND" == true ]]; then
  if [[ -z "$EXISTING_META_AGENT_ID" ]]; then
    echo "ERROR: --only-agent and --only-frontend require a previous full deployment (.env with META_AGENT_ID)."
    exit 1
  fi
fi

# Export for CDK
export AGENT_STUDIO_REGION="$REGION"
export AGENT_STUDIO_ACCOUNT_ID="$ACCOUNT_ID"
export AGENT_STUDIO_S3_BUCKET="$S3_BUCKET"

if [[ -n "$EXISTING_COGNITO_USER_POOL_ID" ]]; then
  export AGENT_STUDIO_USE_EXISTING_COGNITO=true
  export AGENT_STUDIO_COGNITO_USER_POOL_ID="$EXISTING_COGNITO_USER_POOL_ID"
  export AGENT_STUDIO_COGNITO_CLIENT_ID="$EXISTING_COGNITO_CLIENT_ID"
fi
if [[ -n "$EXISTING_META_AGENT_ID" ]]; then
  export AGENT_STUDIO_EXISTING_META_AGENT_ID="$EXISTING_META_AGENT_ID"
fi

echo "  Preflight OK"
echo ""

# --- Dry run: just cdk diff ---
if [[ "$DRY_RUN" == true ]]; then
  echo "=== Dry Run: cdk diff ==="
  cd "$INFRA_DIR" && npm install --silent
  npx cdk diff --all 2>&1 || true
  echo "=== Dry run complete ==="
  exit 0
fi

# --- Only agent: skip everything except meta-agent update ---
if [[ "$ONLY_AGENT" == true ]]; then
  echo "=== Updating Meta-Agent only ==="
  bash "$PROJECT_ROOT/scripts/deploy-agentcore.sh"
  exit 0
fi

# --- Only frontend: skip everything except frontend build ---
if [[ "$ONLY_FRONTEND" == true ]]; then
  echo "=== Deploying frontend only ==="
  # Read values from .env (already loaded above)
  FRONTEND_BUCKET="${AGENT_STUDIO_FRONTEND_BUCKET:-agent-studio-frontend-${ACCOUNT_ID}-${REGION}}"
  CF_ID="${AGENT_STUDIO_CLOUDFRONT_ID:-}"

  cd "$FRONTEND_DIR"
  [[ ! -d "node_modules" ]] && npm install --silent
  npm run build

  aws s3 sync dist/ "s3://$FRONTEND_BUCKET" --delete --region "$REGION"
  if [[ -n "$CF_ID" ]]; then
    aws cloudfront create-invalidation --distribution-id "$CF_ID" --paths "/*" --region us-east-1 --no-cli-pager
  fi
  echo "=== Frontend deployed ==="
  exit 0
fi

# ============================================================
# Phase 1: Create prerequisite resources
# ============================================================
if [[ "$SKIP_INFRA" == false ]]; then
  echo "=== Phase 1: Prerequisite resources ==="

  # Verify S3 bucket exists
  if ! aws s3api head-bucket --bucket "$S3_BUCKET" --region "$REGION" 2>/dev/null; then
    echo "ERROR: S3 bucket $S3_BUCKET does not exist."
    echo "Enable AgentCore in your AWS account first, or create the bucket manually."
    exit 1
  fi

  # DDB tables
  create_table_if_missing() {
    local table_name="$1" pk="$2" gsi_json="$3" attr_json="$4"
    if aws dynamodb describe-table --table-name "$table_name" --region "$REGION" &>/dev/null; then
      echo "  Table $table_name exists, skipping."
      return
    fi
    echo "  Creating table $table_name..."
    aws dynamodb create-table \
      --table-name "$table_name" \
      --attribute-definitions $attr_json \
      --key-schema "AttributeName=$pk,KeyType=HASH" \
      --billing-mode PAY_PER_REQUEST \
      --global-secondary-indexes "$gsi_json" \
      --region "$REGION" --no-cli-pager >/dev/null
    echo "  Waiting for $table_name to be ACTIVE..."
    aws dynamodb wait table-exists --table-name "$table_name" --region "$REGION"
    aws dynamodb update-continuous-backups \
      --table-name "$table_name" \
      --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true \
      --region "$REGION" --no-cli-pager >/dev/null
    echo "  $table_name created with PITR enabled."
  }

  create_table_if_missing "agent-studio-agents" "agentId" \
    '[{"IndexName":"workspace-index","KeySchema":[{"AttributeName":"workspace_id","KeyType":"HASH"},{"AttributeName":"created_at","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}},{"IndexName":"public-index","KeySchema":[{"AttributeName":"visibility","KeyType":"HASH"},{"AttributeName":"created_at","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}]' \
    'AttributeName=agentId,AttributeType=S AttributeName=workspace_id,AttributeType=S AttributeName=created_at,AttributeType=S AttributeName=visibility,AttributeType=S'

  create_table_if_missing "agent-studio-tools" "toolId" \
    '[{"IndexName":"workspace-index","KeySchema":[{"AttributeName":"workspace_id","KeyType":"HASH"},{"AttributeName":"created_at","KeyType":"RANGE"}],"Projection":{"ProjectionType":"ALL"}}]' \
    'AttributeName=toolId,AttributeType=S AttributeName=workspace_id,AttributeType=S AttributeName=created_at,AttributeType=S'

  # base/deployment.zip
  if ! aws s3api head-object --bucket "$S3_BUCKET" --key "base/deployment.zip" --region "$REGION" 2>/dev/null; then
    echo "  Building base/deployment.zip..."
    TMPDIR=$(mktemp -d)
    pip install -q -t "$TMPDIR" strands-agents bedrock-agentcore boto3 requests httpx beautifulsoup4 markdownify pyyaml python-dateutil pydantic tabulate websocket-client
    (cd "$TMPDIR" && zip -qr /tmp/agent-studio-base.zip .)
    aws s3 cp /tmp/agent-studio-base.zip "s3://$S3_BUCKET/base/deployment.zip" --region "$REGION"
    rm -rf "$TMPDIR" /tmp/agent-studio-base.zip
    echo "  base/deployment.zip uploaded."
  else
    echo "  base/deployment.zip exists, skipping."
  fi

  # skills/index.json
  if ! aws s3api head-object --bucket "$S3_BUCKET" --key "skills/index.json" --region "$REGION" 2>/dev/null; then
    echo '[]' | aws s3 cp - "s3://$S3_BUCKET/skills/index.json" --content-type application/json --region "$REGION"
    echo "  skills/index.json initialized."
  fi

  echo ""
fi

# ============================================================
# Phase 2: Package Meta-Agent source
# ============================================================
if [[ "$SKIP_INFRA" == false ]]; then
  echo "=== Phase 2: Package Meta-Agent ==="
  cd "$PROJECT_ROOT/meta-agent"

  python3 - <<'PYEOF'
import io, os, zipfile
import boto3

region = os.environ["AGENT_STUDIO_REGION"]
bucket = os.environ["AGENT_STUDIO_S3_BUCKET"]
source_dir = os.getcwd()

s3 = boto3.client("s3", region_name=region)
base_resp = s3.get_object(Bucket=bucket, Key="base/deployment.zip")
base_data = base_resp["Body"].read()

source_files = {}
for root, dirs, files in os.walk(source_dir):
    dirs[:] = [d for d in dirs if d not in {
        "__pycache__", ".venv", ".git", ".bedrock_agentcore",
        "agent_studio_meta_agent.egg-info", "node_modules", "tests",
    }]
    for f in files:
        if f.endswith((".pyc", ".egg-info")):
            continue
        full = os.path.join(root, f)
        arcname = os.path.relpath(full, source_dir)
        source_files[arcname] = full

buf = io.BytesIO()
with zipfile.ZipFile(io.BytesIO(base_data), "r") as base_zip:
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for item in base_zip.namelist():
            if item in source_files:
                continue
            zf.writestr(item, base_zip.read(item))
        for arcname, full in source_files.items():
            zf.write(full, arcname)

package = buf.getvalue()
s3_key = "agents/agentStudioMeta/deployment.zip"
s3.put_object(Bucket=bucket, Key=s3_key, Body=package)
print(f"  Uploaded to s3://{bucket}/{s3_key} ({len(package) / 1024 / 1024:.1f} MB, {len(source_files)} files)")
PYEOF

  echo ""
fi

# ============================================================
# Phase 3: CDK deploy
# ============================================================
if [[ "$SKIP_INFRA" == false ]]; then
  echo "=== Phase 3: CDK deploy ==="
  cd "$INFRA_DIR"
  npm install --silent

  # Bootstrap both regions
  npx cdk bootstrap "aws://$ACCOUNT_ID/$REGION" 2>&1 | tail -1
  if [[ "$REGION" != "us-east-1" ]]; then
    npx cdk bootstrap "aws://$ACCOUNT_ID/us-east-1" 2>&1 | tail -1
  fi

  npx cdk deploy --all --require-approval never --outputs-file "$PROJECT_ROOT/cdk-outputs.json"
  echo ""
fi

# ============================================================
# Phase 4a: Extract CDK outputs + generate .env
# ============================================================
if [[ "$SKIP_INFRA" == false ]]; then
  echo "=== Phase 4a: Generate .env ==="
  OUTPUTS="$PROJECT_ROOT/cdk-outputs.json"

  if [[ ! -f "$OUTPUTS" ]]; then
    echo "ERROR: cdk-outputs.json not found. CDK deploy may have failed."
    exit 1
  fi

  # Extract values (CDK prefixes output keys with construct path + hash)
  get_output() {
    local val
    val=$(jq -r ".AgentStudioStack | to_entries[] | select(.key | test(\"$1\")) | .value" "$OUTPUTS" | head -1)
    if [[ -z "$val" || "$val" == "null" ]]; then
      echo "WARNING: CDK output matching '$1' not found" >&2
      echo ""
    else
      echo "$val"
    fi
  }

  CF_DOMAIN=$(get_output "CloudFrontDomain")
  CF_ID=$(get_output "CloudFrontId[^a-z]")
  FRONTEND_BUCKET=$(get_output "FrontendBucketName")
  META_AGENT_ID=$(get_output "MetaAgentId[^a-z]")
  USER_POOL_ID=$(get_output "UserPoolId[^a-zA-Z]|UserPoolId$")
  CLIENT_ID=$(get_output "UserPoolClientId")

  # Use existing Cognito IDs if they were passed through
  USER_POOL_ID="${USER_POOL_ID:-$EXISTING_COGNITO_USER_POOL_ID}"
  CLIENT_ID="${CLIENT_ID:-$EXISTING_COGNITO_CLIENT_ID}"

  # Update .env with CDK outputs, preserving all pre-existing keys
  # (e.g. AGENT_STUDIO_MCP_GATEWAY_URL, AGENT_STUDIO_MCP_GATEWAY_ID set by deploy-mcp.sh)
  update_env "$ENV_FILE" "AGENT_STUDIO_REGION"                "$REGION"
  update_env "$ENV_FILE" "AGENT_STUDIO_ACCOUNT_ID"            "$ACCOUNT_ID"
  update_env "$ENV_FILE" "AGENT_STUDIO_META_AGENT_ID"         "$META_AGENT_ID"
  update_env "$ENV_FILE" "AGENT_STUDIO_COGNITO_USER_POOL_ID"  "$USER_POOL_ID"
  update_env "$ENV_FILE" "AGENT_STUDIO_COGNITO_CLIENT_ID"     "$CLIENT_ID"
  update_env "$ENV_FILE" "AGENT_STUDIO_S3_BUCKET"             "$S3_BUCKET"
  update_env "$ENV_FILE" "AGENT_STUDIO_CLOUDFRONT_DOMAIN"     "$CF_DOMAIN"
  update_env "$ENV_FILE" "AGENT_STUDIO_API_URL"               "https://$CF_DOMAIN"
  update_env "$ENV_FILE" "AGENT_STUDIO_FRONTEND_BUCKET"       "$FRONTEND_BUCKET"
  update_env "$ENV_FILE" "AGENT_STUDIO_CLOUDFRONT_ID"         "$CF_ID"
  update_env "$ENV_FILE" "ORIGIN_VERIFY_SECRET"               "$ORIGIN_VERIFY_SECRET"

  chmod 600 "$ENV_FILE"
  echo "  .env generated at $PROJECT_ROOT/.env"
  echo ""
fi

# ============================================================
# Phase 4b: Build + deploy frontend
# ============================================================
if [[ "$SKIP_FRONTEND" == false ]]; then
  echo "=== Phase 4b: Build + deploy frontend ==="

  # Re-source .env to pick up latest values
  set -a; source "$PROJECT_ROOT/.env"; set +a

  FRONTEND_BUCKET="${AGENT_STUDIO_FRONTEND_BUCKET:-agent-studio-frontend-${ACCOUNT_ID}-${REGION}}"
  CF_ID="${AGENT_STUDIO_CLOUDFRONT_ID:-}"

  cd "$FRONTEND_DIR"
  [[ ! -d "node_modules" ]] && npm install --silent
  npm run build

  aws s3 sync dist/ "s3://$FRONTEND_BUCKET" --delete --region "$REGION"
  echo "  Frontend synced to s3://$FRONTEND_BUCKET"

  if [[ -n "$CF_ID" ]]; then
    aws cloudfront create-invalidation --distribution-id "$CF_ID" --paths "/*" --region us-east-1 --no-cli-pager >/dev/null
    echo "  CloudFront invalidation created"
  fi
  echo ""
fi

# ============================================================
# Phase 5: Output
# ============================================================
echo "=========================================="
echo "  Agent Studio deployed!"
echo "=========================================="
if [[ -f "$PROJECT_ROOT/.env" ]]; then
  set -a; source "$PROJECT_ROOT/.env"; set +a
  echo "  URL:         https://${AGENT_STUDIO_CLOUDFRONT_DOMAIN:-<pending>}"
  echo "  Meta-Agent:  ${AGENT_STUDIO_META_AGENT_ID:-<pending>}"
  echo "  Region:      $REGION"
  echo ""
  echo "  Next steps:"
  echo "    - Update meta-agent code:  bash scripts/deploy-agentcore.sh"
  echo "    - Local frontend dev:      cd frontend && npm run dev"
  echo "    - Update infrastructure:   cd infra && npx cdk deploy --all"
fi
echo "=========================================="
