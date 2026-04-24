"""update_skill — Update an existing skill's SKILL.md and DDB metadata.

Verifies the skill's DDB row belongs to the caller's workspace before
touching S3. Returning the generic "not found in this workspace" error
for cross-workspace skills mirrors ensure_agent_in_workspace — it avoids
leaking whether the id exists elsewhere.
"""

import json
from datetime import datetime, timezone

import boto3
from strands import tool

from config import REGION, S3_BUCKET
from tools._scope import ROLE_EDITOR, current_workspace, require_role


_SKILLS_TABLE = "agent-studio-skills"


def _get_skill(skill_id: str) -> dict | None:
    ddb = boto3.resource("dynamodb", region_name=REGION)
    try:
        return ddb.Table(_SKILLS_TABLE).get_item(Key={"skillId": skill_id}).get("Item")
    except Exception:
        return None


@tool
def update_skill(
    skill_id: str,
    skill_name: str = "",
    description: str = "",
    instructions: str = "",
) -> str:
    """Update a skill's SKILL.md — name, description, or body text.

    This tool rewrites ONLY the SKILL.md frontmatter and body. Other
    files in the skill (script.py, scripts/*.py, bundled assets) are
    NOT touched. To fix a bug in script.py or any non-SKILL.md file,
    use ``write_skill_file(skill_id, path, content)`` instead.

    Args:
        skill_id: The skill ID to update.
        skill_name: New name (optional, keeps existing if empty).
        description: New description (optional).
        instructions: New markdown instructions body (optional, replaces entire body).

    Returns:
        JSON with status and updated fields.
    """
    deny = require_role(ROLE_EDITOR)
    if deny:
        return json.dumps(deny)

    ws_id = current_workspace()
    existing = _get_skill(skill_id)
    if not existing or existing.get("workspace_id") != ws_id or existing.get("deleted"):
        return json.dumps({"error": f"Skill {skill_id} not found in this workspace."})

    s3 = boto3.client("s3", region_name=REGION)
    skill_key = f"skills/{skill_id}/SKILL.md"

    # Pull existing SKILL.md so we can rewrite only the frontmatter fields
    # the caller passed without losing the body or metadata we don't own
    # here (e.g. type, source, emoji).
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=skill_key)
        content = obj["Body"].read().decode("utf-8")
    except Exception as e:
        return json.dumps({"error": f"Skill file not found: {e}"})

    existing_name = existing.get("name", skill_id)
    existing_desc = existing.get("description", "")
    existing_type = existing.get("type", "prompt")
    existing_source = existing.get("source", "natural-language")
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    existing_name = line.split(":", 1)[1].strip().strip('"') or existing_name
                elif line.startswith("description:"):
                    existing_desc = line.split(":", 1)[1].strip().strip('"') or existing_desc
                elif line.startswith("type:"):
                    existing_type = line.split(":", 1)[1].strip().strip('"') or existing_type
                elif line.startswith("source:"):
                    existing_source = line.split(":", 1)[1].strip().strip('"') or existing_source
            body = parts[2].strip()

    final_name = skill_name or existing_name
    final_desc = description or existing_desc
    final_body = instructions if instructions else body

    skill_md = f"""---
name: "{final_name}"
description: "{final_desc}"
type: "{existing_type}"
source: "{existing_source}"
user-invocable: true
---

{final_body}
"""

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=skill_key,
        Body=skill_md.encode("utf-8"),
        ContentType="text/markdown",
    )

    updated_fields: list[str] = []
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    update_parts = ["updated_at = :now"]
    expr_names: dict[str, str] = {}
    expr_values: dict[str, object] = {":now": now, ":ws": ws_id}

    if skill_name:
        update_parts.append("#n = :name")
        expr_names["#n"] = "name"
        expr_values[":name"] = final_name
        updated_fields.append("name")
    if description:
        update_parts.append("description = :desc")
        expr_values[":desc"] = final_desc
        updated_fields.append("description")
    if instructions:
        updated_fields.append("instructions")

    try:
        kwargs = {
            "Key": {"skillId": skill_id},
            "UpdateExpression": "SET " + ", ".join(update_parts),
            "ExpressionAttributeValues": expr_values,
            "ConditionExpression": "attribute_exists(skillId) AND workspace_id = :ws",
        }
        if expr_names:
            kwargs["ExpressionAttributeNames"] = expr_names
        boto3.resource("dynamodb", region_name=REGION).Table(_SKILLS_TABLE).update_item(**kwargs)
    except Exception as e:
        return json.dumps({"error": f"Skill metadata update failed: {e}"})

    return json.dumps({
        "skill_id": skill_id,
        "name": final_name,
        "updated_fields": updated_fields,
        "status": "updated",
    }, indent=2)
