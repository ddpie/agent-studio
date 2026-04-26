"""Workspace IAM role helpers shared across Meta-Agent tools.

Provides helpers to look up workspace-level IAM roles from DynamoDB and
decide which role an agent should assume at deploy time. Workspace custom
roles (``AgentStudio-ws-{id}-{region}``) are created on demand by the
CRUD Lambda when an admin enables IAM isolation; most workspaces continue
to use the shared ``AgentStudioSubAgent-basic`` role.

Functions here are intentionally thin wrappers around DDB reads — no
boto3 client caching, no retry logic. The callers (create_agent,
update_agent, check_workspace_permissions) already run inside short-lived
tool invocations and tolerate one-off latency.
"""

import boto3

from config import REGION, SUB_AGENT_ROLE_ARN
from tools._scope import _WORKSPACES_TABLE


def _get_workspace_role_arn(workspace_id: str) -> str | None:
    """Get workspace custom role ARN from DDB. Returns None if no custom role."""
    if not workspace_id:
        return None
    try:
        ddb = boto3.resource("dynamodb", region_name=REGION)
        table = ddb.Table(_WORKSPACES_TABLE)
        item = table.get_item(Key={"workspaceId": workspace_id, "sk": "META"}).get("Item", {})
        return item.get("roleArn") or None
    except Exception:
        return None


def _get_agent_role_arn(workspace_id: str) -> str:
    """Workspace has custom role -> use it; else fallback to shared role.

    This is the single point of truth for "which IAM role does an agent
    created/updated in this workspace assume?" Used by create_agent and
    update_agent so both paths stay consistent.
    """
    custom = _get_workspace_role_arn(workspace_id)
    return custom if custom else SUB_AGENT_ROLE_ARN
