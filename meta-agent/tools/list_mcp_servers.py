"""list_mcp_servers — List available MCP tool servers from AgentCore Gateway."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET


def _load_catalog() -> dict:
    """Load target catalog from S3 for category metadata."""
    try:
        s3 = boto3.client("s3", region_name=REGION)
        resp = s3.get_object(Bucket=S3_BUCKET, Key="mcp/target-catalog.json")
        items = json.loads(resp["Body"].read().decode())
        return {item["name"]: item for item in items}
    except Exception:
        return {}


def _list_gateway_targets() -> list:
    """List all gateway targets with pagination."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    gateways = control.list_gateways()
    all_targets = []

    for gw in gateways.get("items", gateways.get("gateways", [])):
        gw_id = gw["gatewayId"]
        gw_name = gw.get("name", "")

        resp = control.list_gateway_targets(gatewayIdentifier=gw_id)
        targets = resp.get("items", resp.get("targets", []))
        while resp.get("nextToken"):
            resp = control.list_gateway_targets(
                gatewayIdentifier=gw_id, nextToken=resp["nextToken"]
            )
            targets.extend(resp.get("items", resp.get("targets", [])))

        for t in targets:
            all_targets.append({
                "gateway_id": gw_id,
                "gateway_name": gw_name,
                "target_name": t.get("name", ""),
                "status": t.get("status", ""),
            })

    return all_targets


@tool
def list_mcp_servers() -> str:
    """List available MCP tool servers from AgentCore Gateway.

    Returns categorized MCP targets with status. Use this to show users what
    MCP tools are available when creating or updating agents.

    Returns:
        JSON with summary and targets grouped by category.
    """
    try:
        catalog = _load_catalog()
        targets = _list_gateway_targets()

        # Merge gateway status with catalog metadata.
        # Strip the "mcp-" prefix that gateway targets carry — the short name
        # (e.g. "cloudwatch") is the contract everywhere else: the frontend
        # /mcp/targets API returns short names, workspace policy stores short
        # names, and _resolve_mcp_endpoints expects short names so it can map
        # "nova-canvas" → runtime "nova_canvas". Leaving the prefix on here
        # caused Meta-Agent to write "mcp-cloudwatch" into proposals, which
        # then failed to match the selector's short names in the editor and
        # would have 404'd at runtime resolution.
        merged = []
        for t in targets:
            gw_name = t["target_name"]
            short = gw_name[len("mcp-"):] if gw_name.startswith("mcp-") else gw_name
            cat_entry = catalog.get(short, {})
            merged.append({
                "name": short,
                "description": cat_entry.get("description", ""),
                "category": cat_entry.get("category", "general"),
                "status": t["status"],
            })

        # Group by category
        by_category = {}
        for m in merged:
            cat = m["category"]
            by_category.setdefault(cat, []).append(m)

        ready_count = sum(1 for m in merged if m["status"] == "READY")

        result = {
            "total": len(merged),
            "ready": ready_count,
            "categories": {
                cat: [{"name": t["name"], "description": t["description"], "status": t["status"]}
                      for t in targets_list]
                for cat, targets_list in sorted(by_category.items())
            },
        }
        return json.dumps(result, indent=2, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})
