"""list_skills — List all available skills from S3."""

import json

import boto3
from strands import tool

from config import REGION, S3_BUCKET


@tool
def list_skills() -> str:
    """List all available skills stored in S3.

    Returns:
        JSON array of skills with id, name, type, and description.
    """
    s3 = boto3.client("s3", region_name=REGION)

    skills = []
    resp = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix="skills/", Delimiter="/")

    for prefix in resp.get("CommonPrefixes", []):
        skill_prefix = prefix["Prefix"]  # e.g., "skills/abc123/"
        skill_id = skill_prefix.split("/")[1]

        # Read SKILL.md
        try:
            obj = s3.get_object(Bucket=S3_BUCKET, Key=f"{skill_prefix}SKILL.md")
            content = obj["Body"].read().decode("utf-8")

            # Parse YAML frontmatter
            name, description, skill_type = skill_id, "", "prompt"
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    for line in parts[1].strip().split("\n"):
                        line = line.strip()
                        if line.startswith("name:"):
                            name = line.split(":", 1)[1].strip().strip('"')
                        elif line.startswith("description:"):
                            description = line.split(":", 1)[1].strip().strip('"')
                        elif line.startswith("type:"):
                            skill_type = line.split(":", 1)[1].strip().strip('"')

            skills.append({
                "skill_id": skill_id,
                "name": name,
                "description": description,
                "type": skill_type,
            })
        except Exception:
            continue

    return json.dumps(skills, indent=2)
