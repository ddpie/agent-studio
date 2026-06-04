"""delete_agent — Archive, restore, or permanently delete an agent."""

import json

import boto3
from config import AGENTS_TABLE, REGION, S3_BUCKET
from deploy import create_runtime, delete_runtime, wait_for_ready
from strands import tool

from tools._scope import ROLE_ADMIN, ROLE_OWNER, ensure_agent_in_workspace


@tool
def delete_agent(agent_id: str) -> str:
    """Archive an agent: delete its AgentCore Runtime but keep S3 data for recovery.

    Args:
        agent_id: The ID of the agent to archive.

    Returns:
        JSON with archive status.
    """
    record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_ADMIN)
    if err:
        return json.dumps(err)
    if record.get("status") == "archived":
        return json.dumps({"error": "Agent is already archived"})
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)

    # Delete the AgentCore runtime. Agents created via the in-app "new agent"
    # flow or cloned from the marketplace exist only as a DDB placeholder —
    # they have a UUID agentId but no deployed runtime yet. AgentCore
    # returns AccessDeniedException (not NotFound) for non-existent runtime
    # ids, so we swallow both and continue to the DDB archive step.
    runtime_note = ""
    try:
        delete_runtime(agent_id)
    except Exception as e:
        msg = str(e)
        benign = ("AccessDeniedException" in msg or "ResourceNotFoundException" in msg
                  or "not authorized" in msg.lower() or "not found" in msg.lower())
        if not benign:
            return json.dumps({"error": f"Failed to delete runtime: {e}"})
        runtime_note = "runtime was never deployed (or already deleted); archived DDB record only"

    # Mark as archived in DynamoDB
    table.update_item(
        Key={"agentId": agent_id},
        UpdateExpression="SET #s = :val",
        ExpressionAttributeNames={"#s": "status"},
        ExpressionAttributeValues={":val": "archived"},
    )

    result = {"agent_id": agent_id, "action": "archived", "status": "archived"}
    if runtime_note:
        result["note"] = runtime_note
    return json.dumps(result)


@tool
def restore_agent(agent_id: str) -> str:
    """Restore an archived agent by recreating its AgentCore Runtime from S3 data.

    Args:
        agent_id: The ID of the archived agent to restore.

    Returns:
        JSON with restore status.
    """
    record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_ADMIN)
    if err:
        return json.dumps(err)
    if record.get("status") != "archived":
        return json.dumps({"error": "Agent is not archived, cannot restore"})
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)

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
    record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_OWNER)
    if err:
        return json.dumps(err)
    if record.get("status") != "archived":
        return json.dumps({"error": "Only archived agents can be permanently deleted. Archive it first."})
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)

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
