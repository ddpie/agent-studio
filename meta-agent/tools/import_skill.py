"""import_skill — Import a skill from URL or raw markdown content."""

import json
import re
import uuid

import boto3
from strands import tool

from config import REGION, S3_BUCKET


def _parse_frontmatter(content: str) -> dict | None:
    """Parse YAML frontmatter from SKILL.md content. Returns dict or None."""
    if not content.startswith("---"):
        return None
    parts = content.split("---", 2)
    if len(parts) < 3:
        return None
    meta = {}
    for line in parts[1].strip().split("\n"):
        line = line.strip()
        if ":" in line:
            key, val = line.split(":", 1)
            meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta if meta.get("name") else None


def _wrap_with_frontmatter(content: str, name: str, description: str, source: str = "imported") -> str:
    """Wrap plain markdown with AgentSkills.io frontmatter."""
    return f"""---
name: "{name}"
description: "{description}"
type: "prompt"
source: "{source}"
user-invocable: true
---

{content.strip()}
"""


def _update_skill_index(s3_client, new_entry: dict):
    """Read index.json, append new entry, write back."""
    index = []
    try:
        obj = s3_client.get_object(Bucket=S3_BUCKET, Key="skills/index.json")
        index = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        pass
    index = [e for e in index if e.get("id") != new_entry["id"]]
    index.append(new_entry)
    s3_client.put_object(
        Bucket=S3_BUCKET,
        Key="skills/index.json",
        Body=json.dumps(index, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )


@tool
def import_skill(
    content: str = "",
    url: str = "",
    name: str = "",
    description: str = "",
) -> str:
    """Import a skill from raw markdown content or a URL.

    If the content already has AgentSkills.io YAML frontmatter, it is used as-is.
    If it's plain markdown (.cursorrules, AGENTS.md, etc.), frontmatter is auto-generated
    using the provided name and description.

    Args:
        content: Raw markdown content of the skill. Either content or url must be provided.
        url: URL to fetch the skill content from. Either content or url must be provided.
        name: Skill name (required if content has no frontmatter). kebab-case recommended.
        description: Skill description (required if content has no frontmatter).

    Returns:
        JSON with skill_id, name, and status.
    """
    # Fetch from URL if provided
    if url and not content:
        try:
            import urllib.request
            req = urllib.request.Request(url, headers={"User-Agent": "AgentStudio/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                content = resp.read().decode("utf-8")
        except Exception as e:
            return json.dumps({"error": f"Failed to fetch URL: {e}"})

    if not content or not content.strip():
        return json.dumps({"error": "No content provided. Pass content or url."})

    # Check if content already has frontmatter
    meta = _parse_frontmatter(content)
    if meta:
        skill_name = meta.get("name", name or "imported-skill")
        skill_desc = meta.get("description", description or "")
        skill_md = content
    else:
        # Plain markdown — wrap with frontmatter
        if not name:
            return json.dumps({
                "error": "Content has no YAML frontmatter. Provide name and description to auto-generate it.",
                "hint": "Set name (kebab-case) and description for the skill.",
            })
        skill_name = name
        skill_desc = description or f"Imported skill: {name}"
        skill_md = _wrap_with_frontmatter(content, name, skill_desc)

    # Upload to S3
    skill_id = str(uuid.uuid4())[:8]
    s3 = boto3.client("s3", region_name=REGION)

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"skills/{skill_id}/SKILL.md",
        Body=skill_md.encode("utf-8"),
        ContentType="text/markdown",
    )

    _update_skill_index(s3, {
        "id": skill_id,
        "name": skill_name,
        "description": skill_desc,
    })

    return json.dumps({
        "skill_id": skill_id,
        "name": skill_name,
        "description": skill_desc,
        "had_frontmatter": meta is not None,
        "status": "imported",
    }, indent=2)
