#!/usr/bin/env bash
# Deploy Meta-Agent to AgentCore Runtime.
# Supports both create (first time) and update (subsequent).
# Reads configuration from .env in the project root, or from environment variables.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

# Load .env if it exists
if [[ -f "$ENV_FILE" ]]; then
  set -a; source "$ENV_FILE"; set +a
fi

# Validate required vars
REGION="${AGENT_STUDIO_REGION:?AGENT_STUDIO_REGION is required}"
ACCOUNT_ID="${AGENT_STUDIO_ACCOUNT_ID:?AGENT_STUDIO_ACCOUNT_ID is required}"
BUCKET="${AGENT_STUDIO_S3_BUCKET:?AGENT_STUDIO_S3_BUCKET is required}"
META_AGENT_ID="${AGENT_STUDIO_META_AGENT_ID:-}"
ROLE_ARN="${AGENT_STUDIO_ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/AgentStudioMetaAgent-${REGION}}"

echo "=== Agent Studio Deploy ==="
echo "Region:  $REGION"
echo "Account: $ACCOUNT_ID"
echo "Bucket:  $BUCKET"
echo "Meta-Agent: ${META_AGENT_ID:-<will be created>}"
echo ""

# --- 1. Initialize skills/index.json if missing ---
echo "[1/3] Checking skills/index.json..."
if aws s3api head-object --bucket "$BUCKET" --key "skills/index.json" --region "$REGION" 2>/dev/null; then
  echo "  skills/index.json exists, skipping."
else
  echo "  Creating empty skills/index.json..."
  echo '[]' | aws s3 cp - "s3://${BUCKET}/skills/index.json" \
    --content-type application/json --region "$REGION"
  echo "  Done."
fi

# --- 2. Package Meta-Agent ---
echo ""
echo "[2/3] Packaging Meta-Agent..."
cd "${PROJECT_ROOT}/meta-agent"

python3 - <<'PYEOF'
import io, os, zipfile
import boto3

region = os.environ["AGENT_STUDIO_REGION"]
bucket = os.environ["AGENT_STUDIO_S3_BUCKET"]
agent_id = os.environ.get("AGENT_STUDIO_META_AGENT_ID", "")
source_dir = os.getcwd()

print(f"  Packaging {source_dir}...")

# Download base zip
s3 = boto3.client("s3", region_name=region)
base_key = "base/deployment.zip"
print(f"  Downloading base zip from s3://{bucket}/{base_key}...")
base_resp = s3.get_object(Bucket=bucket, Key=base_key)
base_data = base_resp["Body"].read()
print(f"  Base zip: {len(base_data) / 1024 / 1024:.1f} MB")

# Collect source files
source_files = {}
for root, dirs, files in os.walk(source_dir):
    dirs[:] = [d for d in dirs if d not in {
        "__pycache__", ".venv", ".git", ".bedrock_agentcore",
        "agent_studio_meta_agent.egg-info", "node_modules",
    }]
    for f in files:
        if f.endswith((".pyc", ".egg-info")):
            continue
        full = os.path.join(root, f)
        arcname = os.path.relpath(full, source_dir)
        source_files[arcname] = full

# Clone base zip + overlay source files
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
print(f"  Package size: {len(package) / 1024 / 1024:.1f} MB ({len(source_files)} source files)")

# Upload — use agent_id if known, otherwise use the runtime name
s3_name = agent_id if agent_id else "agentStudioMeta"
s3_key = f"agents/{s3_name}/deployment.zip"
s3.put_object(Bucket=bucket, Key=s3_key, Body=package)
print(f"  Uploaded to s3://{bucket}/{s3_key}")

# Also upload to the fixed CfnRuntime key (keeps CDK artifact fresh)
fixed_key = "agents/agentStudioMeta/deployment.zip"
if s3_key != fixed_key:
    s3.put_object(Bucket=bucket, Key=fixed_key, Body=package)
    print(f"  Also uploaded to s3://{bucket}/{fixed_key}")
PYEOF

# --- 3. Create or Update Runtime ---
echo ""
echo "[3/3] Deploying Meta-Agent..."
cd "${PROJECT_ROOT}/meta-agent"

META_AGENT_ID=$(python3 - <<'PYEOF'
import json, os, time, sys
import boto3

region = os.environ["AGENT_STUDIO_REGION"]
bucket = os.environ["AGENT_STUDIO_S3_BUCKET"]
agent_id = os.environ.get("AGENT_STUDIO_META_AGENT_ID", "")
role_arn = os.environ.get("AGENT_STUDIO_ROLE_ARN",
    f"arn:aws:iam::{os.environ['AGENT_STUDIO_ACCOUNT_ID']}:role/AgentStudioMetaAgent-{region}")

control = boto3.client("bedrock-agentcore-control", region_name=region)

# Auto-detect: create or update
if agent_id:
    try:
        existing = control.get_agent_runtime(agentRuntimeId=agent_id)
        mode = "update"
    except control.exceptions.ResourceNotFoundException:
        print(f"  Runtime {agent_id} not found, will create new.", file=sys.stderr)
        agent_id = ""
        mode = "create"
    except Exception as e:
        if "ResourceNotFoundException" in str(type(e).__name__) or "not found" in str(e).lower():
            agent_id = ""
            mode = "create"
        else:
            raise
else:
    mode = "create"

_aid = agent_id or "agentStudioMeta"
_account = os.environ["AGENT_STUDIO_ACCOUNT_ID"]
env_vars = {
    "AGENT_STUDIO_REGION": region,
    # AgentCore Observability via ADOT — emits gen_ai.* spans to aws/spans.
    # AgentCore's data plane captures OTLP from the runtime pod using the
    # x-aws-log-group header; no sidecar collector exists on localhost:4318,
    # so OTEL_EXPORTER_OTLP_ENDPOINT must remain unset.
    "AGENT_OBSERVABILITY_ENABLED": "true",
    "OTEL_PYTHON_DISTRO": "aws_distro",
    "OTEL_PYTHON_CONFIGURATOR": "aws_configurator",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_TRACES_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_METRICS_EXPORTER": "awsemf",
    "OTEL_RESOURCE_ATTRIBUTES": (
        f"service.name={_aid},"
        f"aws.log.group.names=/aws/bedrock-agentcore/runtimes/{_aid}-DEFAULT,"
        f"cloud.resource_id=arn:aws:bedrock-agentcore:{region}:{_account}:runtime/{_aid}"
    ),
    "OTEL_EXPORTER_OTLP_LOGS_HEADERS": (
        f"x-aws-log-group=/aws/bedrock-agentcore/runtimes/{_aid}-DEFAULT,"
        f"x-aws-log-stream=runtime-logs,"
        f"x-aws-metric-namespace=bedrock-agentcore"
    ),
}
for _k in (
    "AGENT_STUDIO_CODE_INTERPRETER_ID",
    "AGENT_STUDIO_BROWSER_ID",
    "AGENT_STUDIO_CLOUDFRONT_DOMAIN",
    "AGENT_STUDIO_A2A_INVOKE_URL",
):
    _v = os.environ.get(_k, "")
    if _v:
        env_vars[_k] = _v

if mode == "update":
    s3_key = f"agents/{agent_id}/deployment.zip"
    existing = control.get_agent_runtime(agentRuntimeId=agent_id)
    control.update_agent_runtime(
        agentRuntimeId=agent_id,
        roleArn=role_arn,
        networkConfiguration={"networkMode": existing["networkConfiguration"]["networkMode"]},
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": bucket, "prefix": s3_key}},
                "runtime": "PYTHON_3_10",
                "entryPoint": ["main.py"],
            }
        },
        environmentVariables=env_vars,
    )
    print(f"  Update triggered for {agent_id}", file=sys.stderr)
else:
    s3_key = "agents/agentStudioMeta/deployment.zip"
    resp = control.create_agent_runtime(
        agentRuntimeName="agentStudioMeta",
        description="Agent Studio Meta-Agent",
        roleArn=role_arn,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": bucket, "prefix": s3_key}},
                "runtime": "PYTHON_3_10",
                "entryPoint": ["main.py"],
            }
        },
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
        filesystemConfigurations=[{
            "sessionStorage": {"mountPath": "/mnt/workspace"}
        }],
        environmentVariables=env_vars,
    )
    agent_id = resp["agentRuntimeId"]
    print(f"  Created runtime: {agent_id}", file=sys.stderr)

# Wait for READY (AgentCore returns CREATING | CREATE_FAILED | UPDATING |
# UPDATE_FAILED | READY | DELETING per boto3 service model).
print("  Waiting for READY...", end="", flush=True, file=sys.stderr)
for _ in range(30):
    time.sleep(10)
    resp = control.get_agent_runtime(agentRuntimeId=agent_id)
    status = resp["status"]
    print(".", end="", flush=True, file=sys.stderr)
    if status == "READY":
        print(f"\n  Status: {status}", file=sys.stderr)
        break
    if status in ("CREATE_FAILED", "UPDATE_FAILED", "DELETING"):
        print(f"\n  Status: {status} — deployment failed!", file=sys.stderr)
        sys.exit(1)
else:
    print("\n  TIMEOUT — agent did not become READY in 5 minutes", file=sys.stderr)
    sys.exit(1)

# Output agent ID to stdout (for deploy-all.sh to capture)
print(agent_id)
PYEOF
)

echo "  Meta-Agent deployed: $META_AGENT_ID"

# --- Ensure Meta-Agent role has required permissions ---
echo ""
echo "[4/4] Ensuring Meta-Agent IAM permissions..."
python3 - <<'PYEOF'
import boto3, json, os

region = os.environ["AGENT_STUDIO_REGION"]
account_id = os.environ["AGENT_STUDIO_ACCOUNT_ID"]
agent_id = os.environ.get("AGENT_STUDIO_META_AGENT_ID", "")
if not agent_id:
    print("  Skipping — no META_AGENT_ID")
    exit(0)

control = boto3.client("bedrock-agentcore-control", region_name=region)
rt = control.get_agent_runtime(agentRuntimeId=agent_id)
role_arn = rt["roleArn"]
role_name = role_arn.split("/")[-1]
print(f"  Role: {role_name}")

iam = boto3.client("iam")

# Workspaces table read (for MCP policy check)
iam.put_role_policy(
    RoleName=role_name,
    PolicyName="AgentStudioWorkspacesTableRead",
    PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["dynamodb:GetItem", "dynamodb:Query"],
            "Resource": [
                f"arn:aws:dynamodb:{region}:{account_id}:table/agent-studio-workspaces",
                f"arn:aws:dynamodb:{region}:{account_id}:table/agent-studio-workspaces/index/*",
            ]
        }]
    }),
)
print("  Added: workspaces table read")

# MCP config read from S3 (for MCP_GATEWAY_URL fallback + registry)
iam.put_role_policy(
    RoleName=role_name,
    PolicyName="AgentStudioMcpConfigRead",
    PolicyDocument=json.dumps({
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Action": ["s3:GetObject"],
            "Resource": [
                f"arn:aws:s3:::{os.environ['AGENT_STUDIO_S3_BUCKET']}/config/*",
                f"arn:aws:s3:::{os.environ['AGENT_STUDIO_S3_BUCKET']}/mcp-runtime/*",
            ]
        }]
    }),
)
print("  Added: MCP config S3 read")
PYEOF

export AGENT_STUDIO_META_AGENT_ID="$META_AGENT_ID"

echo ""
echo "=== Deploy complete ==="
echo "META_AGENT_ID=$META_AGENT_ID"
