"""list_mcp_target_tools — List available tools for a specific MCP target."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET


def _list_all_manifests() -> dict[str, str]:
    """List all target-tools manifest keys from S3, returning {normalized_name: s3_key}."""
    s3 = boto3.client("s3", region_name=REGION)
    mapping = {}
    try:
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix="mcp/target-tools/")
        for obj in resp.get("Contents", []):
            key = obj["Key"]  # e.g. "mcp/target-tools/mcp-nova-canvas.json"
            name = key.split("/")[-1].removesuffix(".json")  # "mcp-nova-canvas"
            mapping[name] = key
    except Exception:
        pass
    return mapping


@tool
def list_mcp_target_tools(target_name: str) -> str:
    """List the tools provided by a specific MCP target server.

    Use this BEFORE writing an agent's system_prompt to get the exact tool names
    and descriptions. This ensures the prompt references real tool names instead
    of guessing. Supports fuzzy matching — e.g., "nova-canvas" will match
    "mcp-nova-canvas".

    Args:
        target_name: The MCP target name (e.g. "nova-canvas", "cloudwatch").

    Returns:
        JSON with the target's tool list: {target, tools: [{name, description}]}.
    """
    s3 = boto3.client("s3", region_name=REGION)
    manifests = _list_all_manifests()

    # Find best match: exact → contains → fuzzy
    needle = target_name.lower().replace("-", "").replace("_", "")
    matched_key = None

    for name, key in manifests.items():
        norm = name.lower().replace("-", "").replace("_", "")
        if norm == needle or name == target_name:
            matched_key = key
            break

    if not matched_key:
        for name, key in manifests.items():
            norm = name.lower().replace("-", "").replace("_", "")
            if needle in norm or norm in needle:
                matched_key = key
                break

    if matched_key:
        try:
            resp = s3.get_object(Bucket=S3_BUCKET, Key=matched_key)
            tools = json.loads(resp["Body"].read().decode("utf-8"))
            return json.dumps({
                "target": target_name,
                "manifest_key": matched_key.split("/")[-1],
                "tool_count": len(tools),
                "tools": tools,
            }, indent=2, ensure_ascii=False)
        except Exception:
            pass

    # No match — return available targets as hint
    available = sorted(manifests.keys())
    return json.dumps({
        "target": target_name,
        "tool_count": 0,
        "tools": [],
        "available_targets": available,
        "hint": f"No manifest matched '{target_name}'. Available: {', '.join(available[:10])}...",
    }, indent=2, ensure_ascii=False)
