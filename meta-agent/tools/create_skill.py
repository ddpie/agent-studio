"""create_skill — Create a Skill in OpenClaw/AgentSkills compatible format.

Writes to both:
  - ``s3://{bucket}/skills/{skill_id}/SKILL.md`` (and optional ``script.py``)
  - ``agent-studio-skills`` DynamoDB row with ``workspace_id`` set

The DDB row is what every downstream consumer (``list_skills``,
``read_skill_file``, ``sync_agent_skill``, Lambda CRUD ``GET /skills``)
queries by ``workspace-index`` to enforce cross-tenant isolation. Skipping
the DDB write — as earlier versions did — produced skills that were
invisible to their own workspace's list while their S3 files stayed
publicly addressable in the shared ``skills/`` prefix, so any caller with
the id could read/edit/delete them regardless of workspace.
"""

import json
import textwrap
import uuid
from datetime import datetime, timezone

import boto3
from config import REGION, S3_BUCKET
from strands import tool

from tools._scope import (
    ROLE_EDITOR,
    current_caller,
    current_workspace,
    require_role,
)

_SKILLS_TABLE = "agent-studio-skills"


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
        instructions: Markdown instructions for how the skill works. For
            ``skill_type="script"``, describe invocation as
            ``run_skill_script(skill_name=..., script="script.py",
            args=...)`` — do NOT phrase it as "call function X" (that's
            a different calling convention that won't work).
        input_params: Description of input parameters the skill accepts.
        script_code: For ``skill_type="script"``, the Python code. MUST
            be a standalone CLI script that reads ``sys.argv[1:]`` and
            ``print``s results. Runs in Code Interpreter sandbox via
            ``runpy.run_path``. Do NOT use ``@tool`` decorator or
            ``from strands import tool`` — those are for agent
            tool_definitions (a different execution context) and will
            fail at runtime with ``ModuleNotFoundError: No module named
            'strands'``. See meta-agent.md "Skill scripts: standalone
            CLI, NOT @tool" for the full template and layout rules.
        source: Origin of the skill: "natural-language", "distilled", or "imported".

    Returns:
        JSON with skill_id, s3_path, and status.
    """
    deny = require_role(ROLE_EDITOR)
    if deny:
        return json.dumps(deny)

    if skill_type not in ("prompt", "script"):
        return json.dumps({"error": "skill_type must be 'prompt' or 'script'."})

    ws_id = current_workspace()
    caller = current_caller() or "unknown"

    skill_id = str(uuid.uuid4())[:8]

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

    s3 = boto3.client("s3", region_name=REGION)
    s3_prefix = f"skills/{skill_id}"

    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"{s3_prefix}/SKILL.md",
        Body=skill_md.encode("utf-8"),
        ContentType="text/markdown",
    )

    if skill_type == "script" and script_code:
        s3.put_object(
            Bucket=S3_BUCKET,
            Key=f"{s3_prefix}/script.py",
            Body=script_code.encode("utf-8"),
            ContentType="text/x-python",
        )

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    # Schema mirrors lambda/crud/skills.py::create_skill so the two write
    # paths stay interchangeable and list_skills / GSI queries don't need
    # to care which created the row.
    item = {
        "skillId": skill_id,
        "workspace_id": ws_id,
        "name": skill_name,
        "description": description,
        "type": skill_type,
        # Script skills need manual approval before they can be attached to
        # agents; prompt skills are inert text and don't.
        "approved": skill_type != "script",
        "visibility": "private",
        "tags": [],
        "deleted": False,
        "source": source,
        "created_by": caller,
        "created_at": now,
        "updated_at": now,
    }
    try:
        boto3.resource("dynamodb", region_name=REGION).Table(_SKILLS_TABLE).put_item(Item=item)
    except Exception as e:
        return json.dumps({"error": f"Skill metadata write failed: {e}"})

    result = {
        "skill_id": skill_id,
        "skill_name": skill_name,
        "type": skill_type,
        "s3_path": f"s3://{S3_BUCKET}/{s3_prefix}/",
        "workspace_id": ws_id,
        "status": "created",
    }

    return json.dumps(result, indent=2)
