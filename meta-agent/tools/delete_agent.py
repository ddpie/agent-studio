"""delete_agent — Delete an agent from AgentCore Runtime."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET, AGENTS_TABLE
from deploy import delete_runtime


@tool
def delete_agent(agent_id: str) -> str:
    """Delete an agent from AgentCore Runtime.

    Args:
        agent_id: The ID of the agent to delete.

    Returns:
        JSON with deletion status.
    """
    # Ownership check
    caller = getattr(__import__('tools.delete_agent', fromlist=['_caller_id']), '_caller_id', 'unknown')
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)
    record = table.get_item(Key={"agentId": agent_id}).get("Item")
    if record and record.get("owner") != caller:
        return json.dumps({"error": f"Permission denied: agent owned by {record['owner']}, you are {caller}"})

    # Delete runtime
    delete_runtime(agent_id)

    # Clean up DynamoDB record
    table.delete_item(Key={"agentId": agent_id})

    # Clean up S3 metadata
    try:
        s3 = boto3.client("s3", region_name=REGION)
        s3.delete_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/metadata.json")
        s3.delete_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/deployment.zip")
    except Exception:
        pass

    return json.dumps({"agent_id": agent_id, "status": "deleted"})
