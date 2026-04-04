"""Shared configuration for agent-studio Meta-Agent."""

import os

REGION = os.getenv("AWS_REGION", "us-east-1")
ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID", "557690613480")
S3_BUCKET = os.getenv(
    "AGENT_STUDIO_S3_BUCKET",
    f"bedrock-agentcore-codebuild-sources-{ACCOUNT_ID}-{REGION}",
)
AGENT_ROLE_ARN = os.getenv(
    "AGENT_STUDIO_ROLE_ARN",
    f"arn:aws:iam::{ACCOUNT_ID}:role/AgentStudioSubAgentRole-{REGION}",
)
BASE_DEPLOYMENT_KEY = "base/deployment.zip"
MODEL_ID = os.getenv("AGENT_STUDIO_MODEL_ID", "us.anthropic.claude-sonnet-4-20250514-v1:0")
AGENTS_TABLE = os.getenv("AGENT_STUDIO_AGENTS_TABLE", "agent-studio-agents")
TOOLS_TABLE = os.getenv("AGENT_STUDIO_TOOLS_TABLE", "agent-studio-tools")
TOOLS_TABLE = os.getenv("AGENT_STUDIO_TOOLS_TABLE", "agent-studio-tools")

# Permission tier → IAM Role mapping
PERMISSION_TIER_ROLES = {
    "basic": f"arn:aws:iam::{ACCOUNT_ID}:role/AgentStudioSubAgent-basic-{REGION}",
    "readonly": f"arn:aws:iam::{ACCOUNT_ID}:role/AgentStudioSubAgentRole-{REGION}",
    "data-access": f"arn:aws:iam::{ACCOUNT_ID}:role/AgentStudioSubAgent-dataaccess-{REGION}",
}
DEFAULT_PERMISSION_TIER = "readonly"
