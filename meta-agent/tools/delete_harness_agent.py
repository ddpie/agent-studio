"""delete_harness_agent — Delete a Harness Agent + soft-delete DDB record.

If the harness is already gone on the AgentCore side (ResourceNotFound), the
DDB record is still archived — lets us clean up orphan records after manual
AWS-console deletions.
"""

import json
from datetime import datetime

import boto3
from config import AGENTS_TABLE, REGION
from strands import tool


def _get_control_client():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def _get_agents_table():
    return boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)


@tool
def delete_harness_agent(agent_id: str) -> str:
    """Delete an AgentCore Harness Agent.

    Calls control-plane delete_harness then soft-deletes the DDB record
    (status="archived"). Use for runtime_type=="harness" agents only.

    Args:
        agent_id: harness agent id (same as DDB agentId).

    Returns:
        JSON-encoded {"ok": true, "agentId": "..."} or {"error": "..."}.
    """
    try:
        table = _get_agents_table()
        existing = table.get_item(Key={"agentId": agent_id}).get("Item")
        if not existing:
            return json.dumps({"error": f"agent not found: {agent_id}"})
        if existing.get("runtime_type") != "harness":
            return json.dumps(
                {"error": f"agent runtime_type is not 'harness': {existing.get('runtime_type')}"}
            )

        cp = _get_control_client()
        try:
            cp.delete_harness(harnessId=agent_id)
        except cp.exceptions.ResourceNotFoundException:
            # Already deleted on AgentCore side — still archive DDB row.
            pass

        now = datetime.utcnow().isoformat() + "Z"
        table.update_item(
            Key={"agentId": agent_id},
            UpdateExpression="SET #s = :status, updated_at = :updated_at",
            ExpressionAttributeNames={"#s": "status"},
            ExpressionAttributeValues={":status": "archived", ":updated_at": now},
        )
        return json.dumps({"ok": True, "agentId": agent_id}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})
