"""kb_attach — Attach/detach Knowledge Bases to/from agents."""

import json
import logging
import boto3
from strands import tool
from datetime import datetime, timezone

from config import REGION, KB_TABLE, AGENTS_TABLE
from tools._scope import current_workspace, ensure_agent_in_workspace, ROLE_EDITOR

log = logging.getLogger("meta_agent.kb_attach")


@tool
def kb_attach_to_agent(kb_id: str, agent_id: str) -> str:
    """Attach a Knowledge Base to an agent. The agent must be redeployed to use the KB.

    Args:
        kb_id: The Knowledge Base ID to attach.
        agent_id: The agent ID to attach the KB to.

    Returns:
        JSON with attached status and redeploy reminder.
    """
    ws_id = current_workspace()
    if not ws_id:
        return json.dumps({"error": "no_workspace"})

    ddb = boto3.client("dynamodb", region_name=REGION)

    try:
        kb_resp = ddb.get_item(TableName=KB_TABLE, Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}})
        kb_item = kb_resp.get("Item")
        if not kb_item:
            return json.dumps({"error": "kb_not_found"})
    except Exception as e:
        return json.dumps({"error": "ddb_read_failed", "message": str(e)})

    kb_name = kb_item.get("name", {}).get("S", kb_id)

    # Verify agent belongs to this workspace (prevents cross-workspace attach)
    agent_record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_EDITOR)
    if err:
        return json.dumps(err)

    now = datetime.now(timezone.utc).isoformat()

    try:
        ddb.update_item(
            TableName=AGENTS_TABLE,
            Key={"agentId": {"S": agent_id}},
            UpdateExpression="ADD knowledge_bases :kb_set SET updated_at = :now",
            ExpressionAttributeValues={":kb_set": {"SS": [kb_id]}, ":now": {"S": now}},
        )
    except Exception as e:
        return json.dumps({"error": "agent_update_failed", "message": str(e)})

    try:
        ddb.update_item(
            TableName=KB_TABLE,
            Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}},
            UpdateExpression="ADD attached_agent_ids :agent_set SET updated_at = :now",
            ExpressionAttributeValues={":agent_set": {"SS": [agent_id]}, ":now": {"S": now}},
        )
    except Exception as e:
        log.warning("Failed to update KB %s attached_agent_ids: %s", kb_id, e)

    return json.dumps({
        "attached": True, "kb_id": kb_id, "kb_name": kb_name, "agent_id": agent_id,
        "needs_redeploy": True,
        "message": f"KB '{kb_name}' attached to agent. Redeploy the agent (update_agent) for the change to take effect.",
    }, ensure_ascii=False)


@tool
def kb_detach_from_agent(kb_id: str, agent_id: str) -> str:
    """Detach a Knowledge Base from an agent. The agent should be redeployed after.

    Args:
        kb_id: The Knowledge Base ID to detach.
        agent_id: The agent ID to detach the KB from.

    Returns:
        JSON with detached status and redeploy reminder.
    """
    ws_id = current_workspace()
    if not ws_id:
        return json.dumps({"error": "no_workspace"})

    ddb = boto3.client("dynamodb", region_name=REGION)

    try:
        kb_resp = ddb.get_item(TableName=KB_TABLE, Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}})
        kb_item = kb_resp.get("Item")
        if not kb_item:
            return json.dumps({"error": "kb_not_found"})
    except Exception as e:
        return json.dumps({"error": "ddb_read_failed", "message": str(e)})

    # Verify agent belongs to this workspace (prevents cross-workspace detach)
    agent_record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_EDITOR)
    if err:
        return json.dumps(err)

    now = datetime.now(timezone.utc).isoformat()

    try:
        ddb.update_item(
            TableName=AGENTS_TABLE,
            Key={"agentId": {"S": agent_id}},
            UpdateExpression="DELETE knowledge_bases :kb_set SET updated_at = :now",
            ExpressionAttributeValues={":kb_set": {"SS": [kb_id]}, ":now": {"S": now}},
        )
    except Exception as e:
        return json.dumps({"error": "agent_update_failed", "message": str(e)})

    try:
        ddb.update_item(
            TableName=KB_TABLE,
            Key={"ws_id": {"S": ws_id}, "kb_id": {"S": kb_id}},
            UpdateExpression="DELETE attached_agent_ids :agent_set SET updated_at = :now",
            ExpressionAttributeValues={":agent_set": {"SS": [agent_id]}, ":now": {"S": now}},
        )
    except Exception as e:
        log.warning("Failed to update KB %s attached_agent_ids: %s", kb_id, e)

    return json.dumps({
        "detached": True, "kb_id": kb_id, "agent_id": agent_id,
        "needs_redeploy": True,
        "message": "KB detached. Redeploy the agent to remove the kb_retrieve tool.",
    })
