"""Shared configuration for agent-studio Meta-Agent."""

import os

REGION = os.getenv("AWS_REGION", "us-east-1")
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")
if not ACCOUNT_ID:
    try:
        import boto3
        ACCOUNT_ID = boto3.client("sts", region_name=REGION).get_caller_identity()["Account"]
    except Exception:
        ACCOUNT_ID = ""
S3_BUCKET = os.getenv(
    "AGENT_STUDIO_S3_BUCKET",
    f"bedrock-agentcore-codebuild-sources-{ACCOUNT_ID}-{REGION}",
)
AGENT_ROLE_ARN = os.getenv(
    "AGENT_STUDIO_ROLE_ARN",
    f"arn:aws:iam::{ACCOUNT_ID}:role/AgentStudioMetaAgent-{REGION}",
)
# Dedicated role assumed by EventBridge Scheduler to invoke agent
# runtimes. Trust policy MUST allow scheduler.amazonaws.com — the
# sub-agent role does not, so it can't be reused here.
SCHEDULER_TARGET_ROLE_ARN = os.getenv(
    "SCHEDULER_TARGET_ROLE_ARN",
    f"arn:aws:iam::{ACCOUNT_ID}:role/AgentStudioSchedulerTargetRole-{REGION}",
)
BASE_DEPLOYMENT_KEY = "base/deployment.zip"
MODEL_ID = os.getenv("AGENT_STUDIO_MODEL_ID", "us.anthropic.claude-opus-4-7")
AGENTS_TABLE = os.getenv("AGENT_STUDIO_AGENTS_TABLE", "agent-studio-agents")
TOOLS_TABLE = os.getenv("AGENT_STUDIO_TOOLS_TABLE", "agent-studio-tools")

# Sub-agent role — single unified role for all sub-agents
SUB_AGENT_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/AgentStudioSubAgent-basic-{REGION}"

# Legacy tier mapping — all tiers now resolve to the unified sub-agent role.
# Kept for backward compat with existing agents that have permissionTier stored.
PERMISSION_TIER_ROLES = {
    "basic": SUB_AGENT_ROLE_ARN,
    "readonly": SUB_AGENT_ROLE_ARN,
    "data-access": SUB_AGENT_ROLE_ARN,
}
DEFAULT_PERMISSION_TIER = "readonly"

CODE_INTERPRETER_ID = os.environ.get("AGENT_STUDIO_CODE_INTERPRETER_ID", "")
BROWSER_ID = os.environ.get("AGENT_STUDIO_BROWSER_ID", "")

MCP_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "")
if not MCP_GATEWAY_URL:
    try:
        import boto3 as _b3
        _resp = _b3.client("s3", region_name=REGION).get_object(
            Bucket=S3_BUCKET, Key="config/mcp_gateway_url.txt"
        )
        MCP_GATEWAY_URL = _resp["Body"].read().decode().strip()
    except Exception:
        pass
