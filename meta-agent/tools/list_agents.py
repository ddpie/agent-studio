"""list_agents — List all deployed agents on AgentCore Runtime."""

import json

import boto3
from strands import tool

from config import REGION


@tool
def list_agents() -> str:
    """List all deployed agents on AgentCore Runtime.

    Returns:
        JSON array of agents with name, id, status, and description.
    """
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    resp = control.list_agent_runtimes()

    agents = []
    for rt in resp.get("agentRuntimes", []):
        agents.append({
            "name": rt["agentRuntimeName"],
            "id": rt["agentRuntimeId"],
            "status": rt["status"],
            "description": rt.get("description", ""),
        })

    return json.dumps(agents, indent=2)
