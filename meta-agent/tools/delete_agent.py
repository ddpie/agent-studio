"""delete_agent — Archive, restore, or permanently delete an agent."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET, AGENTS_TABLE, AGENT_ROLE_ARN
from deploy import delete_runtime, create_runtime, wait_for_ready


@tool
def delete_agent(agent_id: str) -> str:
    """Archive an agent: delete its AgentCore Runtime but keep S3 data for recovery.

    Args:
        agent_id: The ID of the agent to archive.

    Returns:
        JSON with archive status.
    """
    caller = getattr(__import__('tools.delete_agent', fromlist=['_caller_id']), '_caller_id', 'unknown')
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)
    record = table.get_item(Key={"agentId": agent_id}).get("Item")

    if not record:
        return json.dumps({"error": f"Agent {agent_id} not found in registry"})
    if record.get("owner") != caller:
        return json.dumps({"error": f"Permission denied: agent owned by {record['owner']}"})
    if record.get("status") == "archived":
        return json.dumps({"error": "Agent is already archived"})

    # Delete runtime only
    try:
        delete_runtime(agent_id)
    except Exception as e:
        return json.dumps({"error": f"Failed to delete runtime: {e}"})

    # Mark as archived in DynamoDB
    table.update_item(
        Key={"agentId": agent_id},
        UpdateExpression="SET #s = :val",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":val": "archived"},
    )

    return json.dumps({"agent_id": agent_id, "action": "archived", "status": "archived"})


@tool
def restore_agent(agent_id: str) -> str:
    """Restore an archived agent by recreating its AgentCore Runtime from S3 data.

    Args:
        agent_id: The ID of the archived agent to restore.

    Returns:
        JSON with restore status.
    """
    caller = getattr(__import__('tools.delete_agent', fromlist=['_caller_id']), '_caller_id', 'unknown')
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)
    record = table.get_item(Key={"agentId": agent_id}).get("Item")

    if not record:
        return json.dumps({"error": f"Agent {agent_id} not found in registry"})
    if record.get("owner") != caller:
        return json.dumps({"error": f"Permission denied: agent owned by {record['owner']}"})
    if record.get("status") != "archived":
        return json.dumps({"error": "Agent is not archived, cannot restore"})

    agent_name = record.get("agentName", agent_id)

    # Check S3 deployment exists
    s3 = boto3.client("s3", region_name=REGION)
    # Try agentId path first, fallback to name path
    s3_key = f"agents/{agent_id}/deployment.zip"
    try:
        s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
    except Exception:
        s3_key = f"agents/{agent_name}/deployment.zip"
        try:
            s3.head_object(Bucket=S3_BUCKET, Key=s3_key)
        except Exception:
            return json.dumps({"error": "No deployment.zip found in S3, cannot restore"})

    # Read description from metadata
    description = ""
    old_metadata = None
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/metadata.json")
        old_metadata = json.loads(obj["Body"].read().decode("utf-8"))
        description = old_metadata.get("description", "")
    except Exception:
        pass

    # Recreate runtime
    result = create_runtime(agent_name, description, s3_key)
    new_agent_id = result["agent_id"]
    status = wait_for_ready(new_agent_id)

    # Copy metadata.json to new agent_id path
    if old_metadata:
        old_metadata["agent_id"] = new_agent_id
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=f"agents/{new_agent_id}/metadata.json",
            Body=json.dumps(old_metadata),
            ContentType="application/json",
        )

    # Update DynamoDB with new agent ID
    table.delete_item(Key={"agentId": agent_id})
    record["agentId"] = new_agent_id
    record["status"] = "active"
    table.put_item(Item=record)

    return json.dumps({
        "old_agent_id": agent_id,
        "new_agent_id": new_agent_id,
        "agent_name": agent_name,
        "action": "restored",
        "status": status,
    })


@tool
def purge_agent(agent_id: str) -> str:
    """Permanently delete an archived agent, removing all S3 data and DynamoDB record. Irreversible.

    Args:
        agent_id: The ID of the archived agent to permanently delete.

    Returns:
        JSON with purge status.
    """
    caller = getattr(__import__('tools.delete_agent', fromlist=['_caller_id']), '_caller_id', 'unknown')
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)
    record = table.get_item(Key={"agentId": agent_id}).get("Item")

    if not record:
        return json.dumps({"error": f"Agent {agent_id} not found in registry"})
    if record.get("owner") != caller:
        return json.dumps({"error": f"Permission denied: agent owned by {record['owner']}"})
    if record.get("status") != "archived":
        return json.dumps({"error": "Only archived agents can be permanently deleted. Archive it first."})

    agent_name = record.get("agentName", "")

    # Delete S3 data
    s3 = boto3.client("s3", region_name=REGION)
    for prefix in [f"agents/{agent_id}/", f"agents/{agent_name}/"]:
        try:
            resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
            for obj in resp.get("Contents", []):
                s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])
        except Exception:
            pass

    # Delete DynamoDB record
    table.delete_item(Key={"agentId": agent_id})

    return json.dumps({"agent_id": agent_id, "action": "purged", "status": "permanently_deleted"})
