"""delete_skill — Delete a skill from S3 and update index."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET


@tool
def delete_skill(skill_id: str) -> str:
    """Delete a skill by ID. Removes SKILL.md, any scripts, and updates index.json.

    Args:
        skill_id: The skill ID to delete.

    Returns:
        JSON with status.
    """
    s3 = boto3.client("s3", region_name=REGION)
    prefix = f"skills/{skill_id}/"

    # List and delete all objects under the skill prefix
    try:
        resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix=prefix)
        objects = resp.get("Contents", [])
        if not objects:
            return json.dumps({"error": f"Skill '{skill_id}' not found."})

        for obj in objects:
            s3.delete_object(Bucket=S3_BUCKET, Key=obj["Key"])
    except Exception as e:
        return json.dumps({"error": f"Failed to delete skill files: {e}"})

    # Update index.json
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key="skills/index.json")
        index = json.loads(obj["Body"].read().decode("utf-8"))
        index = [e for e in index if e.get("id") != skill_id]
        s3.put_object(
            Bucket=S3_BUCKET,
            Key="skills/index.json",
            Body=json.dumps(index, indent=2, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json",
        )
    except Exception:
        pass  # Index update is best-effort

    return json.dumps({
        "skill_id": skill_id,
        "status": "deleted",
    }, indent=2)
