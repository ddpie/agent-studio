"""list_skills — List skills in the caller's workspace."""

import json

import boto3
from boto3.dynamodb.conditions import Key
from strands import tool

from config import REGION
from tools._scope import current_workspace, require_role, ROLE_VIEWER


_SKILLS_TABLE = "agent-studio-skills"


@tool
def list_skills() -> str:
    """List skills visible to the caller in their current workspace.

    Queries the skills DDB table's workspace-index GSI rather than the
    global skills/index.json on S3 (which aggregates skills across every
    workspace and leaks cross-tenant metadata). Output shape matches the
    old index.json for downstream compat.

    Returns:
        JSON array of skills with id, name, description.
    """
    deny = require_role(ROLE_VIEWER)
    if deny:
        return json.dumps([])

    ws = current_workspace()
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(_SKILLS_TABLE)

    items: list[dict] = []
    last_key = None
    try:
        while True:
            kwargs = {
                "IndexName": "workspace-index",
                "KeyConditionExpression": Key("workspace_id").eq(ws),
            }
            if last_key:
                kwargs["ExclusiveStartKey"] = last_key
            resp = table.query(**kwargs)
            for item in resp.get("Items", []):
                if item.get("deleted"):
                    continue
                items.append({
                    "id": item.get("skillId", ""),
                    "name": item.get("name", ""),
                    "description": item.get("description", ""),
                })
            last_key = resp.get("LastEvaluatedKey")
            if not last_key:
                break
    except Exception as e:
        return json.dumps({"error": str(e)})

    return json.dumps(items, indent=2, ensure_ascii=False)
