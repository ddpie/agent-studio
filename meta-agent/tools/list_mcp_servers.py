"""list_mcp_servers — List available MCP servers from AgentCore Gateway."""

import json

import boto3
from strands import tool

from config import REGION


@tool
def list_mcp_servers() -> str:
    """List available MCP tool servers from AgentCore Gateway targets.

    Returns:
        JSON array of MCP servers with gateway_id, target_id, name, description.
    """
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    servers = []
    gateways = control.list_gateways()

    for gw in gateways.get("gateways", []):
        gw_id = gw["gatewayId"]
        targets = control.list_gateway_targets(gatewayIdentifier=gw_id)

        for target in targets.get("targets", []):
            servers.append({
                "gateway_id": gw_id,
                "gateway_name": gw["name"],
                "target_id": target["targetId"],
                "target_name": target["name"],
                "description": target.get("description", ""),
                "status": target.get("status", ""),
            })

    return json.dumps(servers, indent=2)
