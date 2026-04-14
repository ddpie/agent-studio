"""update_agent — Update an existing agent's code and configuration in-place."""

import json
import re
from datetime import datetime, timezone

import boto3
from strands import tool

from config import MODEL_ID, REGION, S3_BUCKET, AGENT_ROLE_ARN, AGENTS_TABLE
from deploy import build_deployment_package_v2, upload_deployment, wait_for_ready, validate_agent_files, build_skill_prompt_section
from templates.agent_template_v2 import MAIN_PY_TEMPLATE, MAIN_PY_MCP_TEMPLATE, TOOLS_PY_HEADER
from templates.prompt_templates import get_template_prompt, BASE_GUIDELINES
from tools_library.registry import get_tool_code_by_func_name as _get_builtin_code
from tools.create_agent import _get_workspace_mcp_policy, _check_mcp_policy, _resolve_mcp_endpoints

# Fields that require AgentCore redeploy when changed
_REDEPLOY_FIELDS = {"system_prompt", "tool_definitions", "tool_names", "template_id", "mcp_targets"}


def _clean_tool_definitions(defs: str) -> str:
    """Strip template boilerplate from tool_definitions, keeping only @tool functions."""
    if not defs or "@tool" not in defs:
        return defs
    lines = defs.split("\n")
    result = []
    in_tool = False
    for line in lines:
        stripped = line.lstrip()
        if stripped == "@tool":
            in_tool = True
            result.append(line)
            continue
        if in_tool:
            if stripped and not line[0:1] in (" ", "\t") and not stripped.startswith("def ") and not stripped.startswith("#") and not stripped.startswith("@"):
                in_tool = False
                if stripped.startswith(("async def _", "def _", "@app.", "import json as _json",
                                        "import base64 as _b64", "if __name__", "app.run()")):
                    continue
                if stripped.startswith("import ") or stripped.startswith("from "):
                    result.append(line)
                    continue
            else:
                result.append(line)
                continue
        else:
            if stripped.startswith(("async def _", "def _", "@app.", "import json as _json",
                                    "import base64 as _b64", "if __name__", "app.run()",
                                    "yield chunk", "yield event")):
                continue
            if stripped.startswith("import ") or stripped.startswith("from ") or not stripped:
                if not stripped.startswith("import json as _json"):
                    result.append(line)
    return "\n".join(result).strip()


@tool
def update_agent(
    agent_id: str,
    agent_name: str = "",
    description: str = "",
    display_name: str = "",
    system_prompt: str = "",
    tool_definitions: str = "",
    tool_names: str = "",
    welcome_message: str = "",
    suggestions: str = "",
    template_id: str = "",
    gateway_url: str = "",
    mcp_targets: str = "",
    supports_images: bool = False,
    staging_key: str = "",
) -> str:
    """Update an existing agent's code and configuration without deleting and recreating.

    The agent ID and ARN remain unchanged. Only provide fields you want to update.
    If only metadata fields change (description, welcome_message, suggestions, display_name),
    the agent will NOT be redeployed — only S3 metadata is updated (instant).

    Args:
        agent_id: The agent runtime ID to update.
        agent_name: The agent name (must match existing).
        description: Updated description. Leave empty to keep existing.
        display_name: Updated display name. Leave empty to keep existing.
        system_prompt: Updated system prompt. Leave empty to keep existing.
        tool_definitions: Updated Python @tool functions. Leave empty to keep existing.
        tool_names: Updated comma-separated tool names. Leave empty to keep existing.
        welcome_message: Updated welcome message.
        suggestions: Updated suggestions separated by |.
        template_id: Prompt template to apply.
        gateway_url: Optional MCP Gateway URL (deprecated, use mcp_targets).
        mcp_targets: Comma-separated MCP target names (e.g. "cloudwatch,iam"). Validated against workspace policy.
        supports_images: Whether this agent can process image inputs.
        staging_key: S3 key to a JSON file containing all update parameters.

    Returns:
        JSON with update status.
    """
    s3 = boto3.client("s3", region_name=REGION)

    # If staging_key provided, read params from S3
    if staging_key:
        try:
            obj = s3.get_object(Bucket=S3_BUCKET, Key=staging_key)
            staged = json.loads(obj["Body"].read().decode("utf-8"))
            agent_name = staged.get("name", agent_name) or agent_name
            description = staged.get("description", description) or description
            display_name = staged.get("display_name", display_name) or display_name
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
            mcp_targets = staged.get("mcp_targets", mcp_targets) or mcp_targets
            if isinstance(mcp_targets, list):
                mcp_targets = ",".join(mcp_targets)
        except Exception as e:
            return json.dumps({"error": f"Failed to read staging config: {e}"})

    # Read skills from staging config
    skills_config = staged.get("skills", []) if staging_key else []
    skills_data = []
    skill_scripts = {}

    # Parse mcp_targets and validate against workspace policy
    mcp_targets_list = [t.strip() for t in mcp_targets.split(",") if t.strip()] if mcp_targets else []
    mcp_endpoints = []
    if mcp_targets_list:
        workspace_id = staged.get("workspace_id", "") if staging_key else ""
        if not workspace_id:
            workspace_id = getattr(__import__('tools.create_agent', fromlist=['_workspace_id']), '_workspace_id', '')
        policy = _get_workspace_mcp_policy(workspace_id)
        denied = _check_mcp_policy(mcp_targets_list, policy)
        if denied:
            return json.dumps({"error": f"MCP targets not allowed in this workspace: {denied}"})
        mcp_endpoints = _resolve_mcp_endpoints(mcp_targets_list)

    if skills_config:
        s3_client = boto3.client("s3", region_name=REGION)
        for skill_entry in skills_config:
            skill_id = skill_entry.get("id", "")
            skill_name = skill_entry.get("name", "").replace(" ", "_").replace("-", "_")

            skill_md_content = ""
            try:
                skill_prefix = f"agents/{agent_id}/skills/{skill_id}/"
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

    # Permission is enforced by the CRUD Lambda (JWT + workspace RBAC)
    # Meta-Agent runs as a system service, no per-user ownership check here
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)
    record = table.get_item(Key={"agentId": agent_id}).get("Item")
    if not record:
        return json.dumps({"error": f"Agent {agent_id} not found"})

    # Read existing metadata
    existing_metadata = {}
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/metadata.json")
        existing_metadata = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        pass

    # Merge: use new values if provided, else keep existing
    final_prompt = system_prompt or existing_metadata.get("system_prompt", "You are a helpful assistant.")
    final_tools_def = _clean_tool_definitions(tool_definitions) if tool_definitions else ""
    final_tools_names = tool_names or ""
    final_desc = description or existing_metadata.get("description", "")
    final_display = display_name or existing_metadata.get("display_name", agent_name)
    final_welcome = welcome_message or existing_metadata.get("welcome_message", "")
    final_suggestions = suggestions or "|".join(existing_metadata.get("suggestions", []))
    final_template = template_id or existing_metadata.get("template_id", "")

    # Preserve existing MCP config when mcp_targets is not explicitly provided
    if not mcp_targets_list:
        try:
            cfg_obj = s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/config.json")
            existing_config = json.loads(cfg_obj["Body"].read().decode("utf-8"))
            mcp_targets_list = existing_config.get("mcp_targets", [])
            mcp_endpoints = existing_config.get("mcp_endpoints", [])
        except Exception:
            pass
        if not mcp_targets_list:
            mcp_targets_list = existing_metadata.get("mcp_targets", [])
        # Re-resolve endpoints if we have targets but no endpoints
        if mcp_targets_list and not mcp_endpoints:
            mcp_endpoints = _resolve_mcp_endpoints(mcp_targets_list)

    # Diff: check if any redeploy-triggering field actually changed
    needs_redeploy = False
    if system_prompt and system_prompt != existing_metadata.get("system_prompt", ""):
        needs_redeploy = True
    if tool_definitions:
        needs_redeploy = True
    if tool_names and tool_names != ",".join(existing_metadata.get("tools", [])):
        needs_redeploy = True
    if template_id and template_id != existing_metadata.get("template_id", ""):
        needs_redeploy = True
    if mcp_targets and mcp_targets != ",".join(existing_metadata.get("mcp_targets", [])):
        needs_redeploy = True

    status = "metadata_only"

    if needs_redeploy:
        # Apply template if specified
        if final_template:
            base_prompt = get_template_prompt(final_template)
            final_prompt = base_prompt + "\n\n## Specific Instructions\n" + final_prompt
        elif BASE_GUIDELINES not in final_prompt:
            final_prompt = final_prompt + "\n" + BASE_GUIDELINES

        # Build tool_names list
        tool_names_list = [t.strip() for t in final_tools_names.split(",") if t.strip()]

        # Inject built-in tool code for tools in tool_names but not in tool_definitions
        custom_code = final_tools_def or ""
        defined_funcs = set(re.findall(r'@tool\s*\ndef\s+(\w+)\s*\(', custom_code)) if custom_code.strip() else set()
        builtin_code_parts = []
        for tname in tool_names_list:
            if tname not in defined_funcs:
                code = _get_builtin_code(tname)
                if code:
                    builtin_code_parts.append(code.strip())

        # Generate multi-file structure (no more repr()!)
        main_py = MAIN_PY_MCP_TEMPLATE if mcp_endpoints else MAIN_PY_TEMPLATE
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
        if mcp_endpoints:
            config_data["mcp_endpoints"] = mcp_endpoints
        if mcp_targets_list:
            config_data["mcp_targets"] = mcp_targets_list
        config_json = json.dumps(config_data, indent=2, ensure_ascii=False)

        # Validate each file independently
        validation = validate_agent_files(main_py, tools_py, prompt_txt, config_json)
        if not validation["valid"]:
            return json.dumps({"error": "Code validation failed", "details": validation["errors"]})

        # Build and upload
        package = build_deployment_package_v2(main_py, tools_py, prompt_txt, config_json, skill_scripts=skill_scripts)
        s3_key = upload_deployment(agent_id, package)

        # Update runtime in-place
        control = boto3.client("bedrock-agentcore-control", region_name=REGION)
        control.update_agent_runtime(
            agentRuntimeId=agent_id,
            roleArn=AGENT_ROLE_ARN,
            agentRuntimeArtifact={
                "codeConfiguration": {
                    "code": {"s3": {"bucket": S3_BUCKET, "prefix": s3_key}},
                    "runtime": "PYTHON_3_10",
                    "entryPoint": ["main.py"],
                }
            },
            networkConfiguration={"networkMode": "PUBLIC"},
            filesystemConfigurations=[{
                "sessionStorage": {
                    "mountPath": "/mnt/workspace"
                }
            }],
        )

        status = wait_for_ready(agent_id)

    # Always update metadata
    suggestion_list = [s.strip() for s in final_suggestions.split("|") if s.strip()]
    # Use the actual deployed tools_py (includes injected built-in code) if redeployed
    if needs_redeploy:
        final_tools_def_for_meta = tools_py.replace(TOOLS_PY_HEADER, "").strip()
    else:
        final_tools_def_for_meta = final_tools_def or existing_metadata.get("tool_definitions", "")

    deployed_skill_hashes = {}
    for skill_entry in skills_config:
        deployed_skill_hashes[skill_entry["id"]] = skill_entry.get("contentHash", "")

    metadata = {
        "agent_id": agent_id,
        "name": agent_name,
        "display_name": final_display,
        "description": final_desc,
        "model_id": MODEL_ID,
        "system_prompt": final_prompt,
        "tool_definitions": final_tools_def_for_meta,
        "welcome_message": final_welcome or f"I'm {agent_name}. {final_desc}",
        "suggestions": suggestion_list,
        "template_id": final_template,
        "tools": [t.strip() for t in final_tools_names.split(",") if t.strip()],
        "supports_images": supports_images or existing_metadata.get("supports_images", False),
        "skills": skills_config,
        "deployedSkillHashes": deployed_skill_hashes,
        "mcp_targets": mcp_targets_list,
        "created_at": existing_metadata.get("created_at", datetime.now(timezone.utc).isoformat()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"agents/{agent_id}/metadata.json",
        Body=json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    # Update DynamoDB — sync all frontend-visible fields
    update_expr_parts = ["displayName = :dn", "description = :desc", "updated_at = :ua"]
    expr_values = {
        ":dn": final_display,
        ":desc": description or existing_metadata.get("description", ""),
        ":ua": metadata["updated_at"],
    }
    table.update_item(
        Key={"agentId": agent_id},
        UpdateExpression="SET " + ", ".join(update_expr_parts),
        ExpressionAttributeValues=expr_values,
    )

    return json.dumps({
        "agent_id": agent_id,
        "agent_name": agent_name,
        "status": status,
        "action": "redeployed" if needs_redeploy else "metadata_updated",
        "needs_redeploy": needs_redeploy,
    }, indent=2)
