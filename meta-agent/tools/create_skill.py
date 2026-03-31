"""create_skill — Create a Skill in OpenClaw/AgentSkills compatible format."""

import json
import textwrap

import boto3
from strands import tool

from config import REGION, S3_BUCKET


@tool
def create_skill(
    skill_name: str,
    description: str,
    skill_type: str,
    instructions: str,
    input_params: str = "",
    script_code: str = "",
    source: str = "natural-language",
) -> str:
    """Create a new Skill in OpenClaw/AgentSkills compatible SKILL.md format.

    Args:
        skill_name: Unique identifier for the skill (kebab-case recommended).
        description: Brief description of what the skill does.
        skill_type: Either "prompt" or "script".
        instructions: Markdown instructions for how the skill works.
        input_params: Description of input parameters the skill accepts.
        script_code: For script-type skills, the Python code to include.
        source: Origin of the skill: "natural-language", "distilled", or "imported".

    Returns:
        JSON with skill_id, s3_path, and status.
    """
    import uuid

    skill_id = str(uuid.uuid4())[:8]

    # Build SKILL.md content
    frontmatter = textwrap.dedent(f"""\
        ---
        name: "{skill_name}"
        description: "{description}"
        user-invocable: true
        metadata: '{{"openclaw":{{"emoji":"🔧","requires":{{}}}}}}'
        source: "{source}"
        type: "{skill_type}"
        ---
    """)

    body = f"# {skill_name}\n\n{instructions}"

    if input_params:
        body += f"\n\n## Input Parameters\n{input_params}"

    skill_md = frontmatter + "\n" + body

    # Upload to S3
    s3 = boto3.client("s3", region_name=REGION)
    s3_prefix = f"skills/{skill_id}"

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"{s3_prefix}/SKILL.md",
        Body=skill_md.encode("utf-8"),
        ContentType="text/markdown",
    )

    # Upload script if provided
    if skill_type == "script" and script_code:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=f"{s3_prefix}/script.py",
            Body=script_code.encode("utf-8"),
            ContentType="text/x-python",
        )

    result = {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "type": skill_type,
        "s3_path": f"s3://{S3_BUCKET}/{s3_prefix}/",
        "status": "created",
    }

    return json.dumps(result, indent=2)
