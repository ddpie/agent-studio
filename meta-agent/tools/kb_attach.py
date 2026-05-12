"""kb_attach — Attach/detach Knowledge Bases to/from agents."""

import json
import urllib.parse
import boto3
from strands import tool
from datetime import datetime, timezone

from config import REGION, ACCOUNT_ID, KB_TABLE, AGENTS_TABLE
from tools._scope import current_workspace_id


def _update_workspace_role_kb_policy(workspace_id: str, bedrock_kb_id: str, action: str):
    """Add or remove bedrock-agent-runtime:Retrieve permission for a KB on the workspace role."""
    iam = boto3.client("iam", region_name=REGION)
    role_name = f"AgentStudio-ws-{workspace_id[:20]}-{REGION}"
    policy_name = "WorkspaceGrants"
    kb_arn = f"arn:aws:bedrock:{REGION}:{ACCOUNT_ID}:knowledge-base/{bedrock_kb_id}"

    try:
        resp = iam.get_role_policy(RoleName=role_name, PolicyName=policy_name)
        policy_doc = json.loads(urllib.parse.unquote(resp["PolicyDocument"])) if isinstance(resp["PolicyDocument"], str) else resp["PolicyDocument"]
    except iam.exceptions.NoSuchEntityException:
        policy_doc = {"Version": "2012-10-17", "Statement": []}
    except Exception:
        policy_doc = {"Version": "2012-10-17", "Statement": []}

    kb_stmt = None
    for stmt in policy_doc["Statement"]:
        if stmt.get("Sid") == "KBRetrieve":
            kb_stmt = stmt
            break

    if kb_stmt is None:
        kb_stmt = {"Sid": "KBRetrieve", "Effect": "Allow", "Action": "bedrock-agent-runtime:Retrieve", "Resource": []}
        policy_doc["Statement"].append(kb_stmt)

    if isinstance(kb_stmt.get("Resource"), str):
        kb_stmt["Resource"] = [kb_stmt["Resource"]]

    if action == "add":
        if kb_arn not in kb_stmt["Resource"]:
            kb_stmt["Resource"].append(kb_arn)
    elif action == "remove":
        kb_stmt["Resource"] = [r for r in kb_stmt["Resource"] if r != kb_arn]
        if not kb_stmt["Resource"]:
            policy_doc["Statement"] = [s for s in policy_doc["Statement"] if s.get("Sid") != "KBRetrieve"]

    iam.put_role_policy(RoleName=role_name, PolicyName=policy_name, PolicyDocument=json.dumps(policy_doc))


@tool
def kb_attach_to_agent(kb_id: str, agent_id: str) -> str:
    """Attach a Knowledge Base to an agent. The agent must be redeployed to use the KB.

    Args:
        kb_id: The Knowledge Base ID to attach.
        agent_id: The agent ID to attach the KB to.

    Returns:
        JSON with attached status and redeploy reminder.
    """
    ws_id = current_workspace_id()
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

    bedrock_kb_id = kb_item["bedrock_kb_id"]["S"]
    kb_name = kb_item.get("name", {}).get("S", kb_id)

    try:
        agent_resp = ddb.get_item(TableName=AGENTS_TABLE, Key={"agentId": {"S": agent_id}})
        if not agent_resp.get("Item"):
            return json.dumps({"error": "agent_not_found"})
    except Exception as e:
        return json.dumps({"error": "agent_lookup_failed", "message": str(e)})

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
    except Exception:
        pass

    try:
        _update_workspace_role_kb_policy(ws_id, bedrock_kb_id, "add")
    except Exception as e:
        return json.dumps({"error": "iam_update_failed", "message": str(e), "hint": "KB attached in DDB but IAM grant failed."})

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
    ws_id = current_workspace_id()
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

    bedrock_kb_id = kb_item["bedrock_kb_id"]["S"]
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
    except Exception:
        pass

    try:
        _update_workspace_role_kb_policy(ws_id, bedrock_kb_id, "remove")
    except Exception:
        pass

    return json.dumps({
        "detached": True, "kb_id": kb_id, "agent_id": agent_id,
        "needs_redeploy": True,
        "message": "KB detached. Redeploy the agent to remove the kb_retrieve tool.",
    })
