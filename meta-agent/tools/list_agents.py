"""list_agents — List agents in the caller's workspace."""

import json

import boto3
from config import REGION
from strands import tool

from tools._scope import ROLE_VIEWER, list_workspace_agents, require_role


@tool
def list_agents() -> str:
    """List agents visible to the caller in their current workspace.

    Driven by the agents DDB table's ``workspace-index`` GSI, not by
    ``bedrock-agentcore-control:ListAgentRuntimes`` — so MCP runtimes,
    Meta-Agent itself, and agents from other workspaces are never
    returned. Live status is enriched per-agent via GetAgentRuntime;
    agents whose runtime has been deleted or never deployed still appear
    (status falls back to the DDB record's stored status).

    Returns:
        JSON array of agents with name, id, status, description.
    """
    deny = require_role(ROLE_VIEWER)
    if deny:
        return json.dumps([])  # No workspace / not a member → empty list

    records = list_workspace_agents()
    if not records:
        return json.dumps([])

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    agents = []
    for record in records:
        agent_id = record.get("agentId", "")
        name = record.get("name") or record.get("agentName") or agent_id
        # Prefer live status from AgentCore; fall back to DDB record.
        # Placeholders (never-deployed agents) will fail GetAgentRuntime
        # but we still want them in the list.
        runtime_status = record.get("status", "unknown")
        try:
            rt = control.get_agent_runtime(agentRuntimeId=agent_id)
            runtime_status = rt.get("status", runtime_status)
        except Exception:
            pass
        agents.append({
            "name": name,
            "id": agent_id,
            "status": runtime_status,
            "description": record.get("description", ""),
        })

    return json.dumps(agents, indent=2, ensure_ascii=False)
