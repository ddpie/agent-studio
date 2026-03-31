"""create_agent — Generate code, package, and deploy a new Agent to AgentCore Runtime."""

import json
from datetime import datetime, timezone

import boto3
from strands import tool

from config import MODEL_ID, REGION, S3_BUCKET, AGENTS_TABLE, PERMISSION_TIER_ROLES, DEFAULT_PERMISSION_TIER
from deploy import build_deployment_package, upload_deployment, create_runtime, wait_for_ready
from templates.agent_template import AGENT_CODE_TEMPLATE, AGENT_CODE_WITH_MCP_TEMPLATE
from templates.prompt_templates import get_template_prompt, get_template_names, BASE_GUIDELINES


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
    description: str,
    system_prompt: str,
    tool_definitions: str,
    tool_names: str,
    welcome_message: str = "",
    suggestions: str = "",
    template_id: str = "",
    gateway_url: str = "",
    supports_images: bool = False,
    permission_tier: str = "",
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
        template_id: Optional prompt template to use as base (general/expert/customer_service/data_analyst/creative_writer).
        gateway_url: Optional AgentCore Gateway MCP URL.
        supports_images: Whether this agent can process image inputs.
        permission_tier: IAM permission level: basic (model only), readonly (AWS read), data-access (AWS read+write). Default: readonly.

    Returns:
        JSON with agent_id, agent_arn, status.
    """
    # Apply template if specified
    if template_id:
        base_prompt = get_template_prompt(template_id)
        final_prompt = base_prompt + "\n\n## Specific Instructions\n" + system_prompt
    else:
        final_prompt = system_prompt + "\n" + BASE_GUIDELINES

    # Use repr() to safely escape the system_prompt
    safe_prompt = repr(final_prompt)

    # Generate agent code
    if gateway_url:
        agent_code = AGENT_CODE_WITH_MCP_TEMPLATE.format(
            model_id=MODEL_ID,
            system_prompt_repr=safe_prompt,
            tool_definitions=tool_definitions,
            tool_names=tool_names,
            gateway_url=gateway_url,
        )
    else:
        agent_code = AGENT_CODE_TEMPLATE.format(
            model_id=MODEL_ID,
            system_prompt_repr=safe_prompt,
            tool_definitions=tool_definitions,
            tool_names=tool_names,
        )

    # Build, upload, deploy
    # Note: upload uses agent_name because agentId isn't known yet
    tier = permission_tier or DEFAULT_PERMISSION_TIER
    role_arn = PERMISSION_TIER_ROLES.get(tier, PERMISSION_TIER_ROLES[DEFAULT_PERMISSION_TIER])
    package = build_deployment_package(agent_code)
    s3_key = upload_deployment(agent_name, package)
    result = create_runtime(agent_name, description, s3_key, role_arn)

    agent_id = result["agent_id"]

    # Save metadata.json using agentId as path
    suggestion_list = [s.strip() for s in suggestions.split("|") if s.strip()] if suggestions else []
    metadata = {
        "agent_id": agent_id,
        "name": agent_name,
        "display_name": agent_name,
        "description": description,
        "model_id": MODEL_ID,
        "system_prompt": final_prompt,
        "welcome_message": welcome_message or f"I'm {agent_name}. {description}",
        "suggestions": suggestion_list,
        "template_id": template_id,
        "tools": [t.strip() for t in tool_names.split(",")],
        "supports_images": supports_images,
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
    owner = getattr(__import__('tools.create_agent', fromlist=['_caller_id']), '_caller_id', 'unknown')
    table.put_item(Item={
        "agentId": agent_id,
        "agentName": agent_name,
        "displayName": agent_name,
        "description": description,
        "owner": owner,
        "visibility": "private",
        "permissionTier": tier,
    })

    # Wait for ready
    status = wait_for_ready(result["agent_id"])
    result["status"] = status
    result["agent_name"] = agent_name

    return json.dumps(result, indent=2)
