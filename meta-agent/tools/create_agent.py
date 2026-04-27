"""create_agent — Generate code, package, and deploy a new Agent to AgentCore Runtime."""

import json
import os
import re
from datetime import datetime, timezone

import boto3
from strands import tool

from config import MODEL_ID, REGION, S3_BUCKET, AGENTS_TABLE, PERMISSION_TIER_ROLES, DEFAULT_PERMISSION_TIER
from tools._workspace import _get_agent_role_arn
from deploy import build_deployment_package_v2, upload_deployment, create_runtime, wait_for_ready, validate_agent_files, build_skill_prompt_section
from templates.agent_template_v2 import MAIN_PY_TEMPLATE, MAIN_PY_MCP_TEMPLATE, TOOLS_PY_HEADER
from templates.prompt_templates import get_base_guidelines


def _default_welcome(agent_name: str, description: str) -> str:
    """Language-matched fallback when the user didn't specify welcome_message.

    Mirrors the BASE_GUIDELINES bilingual split — a Chinese-speaking
    creator building an agent without a welcome line gets a Chinese
    welcome, not an English "I'm foo. ...". Reads the creator's
    language from tools._scope.
    """
    from tools._scope import current_creator_language
    lang = (current_creator_language() or "").strip().lower()
    if lang.startswith("zh"):
        return f"我是 {agent_name}。{description}" if description else f"我是 {agent_name}。"
    return f"I'm {agent_name}. {description}" if description else f"I'm {agent_name}."
from tools_library.registry import get_tool_code_by_func_name as _get_builtin_code


def _get_workspace_mcp_policy(workspace_id: str) -> dict:
    """Fetch MCP policy from workspace metadata."""
    if not workspace_id:
        return {"mode": "all"}
    table = boto3.resource("dynamodb", region_name=REGION).Table("agent-studio-workspaces")
    item = table.get_item(Key={"workspaceId": workspace_id, "sk": "META"}).get("Item", {})
    return item.get("mcpPolicy", {"mode": "all"})


def _check_mcp_policy(targets: list, policy: dict) -> list:
    """Return list of targets denied by the workspace policy."""
    mode = policy.get("mode", "all")
    if mode == "all":
        return []
    if mode == "allowlist":
        allowed = set(policy.get("allowedTargets", []))
        return [t for t in targets if t not in allowed]
    if mode == "denylist":
        denied = set(policy.get("deniedTargets", []))
        return [t for t in targets if t in denied]
    return []


def _resolve_mcp_endpoints(target_names: list) -> list:
    """Resolve MCP target names to per-workspace endpoint configs.

    v4 per-workspace model (spec §7.4): Runtime endpoints are read from the
    current workspace's DDB META item (``mcp_runtimes`` map). Agent config.json
    receives the literal runtime_arn + endpoint — the Agent does NOT call
    list_agent_runtimes at runtime. Remote targets are resolved via registry.

    Raises ValueError if a runtime target is not READY in this workspace.

    Args:
        target_names: List of short target names without the "mcp-" prefix
            (e.g. ["cloudwatch", "aws-api", "cloudtrail"]).

    Returns:
        List of endpoint dicts:
        - Runtime: {"type": "runtime", "name": "cloudwatch",
                    "runtime_name": "asmcp_<ws12>_cloudwatch",
                    "runtime_arn": "arn:aws:bedrock-agentcore:...",
                    "runtime_endpoint": "https://...",
                    "auth": "runtime"}
        - Remote:  {"type": "remote", "name": "aws-api",
                    "url": "https://...", "auth": "aws-mcp"|"none"}
    """
    # Load registry (remote_targets + runtime_targets metadata)
    remote_map: dict[str, dict] = {}
    try:
        s3 = boto3.client("s3", region_name=REGION)
        import yaml
        resp = s3.get_object(Bucket=S3_BUCKET, Key="mcp-runtime/mcp-registry.yaml")
        registry = yaml.safe_load(resp["Body"].read().decode())
        for rt in (registry.get("remote_targets") or []):
            if rt.get("enabled"):
                auth = "none" if rt.get("auth") == "none" else "aws-mcp"
                remote_map[rt["name"]] = {"url": rt["endpoint"], "auth": auth}
    except Exception:
        # Fallback: hardcoded known remotes
        remote_map = {
            "aws-api": {"url": "https://aws-mcp.us-east-1.api.aws/mcp", "auth": "aws-mcp"},
            "aws-knowledge": {"url": "https://knowledge-mcp.global.api.aws", "auth": "none"},
        }

    # Runtime targets: read from per-workspace DDB META.
    runtime_targets = [t for t in target_names if t not in remote_map]
    mcp_runtimes: dict[str, dict] = {}
    if runtime_targets:
        from tools._scope import current_workspace
        ws_id = current_workspace()
        if not ws_id:
            raise ValueError(
                "No workspace context — cannot resolve per-workspace MCP runtimes."
            )
        WORKSPACES_TABLE = os.getenv("WORKSPACES_TABLE", "agent-studio-workspaces")
        ddb = boto3.resource("dynamodb", region_name=REGION)
        item = ddb.Table(WORKSPACES_TABLE).get_item(
            Key={"workspaceId": ws_id, "sk": "META"},
        ).get("Item") or {}
        mcp_runtimes = item.get("mcp_runtimes") or {}

    endpoints = []
    not_ready: list[str] = []
    for target in target_names:
        if target in remote_map:
            endpoints.append({
                "type": "remote",
                "name": target,
                "url": remote_map[target]["url"],
                "auth": remote_map[target]["auth"],
            })
            continue
        entry = mcp_runtimes.get(target)
        if not entry or entry.get("status") not in ("READY", "ACTIVE"):
            not_ready.append(target)
            continue
        endpoints.append({
            "type": "runtime",
            "name": target,
            "runtime_name": entry.get("runtime_name", ""),
            "runtime_arn": entry.get("runtime_arn", ""),
            "runtime_endpoint": entry.get("runtime_endpoint", ""),
            "auth": "runtime",
        })

    if not_ready:
        raise ValueError(
            f"MCP targets not READY in this workspace: {', '.join(not_ready)}. "
            f"Enable them via /#/mcp (or enable_mcp tool) first."
        )

    return endpoints


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
    mcp_targets: str = "",
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
        template_id: Deprecated; ignored. Prompt templates have been retired — write the full system_prompt yourself.
        gateway_url: Optional AgentCore Gateway MCP URL (deprecated, use mcp_targets).
        mcp_targets: Comma-separated MCP target names (e.g. "cloudwatch,iam"). Validated against workspace policy.
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
            mcp_targets = staged.get("mcp_targets", mcp_targets) or mcp_targets
            if isinstance(mcp_targets, list):
                mcp_targets = ",".join(mcp_targets)
        except Exception as e:
            return json.dumps({"error": f"Failed to read staging config: {e}"})

    # Read skills from staging config
    skills_config = staged.get("skills", []) if staging_key else []
    skills_data = []

    # Resolve workspace_id early — needed for MCP policy checks and IAM role selection.
    workspace_id = staged.get("workspace_id", "") if staging_key else ""
    if not workspace_id:
        workspace_id = getattr(__import__('tools.create_agent', fromlist=['_workspace_id']), '_workspace_id', '')

    # Parse mcp_targets and validate against workspace policy
    mcp_targets_list = [t.strip() for t in mcp_targets.split(",") if t.strip()] if mcp_targets else []
    mcp_endpoints = []
    if mcp_targets_list:
        policy = _get_workspace_mcp_policy(workspace_id)
        denied = _check_mcp_policy(mcp_targets_list, policy)
        if denied:
            return json.dumps({"error": f"MCP targets not allowed in this workspace: {denied}"})
        mcp_endpoints = _resolve_mcp_endpoints(mcp_targets_list)

    # Skill files are NOT packaged into the deployment zip. They live at
    # ``agents/{agent_id}/skills/{skill_id}/`` in S3 (written by the CRUD
    # Lambda) and are fetched on demand by the agent's load_skill /
    # run_skill_script at runtime. That decouples skill revisions from
    # agent redeploys — change a skill, all consuming agents see it on
    # next invocation. We still need SKILL.md content in-hand to build
    # the prompt's progressive-disclosure section.
    if skills_config:
        s3_client = boto3.client("s3", region_name=REGION)
        for skill_entry in skills_config:
            skill_id = skill_entry.get("id", "")
            agent_name_for_path = staged.get("agent_id", agent_name) if staging_key else agent_name

            skill_md_content = ""
            try:
                md_key = f"agents/{agent_name_for_path}/skills/{skill_id}/SKILL.md"
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

    # Compose the final system prompt. template_id is deliberately ignored
    # here — the pre-canned 5-template scheme used to prepend an English
    # block in front of the user's (often Chinese) prompt, producing
    # mixed-language agents. The Meta-Agent now writes the full
    # domain-specific prompt itself in the caller's language, and we only
    # tack on the shared BASE_GUIDELINES (behavioral rules that every
    # agent should follow regardless of domain), picking the zh vs en
    # variant based on the creator's UI language.
    from tools._scope import current_creator_language
    final_prompt = system_prompt + "\n" + get_base_guidelines(current_creator_language())

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

    # Memory config is now read from DDB at runtime by the agent template,
    # so we no longer bake it into config.json. The CRUD Lambda writes the
    # memory field to the agents DDB table; the agent reads it on each invoke.

    config_json = json.dumps(config_data, indent=2, ensure_ascii=False)

    # Validate each file independently
    validation = validate_agent_files(main_py, tools_py, prompt_txt, config_json)
    if not validation["valid"]:
        return json.dumps({"error": "Code validation failed", "details": validation["errors"]})

    # Build, upload, deploy
    # Workspace custom role takes precedence; fall back to shared role.
    role_arn = _get_agent_role_arn(workspace_id)
    tier = permission_tier or DEFAULT_PERMISSION_TIER
    package = build_deployment_package_v2(main_py, tools_py, prompt_txt, config_json)
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
        "welcome_message": welcome_message or _default_welcome(agent_name, description),
        "suggestions": suggestion_list,
        "template_id": template_id,
        "tools": tool_names_list,
        "tool_names": ",".join(tool_names_list),
        "supports_images": supports_images,
        "skills": skills_config,
        "deployedSkillHashes": deployed_skill_hashes,
        "mcp_targets": mcp_targets_list,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    s3 = boto3.client("s3", region_name=REGION)
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"agents/{agent_id}/metadata.json",
        Body=json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    # Mirror system_prompt and tool_definitions to standalone S3 files so
    # the frontend edit page's fetchAgentMetadata (agent-metadata.ts) can
    # show what's currently deployed. update_agent already does this; the
    # two tools must stay symmetric or Meta-Agent-created agents open in
    # the edit UI with empty prompt and empty tool code (the files 404 and
    # the frontend's .catch(() => "") silently swallows them). The strings
    # written here match the shape update_agent uses so round-tripping
    # through create → update → reload stays idempotent.
    tool_definitions_source = tools_py.replace(TOOLS_PY_HEADER, "").strip()
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"agents/{agent_id}/system_prompt.txt",
        Body=final_prompt.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"agents/{agent_id}/tool_definitions.py",
        Body=tool_definitions_source.encode("utf-8"),
        ContentType="text/x-python; charset=utf-8",
    )

    # Write to DynamoDB
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)
    # workspace_id already resolved early (before MCP policy + role selection)
    now = datetime.now(timezone.utc).isoformat()
    caller = getattr(__import__('tools.create_agent', fromlist=['_caller_id']), '_caller_id', 'unknown')
    item = {
        "agentId": agent_id,
        "agentName": agent_name,
        "name": agent_name,
        "display_name": agent_name,
        "description": description,
        "visibility": "private",
        "permissionTier": tier,
        "tool_names": tool_names_list,
        "mcp_targets": mcp_targets_list,
        "supports_images": supports_images,
        "template_id": template_id,
        "welcome_message": welcome_message or _default_welcome(agent_name, description),
        "suggestions": suggestion_list,
        "status": "active",
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
