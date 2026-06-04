#!/usr/bin/env bash
# Deploy Meta-Agent to AgentCore Runtime.
# Supports both create (first time) and update (subsequent).
# Reads configuration from .env in the project root, or from environment variables.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${PROJECT_ROOT}/.env"

# Shared .env helpers
source "${SCRIPT_DIR}/lib/env-utils.sh"

# Load .env if it exists (safe: no shell expansion of values)
safe_source_env "$ENV_FILE"

# Validate required vars
REGION="${AGENT_STUDIO_REGION:?AGENT_STUDIO_REGION is required}"
ACCOUNT_ID="${AGENT_STUDIO_ACCOUNT_ID:?AGENT_STUDIO_ACCOUNT_ID is required}"
BUCKET="${AGENT_STUDIO_S3_BUCKET:?AGENT_STUDIO_S3_BUCKET is required}"
META_AGENT_ID="${AGENT_STUDIO_META_AGENT_ID:-}"
export ROLE_ARN="${AGENT_STUDIO_ROLE_ARN:-arn:aws:iam::${ACCOUNT_ID}:role/AgentStudioMetaAgent-${REGION}}"

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
base_mb = len(base_data) / 1024 / 1024
print(f"  Base zip: {base_mb:.1f} MB")

# Sanity check: a valid base zip is ~200 MB (Python deps + Kiro binary).
# If something truncated it to a near-empty placeholder (we hit this
# after a CDK BucketDeployment Lambda ran out of ephemeral storage and
# uploaded a 154-byte stub), every downstream agent container will
# ImportError on startup and AgentCore returns the deceptive
# "Runtime initialization time exceeded" error. Fail loudly here
# instead of shipping a broken package.
if len(base_data) < 10 * 1024 * 1024:  # 10 MB
    import sys as _sys
    print(
        f"ERROR: base zip is only {base_mb:.2f} MB — expected > 10 MB. "
        f"It was likely overwritten by a broken CDK BucketDeployment or a "
        f"manual upload. Recover it with: "
        f"aws s3 cp base/deployment.zip s3://{bucket}/{base_key} --region {region}",
        file=_sys.stderr,
    )
    _sys.exit(2)

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
    # Explicit S3 bucket + account id. config.py has a STS/region fallback,
    # but kiro_home.py forwards these verbatim into the MCP stdio subprocess
    # env — any empty value there turns every tool that touches S3 into a
    # "Invalid bucket name" failure.
    "AGENT_STUDIO_S3_BUCKET": bucket,
    "AGENT_STUDIO_ACCOUNT_ID": _account,
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
    # Needed by list_mcp_servers / list_mcp_target_tools. Without this,
    # those tools fail immediately with "gateway id not configured".
    "AGENT_STUDIO_MCP_GATEWAY_ID",
    # config.py reads MCP_GATEWAY_URL first, then falls back to
    # s3://bucket/config/mcp_gateway_url.txt. Forwarding it saves one S3
    # GetObject per cold-start of the MCP stdio subprocess.
    "AGENT_STUDIO_MCP_GATEWAY_URL",
    # Kiro model default is still env-configured; the API key moved to
    # per-workspace Secrets Manager (hydrated at invoke time by the
    # Invoke Lambda) so it no longer ships in Runtime env at all.
    "AGENT_STUDIO_KIRO_MODEL",
    # Knowledge Base infrastructure references
    "AGENT_STUDIO_KB_TABLE",
    "AGENT_STUDIO_KB_SERVICE_ROLE_ARN",
    "AGENT_STUDIO_VECTORS_BUCKET",
):
    _v = os.environ.get(_k, "")
    if _v:
        env_vars[_k] = _v

# Admin-only fallback: if KIRO_API_KEY is present in the deployer's shell
# env we forward it as a last-resort credential the Runtime uses when no
# per-workspace key is configured. Leave unset in prod; users must
# configure per-workspace keys via the Settings UI.
_kiro_fallback = os.environ.get("AGENT_STUDIO_KIRO_API_KEY", "")
if _kiro_fallback:
    env_vars["KIRO_API_KEY"] = _kiro_fallback
    print(
        "  NOTE: AGENT_STUDIO_KIRO_API_KEY is set; forwarding as admin "
        "fallback. Workspaces should configure their own keys in Settings.",
        file=sys.stderr,
    )

# /mnt/kiro is where AgentCore mounts per-runtimeSessionId persistent storage
# for the Meta-Agent pod. ensure_kiro_home() writes the custom agent config
# and Kiro's own sessions/cli/ files into this mount, so turn N can
# session/load whatever turn N-1 persisted. AgentCore accepts max 1
# filesystemConfigurations entry.
_filesystem_configs = [{"sessionStorage": {"mountPath": "/mnt/kiro"}}]

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
        # UpdateAgentRuntime treats omitted fields as "clear". We explicitly
        # re-send the sessionStorage mount every update so the Kiro backend
        # doesn't lose its /mnt/kiro on prompt or code bumps.
        filesystemConfigurations=_filesystem_configs,
        environmentVariables=env_vars,
    )
    print(f"  Update triggered for {agent_id}", file=sys.stderr)
else:
    s3_key = "agents/agentStudioMeta/deployment.zip"
    network_mode = os.environ.get("AGENT_STUDIO_NETWORK_MODE", "PUBLIC")
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
        networkConfiguration={"networkMode": network_mode},
        protocolConfiguration={"serverProtocol": "HTTP"},
        filesystemConfigurations=_filesystem_configs,
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

# Export the (possibly new) agent ID so step 4's Python can see it
export AGENT_STUDIO_META_AGENT_ID="$META_AGENT_ID"

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

# Persist the agent ID back to .env so subsequent runs are idempotent
if [[ -f "$ENV_FILE" ]]; then
  update_env "$ENV_FILE" "AGENT_STUDIO_META_AGENT_ID" "$META_AGENT_ID"
fi

echo ""
echo "=== Deploy complete ==="
echo "META_AGENT_ID=$META_AGENT_ID"
