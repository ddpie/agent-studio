"""get_agent_detail — Get full configuration details of a deployed agent."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET


@tool
def get_agent_detail(agent_id: str) -> str:
    """Get detailed configuration of a deployed agent including metadata, model, tools, and system prompt.

    Args:
        agent_id: The agent runtime ID (e.g., "myAgent-abc123").

    Returns:
        JSON with full agent configuration.
    """
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    s3 = boto3.client("s3", region_name=REGION)

    # Get runtime info
    try:
        runtime = control.get_agent_runtime(agentRuntimeId=agent_id)
    except Exception as e:
        return json.dumps({"error": f"Agent not found: {e}"})

    agent_name = runtime["agentRuntimeName"]
    result = {
        "agent_id": agent_id,
        "name": agent_name,
        "status": runtime["status"],
        "arn": runtime["agentRuntimeArn"],
        "created_at": str(runtime.get("createdAt", "")),
        "updated_at": str(runtime.get("lastUpdatedAt", "")),
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
