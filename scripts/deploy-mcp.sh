#!/usr/bin/env bash
# Deploy MCP Gateway + register targets (remote + runtime).
# Replaces CDK Custom Resources with direct API calls.
# Reads mcp-runtime/mcp-registry.yaml for target definitions.
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
GATEWAY_ONLY=false
SKIP_CATALOG=false

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
    --gateway-only)
      GATEWAY_ONLY=true
      shift
      ;;
    --region)
      REGION="$2"
      shift 2
      ;;
    --skip-catalog)
      SKIP_CATALOG=true
      shift
      ;;
    *)
      echo "Unknown option: $1"
      echo "Usage: $0 [--only <name>] [--list] [--gateway-only] [--region <region>] [--skip-catalog]"
      exit 1
      ;;
  esac
done

ACCOUNT_ID="${AGENT_STUDIO_ACCOUNT_ID:?AGENT_STUDIO_ACCOUNT_ID is required}"
S3_BUCKET="${AGENT_STUDIO_S3_BUCKET:?AGENT_STUDIO_S3_BUCKET is required}"
REGISTRY_FILE="${PROJECT_ROOT}/mcp-runtime/mcp-registry.yaml"

echo "=== MCP Gateway Deploy ==="
echo "Region:   $REGION"
echo "Account:  $ACCOUNT_ID"
echo "Registry: $REGISTRY_FILE"
echo ""

# Preflight
python3 -c "import boto3, yaml" 2>/dev/null || {
  echo "ERROR: boto3 and pyyaml are required. Run: pip install boto3 pyyaml"; exit 1
}

# Export for Python blocks
export REGION ACCOUNT_ID S3_BUCKET REGISTRY_FILE ONLY_TARGET

# ============================================================
# Parse targets from registry (before any API calls)
# ============================================================
TARGETS_JSON=$(python3 << 'PYEOF'
import json, os, sys
import yaml

registry_file = os.environ["REGISTRY_FILE"]
only_target = os.environ.get("ONLY_TARGET", "")

with open(registry_file) as f:
    registry = yaml.safe_load(f)

remote = []
for t in registry.get("remote_targets", []):
    if not t.get("enabled"):
        continue
    if only_target and t["name"] != only_target:
        continue
    remote.append({"name": t["name"], "endpoint": t["endpoint"], "description": t.get("description", ""), "category": t.get("category", "general")})

runtime = []
for t in registry.get("runtime_targets", []):
    if not t.get("enabled") or t.get("vpc_required") or t.get("deprecated"):
        continue
    if only_target and t["name"] != only_target:
        continue
    runtime.append({
        "name": t["name"],
        "package": t["package"],
        "command": t["command"],
        "version": t["version"],
        "description": t.get("description", ""),
        "category": t.get("category", "general"),
    })

print(json.dumps({"remote": remote, "runtime": runtime}))
PYEOF
)

# --list mode: print and exit (no API calls)
if [[ "$LIST_ONLY" == true ]]; then
  echo "=== Targets that would be deployed ==="
  echo "$TARGETS_JSON" | python3 -c "
import sys, json
data = json.load(sys.stdin)
print(f\"Remote targets ({len(data['remote'])}):\")
for t in data['remote']:
    print(f\"  - {t['name']}: {t['endpoint']}\")
print(f\"Runtime targets ({len(data['runtime'])}):\")
for t in data['runtime']:
    print(f\"  - {t['name']} ({t['package']}=={t['version']})\")
print(f\"Total: {len(data['remote']) + len(data['runtime'])} targets\")
"
  exit 0
fi

# ============================================================
# Helpers: shared .env utilities
# ============================================================
source "${SCRIPT_DIR}/lib/env-utils.sh"

# ============================================================
# Step 1: Create or find MCP Gateway
# ============================================================
echo "[1/6] Gateway setup..."

GATEWAY_INFO=$(python3 << 'PYEOF'
import json, os, sys
import boto3

region = os.environ["REGION"]
account_id = os.environ["ACCOUNT_ID"]
registry_file = os.environ["REGISTRY_FILE"]

import yaml
with open(registry_file) as f:
    registry = yaml.safe_load(f)

gateway_name = registry["gateway"]["name"]
client = boto3.client("bedrock-agentcore-control", region_name=region)

# List existing gateways, find by name
gateway_id = None
gateway_url = None
gateway_arn = None
gateway_role_arn = None

try:
    paginator = client.get_paginator("list_gateways")
    for page in paginator.paginate():
        for gw in page.get("gateways", []):
            if gw.get("name") == gateway_name:
                gateway_id = gw["gatewayId"]
                break
        if gateway_id:
            break
except Exception:
    # list_gateways may not support pagination — try direct call
    try:
        resp = client.list_gateways()
        for gw in resp.get("items", resp.get("gateways", [])):
            if gw.get("name") == gateway_name:
                gateway_id = gw["gatewayId"]
                break
    except Exception as e:
        print(f"  WARNING: list_gateways failed: {e}", file=sys.stderr)

if gateway_id:
    print(f"  Found existing gateway: {gateway_id}", file=sys.stderr)
else:
    # Create IAM role for Gateway
    iam = boto3.client("iam")
    role_name = f"AgentStudio-McpGateway-{region}"
    try:
        iam.get_role(RoleName=role_name)
        print(f"  IAM role exists: {role_name}", file=sys.stderr)
    except iam.exceptions.NoSuchEntityException:
        print(f"  Creating IAM role: {role_name}", file=sys.stderr)
        iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps({
                "Version": "2012-10-17",
                "Statement": [{
                    "Effect": "Allow",
                    "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                    "Condition": {
                        "StringEquals": {
                            "aws:SourceAccount": account_id
                        }
                    }
                }]
            }),
        )
        import time as _time
        _time.sleep(10)  # Wait for IAM propagation

    # Converge trust policy (idempotent — ensures aws:SourceAccount condition)
    iam.update_assume_role_policy(
        RoleName=role_name,
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Principal": {"Service": "bedrock-agentcore.amazonaws.com"},
                "Action": "sts:AssumeRole",
                "Condition": {
                    "StringEquals": {
                        "aws:SourceAccount": account_id
                    }
                }
            }]
        }),
    )

    # Ensure policy exists (idempotent — covers both new and existing roles)
    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="McpGatewayPolicy",
        PolicyDocument=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "bedrock-agentcore:InvokeAgentRuntime",
                        "bedrock-agentcore:InvokeAgent",
                    ],
                    "Resource": "*"
                }
            ]
        }),
    )

    role_arn = iam.get_role(RoleName=role_name)["Role"]["Arn"]

    # Create gateway
    print(f"  Creating gateway '{gateway_name}'...", file=sys.stderr)
    try:
        resp = client.create_gateway(
            name=gateway_name,
            protocolType="MCP",
            authorizerType="NONE",
            roleArn=role_arn,
        )
        gateway_id = resp["gatewayId"]
        print(f"  Created gateway: {gateway_id}", file=sys.stderr)
    except client.exceptions.ConflictException:
        # Gateway already exists — find it
        print(f"  Gateway already exists, looking up...", file=sys.stderr)
        resp = client.list_gateways()
        for gw in resp.get("items", resp.get("gateways", [])):
            if gw.get("name") == gateway_name:
                gateway_id = gw["gatewayId"]
                break
        if not gateway_id:
            print(f"  ERROR: Gateway exists but could not find it", file=sys.stderr)
            sys.exit(1)
        print(f"  Found gateway: {gateway_id}", file=sys.stderr)
    except Exception as e:
        print(f"  ERROR creating gateway: {e}", file=sys.stderr)
        sys.exit(1)

    # Wait for gateway to be ready
    import time as _time
    for _ in range(30):
        gw_status = client.get_gateway(gatewayIdentifier=gateway_id)
        if gw_status.get("status") == "READY":
            break
        print(f"  Waiting for gateway READY (current: {gw_status.get('status')})...", file=sys.stderr)
        _time.sleep(5)
    else:
        print(f"  WARNING: Gateway not READY after 150s", file=sys.stderr)

# Get full gateway details
gw = client.get_gateway(gatewayIdentifier=gateway_id)
gateway_url = gw.get("gatewayUrl", "")
gateway_arn = gw.get("gatewayArn", "")

# Extract role ARN from authorizerConfiguration or gateway-level role
auth_config = gw.get("authorizerConfiguration", {})
if isinstance(auth_config, dict):
    gateway_role_arn = auth_config.get("roleArn", "")
if not gateway_role_arn:
    gateway_role_arn = gw.get("roleArn", "")

# Wait for gateway to be ready if just created
import time
status = gw.get("status", "")
if status not in ("READY", "ACTIVE", "AVAILABLE"):
    print(f"  Waiting for gateway (status: {status})...", file=sys.stderr)
    for _ in range(30):
        time.sleep(10)
        gw = client.get_gateway(gatewayIdentifier=gateway_id)
        status = gw.get("status", "")
        print(f"  Status: {status}", file=sys.stderr)
        if status in ("READY", "ACTIVE", "AVAILABLE"):
            gateway_url = gw.get("gatewayUrl", gateway_url)
            gateway_arn = gw.get("gatewayArn", gateway_arn)
            if not gateway_role_arn:
                ac = gw.get("authorizerConfiguration", {})
                if isinstance(ac, dict):
                    gateway_role_arn = ac.get("roleArn", "")
                if not gateway_role_arn:
                    gateway_role_arn = gw.get("roleArn", "")
            break
        if status in ("FAILED", "CREATE_FAILED", "DELETING"):
            print(f"  ERROR: Gateway creation failed (status: {status})", file=sys.stderr)
            sys.exit(1)
    else:
        print("  ERROR: Gateway did not become ready in 5 minutes", file=sys.stderr)
        sys.exit(1)

result = {
    "gateway_id": gateway_id,
    "gateway_url": gateway_url,
    "gateway_arn": gateway_arn,
    "gateway_role_arn": gateway_role_arn,
}
print(json.dumps(result))
PYEOF
)

GATEWAY_ID=$(echo "$GATEWAY_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['gateway_id'])")
GATEWAY_URL=$(echo "$GATEWAY_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['gateway_url'])")
GATEWAY_ARN=$(echo "$GATEWAY_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['gateway_arn'])")
GATEWAY_ROLE_ARN=$(echo "$GATEWAY_INFO" | python3 -c "import sys,json; print(json.load(sys.stdin)['gateway_role_arn'])")

echo "  Gateway ID:   $GATEWAY_ID"
echo "  Gateway URL:  $GATEWAY_URL"
echo "  Gateway ARN:  $GATEWAY_ARN"
echo "  Role ARN:     $GATEWAY_ROLE_ARN"
echo ""

export GATEWAY_ID GATEWAY_URL GATEWAY_ARN GATEWAY_ROLE_ARN

if [[ "$GATEWAY_ONLY" == true ]]; then
  update_env "$ENV_FILE" "AGENT_STUDIO_MCP_GATEWAY_URL" "$GATEWAY_URL"
  echo "Gateway-only mode. Done."
  exit 0
fi

# ============================================================
# Step 2: Generate target-catalog.json
# ============================================================
if [[ "$SKIP_CATALOG" == false ]]; then
  echo "[2/6] Generating target-catalog.json..."

  python3 << 'PYEOF'
import json, os, sys
import yaml, boto3

registry_file = os.environ["REGISTRY_FILE"]
bucket = os.environ["S3_BUCKET"]
region = os.environ["REGION"]

with open(registry_file) as f:
    registry = yaml.safe_load(f)

catalog = []

for target in registry.get("remote_targets", []):
    if not target.get("enabled", False):
        continue
    catalog.append({
        "name": target["name"],
        "description": target.get("description", ""),
        "category": target.get("category", "general"),
        "type": "remote",
    })

for target in registry.get("runtime_targets", []):
    if not target.get("enabled", False):
        continue
    if target.get("vpc_required", False):
        continue
    if target.get("deprecated", False):
        continue
    catalog.append({
        "name": target["name"],
        "description": target.get("description", ""),
        "category": target.get("category", "general"),
        "type": "runtime",
    })

s3 = boto3.client("s3", region_name=region)
body = json.dumps(catalog, indent=2, ensure_ascii=False)
s3.put_object(
    Bucket=bucket,
    Key="mcp/target-catalog.json",
    Body=body.encode("utf-8"),
    ContentType="application/json",
)
print(f"  Uploaded {len(catalog)} targets to s3://{bucket}/mcp/target-catalog.json")
PYEOF

  echo ""
else
  echo "[2/6] Skipping catalog generation (--skip-catalog)"
  echo ""
fi

# Counters
SUCCESS=0
FAILED=0
SKIPPED=0

# ============================================================
# Step 3: Deploy remote targets
# ============================================================
REMOTE_COUNT=$(echo "$TARGETS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['remote']))")
echo "[3/6] Deploying $REMOTE_COUNT remote target(s)..."

if [[ "$REMOTE_COUNT" -gt 0 ]]; then
for i in $(seq 0 $((REMOTE_COUNT - 1))); do
  TARGET_NAME=$(echo "$TARGETS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['remote'][$i]['name'])")
  TARGET_ENDPOINT=$(echo "$TARGETS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['remote'][$i]['endpoint'])")

  echo "  [$((i + 1))/$REMOTE_COUNT] $TARGET_NAME → $TARGET_ENDPOINT"

  RESULT=$(python3 << PYEOF
import json, os, sys, urllib.request
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials

region = os.environ["REGION"]
gateway_id = os.environ["GATEWAY_ID"]
gateway_role_arn = os.environ["GATEWAY_ROLE_ARN"]
target_name = "$TARGET_NAME"
target_endpoint = "$TARGET_ENDPOINT"

client = boto3.client("bedrock-agentcore-control", region_name=region)

# Check if target already exists
try:
    existing = client.list_gateway_targets(gatewayIdentifier=gateway_id)
    for t in existing.get("items", existing.get("targets", [])):
        if t.get("name") == target_name:
            print(json.dumps({"status": "skipped", "message": "already registered"}))
            sys.exit(0)
except Exception as e:
    print(f"    WARNING: list_gateway_targets failed: {e}", file=sys.stderr)

# Register target via raw SigV4 (SDK missing iamCredentialProvider field)
session = boto3.Session(region_name=region)
credentials = session.get_credentials().get_frozen_credentials()

body = json.dumps({
    "name": target_name,
    "targetConfiguration": {
        "mcp": {
            "mcpServer": {
                "endpoint": target_endpoint,
            }
        }
    },
    "credentialProviderConfigurations": [{
        "credentialProviderType": "GATEWAY_IAM_ROLE",
        "credentialProvider": {
            "iamCredentialProvider": {
                "roleArn": gateway_role_arn,
                "service": "bedrock-agentcore",
            }
        }
    }],
})

url = f"https://bedrock-agentcore-control.{region}.amazonaws.com/gateways/{gateway_id}/targets"
request = AWSRequest(method="POST", url=url, data=body, headers={
    "Content-Type": "application/json",
    "Accept": "application/json",
})
SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(request)

try:
    req = urllib.request.Request(
        url=url,
        data=body.encode("utf-8"),
        headers=dict(request.headers),
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    target_id = result.get("targetId", result.get("name", "unknown"))
    print(json.dumps({"status": "success", "target_id": target_id}))
except urllib.error.HTTPError as e:
    error_body = e.read().decode("utf-8", errors="replace")
    # Conflict = already exists
    if e.code == 409:
        print(json.dumps({"status": "skipped", "message": "already exists (409)"}))
    else:
        print(json.dumps({"status": "error", "message": f"HTTP {e.code}: {error_body}"}))
except Exception as e:
    print(json.dumps({"status": "error", "message": str(e)}))
PYEOF
  )

  STATUS=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")
  case "$STATUS" in
    success)
      echo "    Registered"
      SUCCESS=$((SUCCESS + 1))
      ;;
    skipped)
      MSG=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',''))" 2>/dev/null)
      echo "    Skipped ($MSG)"
      SKIPPED=$((SKIPPED + 1))
      ;;
    *)
      MSG=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message','unknown'))" 2>/dev/null)
      echo "    ERROR: $MSG"
      FAILED=$((FAILED + 1))
      ;;
  esac
done
fi
echo ""
# ============================================================
RUNTIME_COUNT=$(echo "$TARGETS_JSON" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['runtime']))")
echo "[4/6] Deploying $RUNTIME_COUNT runtime target(s)..."

if [[ "$RUNTIME_COUNT" -gt 0 ]]; then
for i in $(seq 0 $((RUNTIME_COUNT - 1))); do
  RT_NAME=$(echo "$TARGETS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['runtime'][$i]['name'])")
  RT_VERSION=$(echo "$TARGETS_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['runtime'][$i]['version'])")

  echo "  [$((i + 1))/$RUNTIME_COUNT] $RT_NAME (v$RT_VERSION)"

  # Check ECR image exists
  REPO_NAME="mcp-${RT_NAME}"
  ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO_NAME}:${RT_VERSION}"

  if ! aws ecr describe-images --repository-name "$REPO_NAME" --image-ids "imageTag=${RT_VERSION}" --region "$REGION" &>/dev/null; then
    echo "    ERROR: ECR image not found: $ECR_URI"
    echo "    Run 'scripts/build-mcp.sh --only $RT_NAME' first."
    FAILED=$((FAILED + 1))
    echo ""
    continue
  fi

  # Create/find runtime + register as gateway target
  RESULT=$(python3 << PYEOF
import json, os, sys, time, urllib.request, urllib.parse
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

import yaml as _yaml

region = os.environ["REGION"]
account_id = os.environ["ACCOUNT_ID"]
gateway_id = os.environ["GATEWAY_ID"]
gateway_role_arn = os.environ["GATEWAY_ROLE_ARN"]
registry_file = os.environ["REGISTRY_FILE"]
rt_name = "$RT_NAME"
rt_version = "$RT_VERSION"
ecr_uri = "$ECR_URI"

runtime_name = f"mcp_{rt_name}".replace("-", "_")  # AgentCore Runtime: [a-zA-Z][a-zA-Z0-9_]{0,47}
target_name = f"mcp-{rt_name}"                     # Gateway target: ([0-9a-zA-Z][-]?){1,100}
control = boto3.client("bedrock-agentcore-control", region_name=region)

# --- Find or create AgentCore Runtime ---
runtime_id = None
runtime_arn = None
runtime_status = None

# List existing runtimes, find by name
try:
    resp = control.list_agent_runtimes()
    for rt in resp.get("agentRuntimes", resp.get("agentRuntimeSummaries", resp.get("runtimes", []))):
        name = rt.get("agentRuntimeName", rt.get("name", ""))
        if name == runtime_name:
            runtime_id = rt.get("agentRuntimeId", rt.get("runtimeId", ""))
            break
    # Paginate if needed
    while not runtime_id and resp.get("nextToken"):
        resp = control.list_agent_runtimes(nextToken=resp["nextToken"])
        for rt in resp.get("agentRuntimes", resp.get("agentRuntimeSummaries", resp.get("runtimes", []))):
            name = rt.get("agentRuntimeName", rt.get("name", ""))
            if name == runtime_name:
                runtime_id = rt.get("agentRuntimeId", rt.get("runtimeId", ""))
                break
except Exception as e:
    print(f"    WARNING: list_agent_runtimes failed: {e}", file=sys.stderr)

# Per-target execution role: if the target declares an iam_policy in the
# registry, use the CDK-managed per-target role (AgentStudioMCP-{name}-{region}).
# Otherwise fall back to the shared basic role. This replaces the old
# single-canonical-role approach (see commit 12a9fc8 for history).
with open(registry_file) as _rf:
    _registry = _yaml.safe_load(_rf)
_target_has_iam_policy = False
for _t in _registry.get("runtime_targets", []):
    if _t.get("name") == rt_name:
        _ip = _t.get("iam_policy")
        if _ip and isinstance(_ip, dict) and _ip.get("Statement"):
            _target_has_iam_policy = True
        break

if _target_has_iam_policy:
    execution_role = f"arn:aws:iam::{account_id}:role/AgentStudioMCP-{rt_name}-{region}"
    print(f"    Using per-target role: {execution_role.split('/')[-1]}", file=sys.stderr)
else:
    execution_role = f"arn:aws:iam::{account_id}:role/AgentStudioSubAgent-basic-{region}"
    print(f"    Using shared basic role: {execution_role.split('/')[-1]}", file=sys.stderr)

if runtime_id:
    print(f"    Found existing runtime: {runtime_id}", file=sys.stderr)
    # Get current status
    rt_info = control.get_agent_runtime(agentRuntimeId=runtime_id)
    runtime_status = rt_info.get("status", "")
    runtime_arn = rt_info.get("agentRuntimeArn", "")
    existing_role = rt_info.get("roleArn", "")
    if existing_role and existing_role != execution_role:
        print(f"    Existing runtime has drifted roleArn (was {existing_role}); resetting to {execution_role}", file=sys.stderr)
    # Update if already exists (new image version)
    if runtime_status in ("READY", "ACTIVE"):
        try:
            control.update_agent_runtime(
                agentRuntimeId=runtime_id,
                roleArn=execution_role,
                networkConfiguration={"networkMode": rt_info["networkConfiguration"]["networkMode"]},
                protocolConfiguration={"serverProtocol": "MCP"},
                agentRuntimeArtifact={
                    "containerConfiguration": {"containerUri": ecr_uri}
                },
            )
            print(f"    Updated runtime with image {ecr_uri}", file=sys.stderr)
            runtime_status = "UPDATING"
        except Exception as e:
            print(f"    WARNING: update failed, using existing: {e}", file=sys.stderr)
else:
    # Create new runtime using the canonical execution_role defined above
    try:
        resp = control.create_agent_runtime(
            agentRuntimeName=runtime_name,
            description=f"MCP Server: {rt_name}",
            agentRuntimeArtifact={
                "containerConfiguration": {"containerUri": ecr_uri}
            },
            protocolConfiguration={"serverProtocol": "MCP"},
            networkConfiguration={"networkMode": "PUBLIC"},
            roleArn=execution_role,
        )
        runtime_id = resp["agentRuntimeId"]
        runtime_status = "CREATING"
        print(f"    Created runtime: {runtime_id}", file=sys.stderr)
    except control.exceptions.ConflictException:
        # Runtime exists but list didn't find it — look up by listing again
        print(f"    Runtime already exists, looking up...", file=sys.stderr)
        try:
            resp = control.list_agent_runtimes()
            for rt in resp.get("agentRuntimes", resp.get("agentRuntimeSummaries", resp.get("runtimes", []))):
                name = rt.get("agentRuntimeName", rt.get("name", ""))
                if name == runtime_name:
                    runtime_id = rt.get("agentRuntimeId", rt.get("runtimeId", ""))
                    break
            if not runtime_id:
                # Try paginating
                while resp.get("nextToken"):
                    resp = control.list_agent_runtimes(nextToken=resp["nextToken"])
                    for rt in resp.get("agentRuntimes", resp.get("agentRuntimeSummaries", resp.get("runtimes", []))):
                        name = rt.get("agentRuntimeName", rt.get("name", ""))
                        if name == runtime_name:
                            runtime_id = rt.get("agentRuntimeId", rt.get("runtimeId", ""))
                            break
                    if runtime_id:
                        break
        except Exception as e2:
            print(f"    WARNING: list retry failed: {e2}", file=sys.stderr)
        if runtime_id:
            rt_info = control.get_agent_runtime(agentRuntimeId=runtime_id)
            runtime_status = rt_info.get("status", "")
            runtime_arn = rt_info.get("agentRuntimeArn", "")
            print(f"    Found runtime: {runtime_id} ({runtime_status})", file=sys.stderr)
        else:
            print(json.dumps({"status": "error", "message": "Runtime exists but could not find its ID"}))
            sys.exit(0)
    except Exception as e:
        print(json.dumps({"status": "error", "message": f"create_agent_runtime failed: {e}"}))
        sys.exit(0)

# --- Wait for READY (timeout 10 min) ---
if runtime_status not in ("READY", "ACTIVE"):
    print(f"    Waiting for READY...", file=sys.stderr)
    for attempt in range(60):
        time.sleep(10)
        rt_info = control.get_agent_runtime(agentRuntimeId=runtime_id)
        runtime_status = rt_info.get("status", "")
        runtime_arn = rt_info.get("agentRuntimeArn", "")
        if runtime_status in ("READY", "ACTIVE"):
            print(f"    Runtime ready", file=sys.stderr)
            break
        if runtime_status in ("FAILED", "CREATE_FAILED", "DELETING"):
            print(json.dumps({"status": "error", "message": f"Runtime failed: {runtime_status}"}))
            sys.exit(0)
        if attempt % 6 == 5:
            print(f"    Still waiting ({runtime_status})...", file=sys.stderr)
    else:
        print(json.dumps({"status": "error", "message": "Runtime did not become READY in 10 minutes"}))
        sys.exit(0)

if not runtime_arn:
    rt_info = control.get_agent_runtime(agentRuntimeId=runtime_id)
    runtime_arn = rt_info.get("agentRuntimeArn", "")

# --- Build endpoint URL ---
encoded_arn = urllib.parse.quote(runtime_arn, safe="")
endpoint_url = f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{encoded_arn}/invocations?qualifier=DEFAULT"

# --- Register as Gateway target (SigV4) ---
# Check if already registered
try:
    existing_targets = control.list_gateway_targets(gatewayIdentifier=gateway_id)
    for t in existing_targets.get("items", existing_targets.get("targets", [])):
        if t.get("name") == target_name:
            print(json.dumps({"status": "skipped_target", "runtime_id": runtime_id, "message": "target already registered"}))
            sys.exit(0)
except Exception as e:
    print(f"    WARNING: list_gateway_targets failed: {e}", file=sys.stderr)

session = boto3.Session(region_name=region)
credentials = session.get_credentials().get_frozen_credentials()

body = json.dumps({
    "name": target_name,
    "targetConfiguration": {
        "mcp": {
            "mcpServer": {
                "endpoint": endpoint_url,
            }
        }
    },
    "credentialProviderConfigurations": [{
        "credentialProviderType": "GATEWAY_IAM_ROLE",
        "credentialProvider": {
            "iamCredentialProvider": {
                "roleArn": gateway_role_arn,
                "service": "bedrock-agentcore",
            }
        }
    }],
})

url = f"https://bedrock-agentcore-control.{region}.amazonaws.com/gateways/{gateway_id}/targets"
request = AWSRequest(method="POST", url=url, data=body, headers={
    "Content-Type": "application/json",
    "Accept": "application/json",
})
SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(request)

try:
    req = urllib.request.Request(
        url=url,
        data=body.encode("utf-8"),
        headers=dict(request.headers),
        method="POST",
    )
    with urllib.request.urlopen(req) as resp:
        result = json.loads(resp.read())
    print(json.dumps({"status": "success", "runtime_id": runtime_id}))
except urllib.error.HTTPError as e:
    error_body = e.read().decode("utf-8", errors="replace")
    if e.code == 409:
        print(json.dumps({"status": "skipped_target", "runtime_id": runtime_id, "message": "target already exists (409)"}))
    else:
        print(json.dumps({"status": "error", "message": f"Target registration HTTP {e.code}: {error_body}"}))
except Exception as e:
    print(json.dumps({"status": "error", "message": f"Target registration failed: {e}"}))
PYEOF
  )

  STATUS=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','error'))" 2>/dev/null || echo "error")
  case "$STATUS" in
    success)
      echo "    Runtime deployed + target registered"
      SUCCESS=$((SUCCESS + 1))
      ;;
    skipped_target)
      MSG=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message',''))" 2>/dev/null)
      echo "    Runtime OK, target skipped ($MSG)"
      SKIPPED=$((SKIPPED + 1))
      ;;
    *)
      MSG=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('message','unknown'))" 2>/dev/null)
      echo "    ERROR: $MSG"
      FAILED=$((FAILED + 1))
      ;;
  esac
  echo ""
done
fi

# ============================================================
# Step 5: Write Gateway URL to .env
# ============================================================
echo "[5/6] Updating .env + S3 config..."
update_env "$ENV_FILE" "AGENT_STUDIO_MCP_GATEWAY_URL" "$GATEWAY_URL"
update_env "$ENV_FILE" "AGENT_STUDIO_MCP_GATEWAY_ID" "$GATEWAY_ID"
echo "  AGENT_STUDIO_MCP_GATEWAY_URL=$GATEWAY_URL"
echo "  AGENT_STUDIO_MCP_GATEWAY_ID=$GATEWAY_ID"

# Upload gateway URL to S3 for Meta-Agent (codeConfiguration runtimes can't read env vars)
echo "$GATEWAY_URL" | aws s3 cp - "s3://${S3_BUCKET}/config/mcp_gateway_url.txt" \
  --content-type text/plain --region "$REGION"
echo "  Uploaded gateway URL to s3://${S3_BUCKET}/config/mcp_gateway_url.txt"

# Upload registry to S3 for Meta-Agent endpoint resolution (remote vs runtime)
aws s3 cp "$REGISTRY_FILE" "s3://${S3_BUCKET}/mcp-runtime/mcp-registry.yaml" \
  --content-type text/yaml --region "$REGION"
echo "  Uploaded registry to s3://${S3_BUCKET}/mcp-runtime/mcp-registry.yaml"
echo ""

# ============================================================
# Step 5b: Generate per-target tool manifests from Gateway
# ============================================================
if [[ "$SKIP_CATALOG" == false ]]; then
  echo "[5b/6] Generating tool manifests..."

  python3 << 'PYEOF'
import json, os, sys, urllib.request, urllib.parse
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

region = os.environ["REGION"]
bucket = os.environ["S3_BUCKET"]
gateway_url = os.environ["GATEWAY_URL"]

session = boto3.Session(region_name=region)
credentials = session.get_credentials().get_frozen_credentials()

# Paginate tools/list from Gateway
all_tools = []
cursor = None
for page in range(30):
    body = json.dumps({"jsonrpc": "2.0", "id": str(page), "method": "tools/list",
                        "params": {"cursor": cursor} if cursor else {}})
    req = AWSRequest(method="POST", url=gateway_url, data=body, headers={
        "Content-Type": "application/json", "Accept": "application/json, text/event-stream"})
    SigV4Auth(credentials, "bedrock-agentcore", region).add_auth(req)
    try:
        http_req = urllib.request.Request(url=gateway_url, data=body.encode(),
                                          headers=dict(req.headers), method="POST")
        with urllib.request.urlopen(http_req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
        page_tools = result.get("result", {}).get("tools", [])
        all_tools.extend(page_tools)
        cursor = result.get("result", {}).get("nextCursor")
        if not cursor:
            break
    except Exception as e:
        print(f"  WARNING: tools/list page {page} failed: {e}", file=sys.stderr)
        break

print(f"  Fetched {len(all_tools)} tools from Gateway")

# Group by target prefix (target___toolname) — keep name + description
by_target = {}
for tool in all_tools:
    full_name = tool.get("name", "")
    desc = tool.get("description", "")
    if "___" in full_name:
        prefix, tool_name = full_name.split("___", 1)
        by_target.setdefault(prefix, []).append({"name": tool_name, "description": desc[:200]})
    else:
        by_target.setdefault("_ungrouped", []).append({"name": full_name, "description": desc[:200]})

# Upload per-target manifests to S3
s3 = boto3.client("s3", region_name=region)
for target, tools in by_target.items():
    if target == "_ungrouped":
        continue
    tools.sort(key=lambda t: t["name"])
    body = json.dumps(tools, ensure_ascii=False)
    s3.put_object(Bucket=bucket, Key=f"mcp/target-tools/{target}.json",
                  Body=body.encode(), ContentType="application/json")

print(f"  Uploaded manifests for {len(by_target) - (1 if '_ungrouped' in by_target else 0)} targets")
PYEOF

  echo ""
fi

# ============================================================
# Step 6: Summary
# ============================================================
TOTAL=$((SUCCESS + FAILED + SKIPPED))
echo "[6/6] Deploy summary"
echo "=========================================="
echo "  Gateway:  $GATEWAY_ID"
echo "  URL:      $GATEWAY_URL"
echo "  Total:    $TOTAL targets"
echo "  Success:  $SUCCESS"
echo "  Skipped:  $SKIPPED (already existed)"
echo "  Failed:   $FAILED"
echo "=========================================="

if [[ $FAILED -gt 0 ]]; then
  echo ""
  echo "Some targets failed. Check logs above."
  exit 1
fi

# ============================================================
# Step 7: Role-drift audit (converge any runtime whose roleArn
# was left behind by a prior rename, e.g. commit 12a9fc8)
# ============================================================
echo ""
echo "[7/7] MCP runtime role-drift audit"
python3 "${SCRIPT_DIR}/check-mcp-runtime-roles.py" --region "$REGION" --fix || {
  echo "WARNING: role-drift audit reported errors. Re-run scripts/check-mcp-runtime-roles.py manually."
}

echo ""
echo "MCP Gateway deployment complete."
