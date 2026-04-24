"""delete_skill — Delete a skill from DDB + S3 within the caller's workspace.

Hard-deletes both the DDB row and all objects under ``skills/{skill_id}/``.
The Lambda CRUD ``DELETE /skills/{id}`` endpoint soft-deletes by default
(``deleted=true`` + ``deleted_at`` timestamp), but Meta-Agent operators
are already gating this behind an explicit user confirmation prompt, so
the hard-delete semantics stay.
"""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET
from tools._scope import ROLE_EDITOR, current_workspace, require_role


_SKILLS_TABLE = "agent-studio-skills"


@tool
def delete_skill(skill_id: str) -> str:
    """Delete a skill by ID. Removes DDB row + all S3 files under skills/{id}/.

    Args:
        skill_id: The skill ID to delete.

    Returns:
        JSON with status.
    """
    deny = require_role(ROLE_EDITOR)
    if deny:
        return json.dumps(deny)

    ws_id = current_workspace()
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(_SKILLS_TABLE)

    try:
        existing = table.get_item(Key={"skillId": skill_id}).get("Item")
    except Exception as e:
        return json.dumps({"error": f"Skill lookup failed: {e}"})

    if not existing or existing.get("workspace_id") != ws_id:
        return json.dumps({"error": f"Skill {skill_id} not found in this workspace."})

    # Delete S3 files first — if we deleted the DDB row first and then
    # crashed mid-S3-cleanup, orphan files would be unreachable via any
    # workspace-scoped tool but still billable. Orphan S3 only — fine.
    s3 = boto3.client("s3", region_name=REGION)
    prefix = f"skills/{skill_id}/"
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=S3_BUCKET, Prefix=prefix):
            objects = page.get("Contents", []) or []
            if not objects:
                continue
            s3.delete_objects(
                Bucket=S3_BUCKET,
                Delete={"Objects": [{"Key": o["Key"]} for o in objects]},
            )
    except Exception as e:
        return json.dumps({"error": f"Failed to delete skill files: {e}"})

    try:
        table.delete_item(
            Key={"skillId": skill_id},
            ConditionExpression="attribute_exists(skillId) AND workspace_id = :ws",
            ExpressionAttributeValues={":ws": ws_id},
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return json.dumps({"error": f"Skill {skill_id} not found in this workspace."})
    except Exception as e:
        return json.dumps({"error": f"Skill metadata delete failed: {e}"})

    return json.dumps({
        "skill_id": skill_id,
        "status": "deleted",
    }, indent=2)
