"""create_agent — Generate code, package, and deploy a new Agent to AgentCore Runtime."""

import json
import re
from datetime import datetime, timezone

import boto3
from strands import tool

from config import MODEL_ID, REGION, S3_BUCKET, AGENTS_TABLE, PERMISSION_TIER_ROLES, DEFAULT_PERMISSION_TIER
from deploy import build_deployment_package_v2, upload_deployment, create_runtime, wait_for_ready, validate_agent_files, build_skill_prompt_section
from templates.agent_template_v2 import MAIN_PY_TEMPLATE, MAIN_PY_MCP_TEMPLATE, TOOLS_PY_HEADER
from templates.prompt_templates import get_template_prompt, get_template_names, BASE_GUIDELINES
from tools_library.registry import get_tool_code_by_func_name as _get_builtin_code


@tool
def list_prompt_templates() -> str:
    """List available system prompt templates for creating agents.

    Returns:
        JSON array of templates with id, name, and description.
    """
    return json.dumps(get_template_names(), indent=2, ensure_ascii=False)


@tool
def create_agent(
    agent_name: str,
    description: str = "",
    system_prompt: str = "",
    tool_definitions: str = "",
    tool_names: str = "",
    welcome_message: str = "",
    suggestions: str = "",
    template_id: str = "",
    gateway_url: str = "",
    supports_images: bool = False,
    permission_tier: str = "",
    staging_key: str = "",
) -> str:
    """Create and deploy a new AI agent to AgentCore Runtime.

    Args:
        agent_name: Name for the agent (alphanumeric only, max 36 chars).
        description: Brief description of what the agent does.
        system_prompt: The system prompt defining the agent's behavior.
        tool_definitions: Python code defining @tool decorated functions.
        tool_names: Comma-separated list of tool function names.
        welcome_message: Welcome message shown when user opens this agent's chat.
        suggestions: Three suggested prompts separated by | (e.g., "Ask about X|Try Y|Help with Z").
        template_id: Optional prompt template to use as base.
        gateway_url: Optional AgentCore Gateway MCP URL.
        supports_images: Whether this agent can process image inputs.
        permission_tier: IAM permission level: basic/readonly/data-access. Default: readonly.
        staging_key: S3 key to a JSON file containing all parameters.

    Returns:
        JSON with agent_id, agent_arn, status.
    """
    # If staging_key provided, read params from S3
    if staging_key:
        s3_client = boto3.client("s3", region_name=REGION)
        try:
            obj = s3_client.get_object(Bucket=S3_BUCKET, Key=staging_key)
            staged = json.loads(obj["Body"].read().decode("utf-8"))
            agent_name = staged.get("name", agent_name) or agent_name
            description = staged.get("description", description) or description
            system_prompt = staged.get("system_prompt", system_prompt) or system_prompt
            tool_definitions = staged.get("tool_definitions", tool_definitions) or tool_definitions
            tool_names = staged.get("tool_names", tool_names) or tool_names
            welcome_message = staged.get("welcome_message", welcome_message) or welcome_message
            suggestions = staged.get("suggestions", suggestions)
            if isinstance(suggestions, list):
                suggestions = "|".join(suggestions)
            template_id = staged.get("template_id", template_id) or template_id
            supports_images = staged.get("supports_images", supports_images)
            gateway_url = staged.get("gateway_url", gateway_url) or gateway_url
        except Exception as e:
            return json.dumps({"error": f"Failed to read staging config: {e}"})

    # Read skills from staging config
    skills_config = staged.get("skills", []) if staging_key else []
    skills_data = []
    skill_scripts = {}

    if skills_config:
        s3_client = boto3.client("s3", region_name=REGION)
        for skill_entry in skills_config:
            skill_id = skill_entry.get("id", "")
            skill_name = skill_entry.get("name", "").replace(" ", "_").replace("-", "_")
            agent_name_for_path = staged.get("agent_id", agent_name) if staging_key else agent_name

            skill_md_content = ""
            try:
                skill_prefix = f"agents/{agent_name_for_path}/skills/{skill_id}/"
                md_key = f"{skill_prefix}SKILL.md"
                md_obj = s3_client.get_object(Bucket=S3_BUCKET, Key=md_key)
                skill_md_content = md_obj["Body"].read().decode("utf-8")
            except Exception as e:
                import sys
                print(f"WARNING: Failed to read SKILL.md for skill {skill_id}: {e}", file=sys.stderr)

            skills_data.append({
                "name": skill_entry.get("name", skill_id),
                "description": skill_entry.get("description", ""),
                "skill_md_content": skill_md_content,
            })

            try:
                resp = s3_client.list_objects_v2(
                    Bucket=S3_BUCKET,
                    Prefix=f"{skill_prefix}scripts/",
                )
                script_files = {}
                for obj in resp.get("Contents", []):
                    key = obj["Key"]
                    filename = key.split("/")[-1]
                    if filename:
                        content = s3_client.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read().decode("utf-8")
                        script_files[filename] = content
                if script_files:
                    skill_scripts[skill_name] = script_files
            except Exception as e:
                import sys
                print(f"WARNING: Failed to read scripts for skill {skill_id}: {e}", file=sys.stderr)

    # Apply template if specified
    if template_id:
        base_prompt = get_template_prompt(template_id)
        final_prompt = base_prompt + "\n\n## Specific Instructions\n" + system_prompt
    else:
        final_prompt = system_prompt + "\n" + BASE_GUIDELINES

    # Build tool_names list
    tool_names_list = [t.strip() for t in tool_names.split(",") if t.strip()]

    # Inject built-in tool code for tools declared in tool_names but not in tool_definitions
    custom_code = tool_definitions or ""
    defined_funcs = set(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', custom_code)) if custom_code.strip() else set()
    builtin_code_parts = []
    for tname in tool_names_list:
        if tname not in defined_funcs:
            code = _get_builtin_code(tname)
            if code:
                builtin_code_parts.append(code.strip())

    # Generate multi-file structure (no more repr() or string template substitution!)
    main_py = MAIN_PY_MCP_TEMPLATE if gateway_url else MAIN_PY_TEMPLATE
    tools_py = TOOLS_PY_HEADER + "\n\n".join(builtin_code_parts + ([custom_code] if custom_code.strip() else []))

    # Inject skill content into prompt (progressive disclosure)
    skill_prompt_section = build_skill_prompt_section(skills_data)
    if skill_prompt_section:
        prompt_txt = final_prompt + skill_prompt_section
    else:
        prompt_txt = final_prompt

    config_data = {
        "model_id": MODEL_ID,
        "tool_names": tool_names_list,
    }
    if gateway_url:
        config_data["gateway_url"] = gateway_url
    config_json = json.dumps(config_data, indent=2, ensure_ascii=False)

    # Validate each file independently
    validation = validate_agent_files(main_py, tools_py, prompt_txt, config_json)
    if not validation["valid"]:
        return json.dumps({"error": "Code validation failed", "details": validation["errors"]})

    # Build, upload, deploy
    tier = permission_tier or DEFAULT_PERMISSION_TIER
    role_arn = PERMISSION_TIER_ROLES.get(tier, PERMISSION_TIER_ROLES[DEFAULT_PERMISSION_TIER])
    package = build_deployment_package_v2(main_py, tools_py, prompt_txt, config_json, skill_scripts=skill_scripts)
    s3_key = upload_deployment(agent_name, package)
    result = create_runtime(agent_name, description, s3_key, role_arn)

    agent_id = result["agent_id"]

    # Save metadata.json
    suggestion_list = [s.strip() for s in suggestions.split("|") if s.strip()] if suggestions else []

    deployed_skill_hashes = {}
    for skill_entry in skills_config:
        deployed_skill_hashes[skill_entry["id"]] = skill_entry.get("contentHash", "")

    metadata = {
        "agent_id": agent_id,
        "name": agent_name,
        "display_name": agent_name,
        "description": description,
        "model_id": MODEL_ID,
        "system_prompt": final_prompt,
        "tool_definitions": tools_py.replace(TOOLS_PY_HEADER, "").strip(),
        "welcome_message": welcome_message or f"I'm {agent_name}. {description}",
        "suggestions": suggestion_list,
        "template_id": template_id,
        "tools": tool_names_list,
        "tool_names": ",".join(tool_names_list),
        "supports_images": supports_images,
        "skills": skills_config,
        "deployedSkillHashes": deployed_skill_hashes,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    s3 = boto3.client("s3", region_name=REGION)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"agents/{agent_id}/metadata.json",
        Body=json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    # Write to DynamoDB
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)
    # workspace_id from staging config, or from the invoke payload (set by main.py)
    workspace_id = staged.get("workspace_id", "") if staging_key else ""
    if not workspace_id:
        workspace_id = getattr(__import__('tools.create_agent', fromlist=['_workspace_id']), '_workspace_id', '')
    now = datetime.now(timezone.utc).isoformat()
    caller = getattr(__import__('tools.create_agent', fromlist=['_caller_id']), '_caller_id', 'unknown')
    item = {
        "agentId": agent_id,
        "agentName": agent_name,
        "displayName": agent_name,
        "description": description,
        "visibility": "private",
        "permissionTier": tier,
        "created_at": now,
        "updated_at": now,
        "created_by": caller,
    }
    if workspace_id:
        item["workspace_id"] = workspace_id
    table.put_item(Item=item)

    # Wait for ready
    status = wait_for_ready(result["agent_id"])
    result["status"] = status
    result["agent_name"] = agent_name

    return json.dumps(result, indent=2)
