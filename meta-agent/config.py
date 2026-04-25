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
# agent role does not, so it can't be reused here.
SCHEDULER_TARGET_ROLE_ARN = os.getenv(
    "SCHEDULER_TARGET_ROLE_ARN",
    f"arn:aws:iam::{ACCOUNT_ID}:role/AgentStudioSchedulerTargetRole-{REGION}",
)
BASE_DEPLOYMENT_KEY = "base/deployment.zip"
# Agents use a fatter base layer that includes Playwright + strands-agents-tools
# (for browser_use tool). Meta-Agent stays on the slim base to keep cold-start
# inside the 30s AgentCore runtime init budget.
SUB_AGENT_BASE_DEPLOYMENT_KEY = os.getenv(
    "SUB_AGENT_BASE_DEPLOYMENT_KEY", "base/agent-deployment.zip"
)
MODEL_ID = os.getenv("AGENT_STUDIO_MODEL_ID", "us.anthropic.claude-opus-4-7")
AGENTS_TABLE = os.getenv("AGENT_STUDIO_AGENTS_TABLE", "agent-studio-agents")
TOOLS_TABLE = os.getenv("AGENT_STUDIO_TOOLS_TABLE", "agent-studio-tools")

# Agent role — single unified role for all agents
SUB_AGENT_ROLE_ARN = f"arn:aws:iam::{ACCOUNT_ID}:role/AgentStudioSubAgent-basic-{REGION}"

# Legacy tier mapping — all tiers now resolve to the unified agent role.
# Kept for backward compat with existing agents that have permissionTier stored.
PERMISSION_TIER_ROLES = {
    "basic": SUB_AGENT_ROLE_ARN,
    "readonly": SUB_AGENT_ROLE_ARN,
    "data-access": SUB_AGENT_ROLE_ARN,
}
DEFAULT_PERMISSION_TIER = "readonly"

CODE_INTERPRETER_ID = os.environ.get("AGENT_STUDIO_CODE_INTERPRETER_ID", "")
BROWSER_ID = os.environ.get("AGENT_STUDIO_BROWSER_ID", "")

# Max output tokens per model family.
# Bedrock API doesn't expose this; values from Anthropic docs + runtime errors.
# Key: substring matched against model_id (first match wins, checked in order).
_MAX_TOKENS_TABLE = [
    ("opus-4-7",    128000),   # Claude Opus 4.7
    ("opus-4-6",    128000),   # Claude Opus 4.6
    ("opus-4-5",    32000),    # Claude Opus 4.5
    ("opus-4-1",    32000),    # Claude Opus 4.1
    ("opus",        128000),   # Opus fallback (future versions)
    ("sonnet-4-6",  65536),    # Claude Sonnet 4.6
    ("sonnet-4-5",  16384),    # Claude Sonnet 4.5
    ("sonnet-4",    65536),    # Claude Sonnet 4 / 4.x fallback
    ("sonnet-3-5",  8192),     # Claude 3.5 Sonnet
    ("sonnet",      65536),    # Sonnet fallback
    ("haiku-4-5",   16384),    # Claude Haiku 4.5
    ("haiku-3",     4096),     # Claude 3 Haiku
    ("haiku",       16384),    # Haiku fallback
]


def get_max_tokens(model_id: str) -> int:
    """Return the max output tokens for a Bedrock model ID."""
    mid = model_id.lower()
    for pattern, limit in _MAX_TOKENS_TABLE:
        if pattern in mid:
            return limit
    return 16384  # conservative default for unknown models


MCP_GATEWAY_URL = os.getenv("MCP_GATEWAY_URL", "") or os.getenv(
    "AGENT_STUDIO_MCP_GATEWAY_URL", ""
)
if not MCP_GATEWAY_URL:
    try:
        import boto3 as _b3
        _resp = _b3.client("s3", region_name=REGION).get_object(
            Bucket=S3_BUCKET, Key="config/mcp_gateway_url.txt"
        )
        MCP_GATEWAY_URL = _resp["Body"].read().decode().strip()
    except Exception:
        pass
