"""get_agent_detail — Get full configuration details of a deployed agent."""

import json
from datetime import datetime, timezone

import boto3
from strands import tool

from config import REGION, S3_BUCKET
from tools._scope import ensure_agent_in_workspace, ROLE_VIEWER


def _iso_utc(value) -> str:
    """Serialize a datetime as a UTC ISO-8601 string so downstream
    consumers parse it as UTC instead of silently treating it as local.
    """
    if value is None or value == "":
        return ""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.isoformat()
    return str(value)


@tool
def get_agent_detail(agent_id: str) -> str:
    """Get detailed configuration of a deployed agent including metadata, model, tools, and system prompt.

    Args:
        agent_id: The agent runtime ID (e.g., "myAgent-abc123").

    Returns:
        JSON with full agent configuration.
    """
    _record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_VIEWER)
    if err:
        return json.dumps(err)

    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    # Get runtime info
    try:
        runtime = control.get_agent_runtime(agentRuntimeId=agent_id)
    except Exception as e:
        return json.dumps({"error": f"Agent runtime lookup failed: {e}"})

    agent_name = runtime["agentRuntimeName"]
    result = {
        "agent_id": agent_id,
        "name": agent_name,
        "status": runtime["status"],
        "arn": runtime["agentRuntimeArn"],
        "created_at": _iso_utc(runtime.get("createdAt")),
        "updated_at": _iso_utc(runtime.get("lastUpdatedAt")),
        "description": runtime.get("description", ""),
    }

    # Try to read metadata.json from S3
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/metadata.json")
        metadata = json.loads(obj["Body"].read().decode("utf-8"))
        result["metadata"] = metadata
    except Exception:
        result["metadata"] = None
        result["metadata_note"] = "No metadata.json found (agent may have been created before metadata support)"

    return json.dumps(result, indent=2, ensure_ascii=False, default=str)
