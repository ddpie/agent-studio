"""update_skill — Update an existing skill's SKILL.md and index."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET


@tool
def update_skill(
    skill_id: str,
    skill_name: str = "",
    description: str = "",
    instructions: str = "",
) -> str:
    """Update an existing skill. Only provided fields are changed.

    Args:
        skill_id: The skill ID to update.
        skill_name: New name (optional, keeps existing if empty).
        description: New description (optional).
        instructions: New markdown instructions body (optional, replaces entire body).

    Returns:
        JSON with status and updated fields.
    """
    s3 = boto3.client("s3", region_name=REGION)
    skill_key = f"skills/{skill_id}/SKILL.md"

    # Read existing SKILL.md
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=skill_key)
        content = obj["Body"].read().decode("utf-8")
    except Exception as e:
        return json.dumps({"error": f"Skill not found: {e}"})

    # Parse existing frontmatter
    existing_name, existing_desc, existing_type, existing_source = skill_id, "", "prompt", "natural-language"
    body = content
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].strip().split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    existing_name = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("description:"):
                    existing_desc = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("type:"):
                    existing_type = line.split(":", 1)[1].strip().strip('"')
                elif line.startswith("source:"):
                    existing_source = line.split(":", 1)[1].strip().strip('"')
            body = parts[2].strip()

    # Apply updates
    final_name = skill_name or existing_name
    final_desc = description or existing_desc
    final_body = instructions if instructions else body

    # Rebuild SKILL.md
    skill_md = f"""---
name: "{final_name}"
description: "{final_desc}"
type: "{existing_type}"
source: "{existing_source}"
user-invocable: true
---

{final_body}
"""

    # Upload
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=skill_key,
        Body=skill_md.encode("utf-8"),
        ContentType="text/markdown",
    )

    # Update index.json
    index = []
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key="skills/index.json")
        index = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        pass

    index = [e for e in index if e.get("id") != skill_id]
    index.append({"id": skill_id, "name": final_name, "description": final_desc})

    s3.put_object(
        Bucket=S3_BUCKET,
        Key="skills/index.json",
        Body=json.dumps(index, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    updated_fields = []
    if skill_name:
        updated_fields.append("name")
    if description:
        updated_fields.append("description")
    if instructions:
        updated_fields.append("instructions")

    return json.dumps({
        "skill_id": skill_id,
        "name": final_name,
        "updated_fields": updated_fields,
        "status": "updated",
    }, indent=2)
