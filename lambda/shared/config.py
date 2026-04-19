"""Lambda config — reads from env vars set by CDK."""
import os

REGION = os.environ.get("AWS_REGION", "us-east-1")
S3_BUCKET = os.environ.get("S3_BUCKET", "")
ASSETS_BUCKET = os.environ.get("S3_BUCKET", "")
WORKSPACES_TABLE = os.environ.get("WORKSPACES_TABLE", "")
AGENTS_TABLE = os.environ.get("AGENTS_TABLE", "")
SKILLS_TABLE = os.environ.get("SKILLS_TABLE", "")
TOOLS_TABLE = os.environ.get("TOOLS_TABLE", "")
COGNITO_USER_POOL_ID = os.environ.get("COGNITO_USER_POOL_ID", "")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "")
META_AGENT_ARN = os.environ.get("META_AGENT_ARN", "")
EVALUATOR_ROLE_ARN = os.environ.get("EVALUATOR_ROLE_ARN", "")
SPANS_LOG_GROUP = os.environ.get("SPANS_LOG_GROUP", "aws/spans")
