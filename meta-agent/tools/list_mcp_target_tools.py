"""list_mcp_target_tools — List available tools for a specific MCP target."""

import json

import boto3
from config import REGION, S3_BUCKET
from strands import tool


def _list_all_manifests() -> dict[str, str]:
    """List all target-tools manifest keys from S3, returning {normalized_name: s3_key}."""
    s3 = boto3.client("s3", region_name=REGION)
    mapping = {}
    try:
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix="mcp/target-tools/")
        for obj in resp.get("Contents", []):
            key = obj["Key"]  # e.g. "mcp/target-tools/mcp-cloudwatch.json"
            name = key.split("/")[-1].removesuffix(".json")  # "mcp-cloudwatch"
            mapping[name] = key
    except Exception:
        pass
    return mapping


@tool
def list_mcp_target_tools(target_name: str) -> str:
    """List the tools provided by a specific MCP target server.

    Use this BEFORE writing an agent's system_prompt to get the exact tool names
    and descriptions. This ensures the prompt references real tool names instead
    of guessing. Supports fuzzy matching — e.g., "cloudtrail" will match
    "mcp-cloudwatch".

    Args:
        target_name: The MCP target name (e.g. "cloudwatch", "cloudtrail").

    Returns:
        JSON with the target's tool list: {target, tools: [{name, description}]}.
    """
    s3 = boto3.client("s3", region_name=REGION)
    manifests = _list_all_manifests()

    # Find best match: exact → prefix-equal → shortest fuzzy contains.
    # Iteration order of the S3 listing is NOT stable across regions, and we
    # previously returned the first "contains" match — which for target="cloudwatch"
    # picked mcp-cloudwatch-applicationsignals (wrong runtime) over mcp-cloudwatch.
    # Sort candidates by length so the most-specific match wins the tie.
    needle = target_name.lower().replace("-", "").replace("_", "")
    matched_key = None

    # Pass 1: exact (normalized) or verbatim match
    for name, key in manifests.items():
        norm = name.lower().replace("-", "").replace("_", "")
        if norm == needle or name == target_name:
            matched_key = key
            break

    # Pass 2: normalized name equals needle with an `mcp` prefix/suffix stripped.
    # Catches user saying "cloudwatch" when the manifest is "mcp-cloudwatch".
    if not matched_key:
        for name, key in sorted(manifests.items(), key=lambda kv: len(kv[0])):
            norm = name.lower().replace("-", "").replace("_", "")
            stripped = norm.removeprefix("mcp")
            if stripped == needle:
                matched_key = key
                break

    # Pass 3: fuzzy contains — but prefer the shortest candidate so
    # "cloudwatch" doesn't silently match "cloudwatch-applicationsignals".
    if not matched_key:
        candidates = [(name, key) for name, key in manifests.items()
                      if needle in name.lower().replace("-", "").replace("_", "")
                      or name.lower().replace("-", "").replace("_", "") in needle]
        candidates.sort(key=lambda kv: len(kv[0]))
        if candidates:
            matched_key = candidates[0][1]

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
