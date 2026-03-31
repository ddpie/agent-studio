"""update_agent — Update an existing agent's code and configuration in-place."""

import json
from datetime import datetime, timezone

import boto3
from strands import tool

from config import MODEL_ID, REGION, S3_BUCKET, AGENT_ROLE_ARN, AGENTS_TABLE
from deploy import build_deployment_package, upload_deployment, wait_for_ready
from templates.agent_template import AGENT_CODE_TEMPLATE, AGENT_CODE_WITH_MCP_TEMPLATE
from templates.prompt_templates import get_template_prompt, BASE_GUIDELINES

# Fields that require AgentCore redeploy when changed
_REDEPLOY_FIELDS = {"system_prompt", "tool_definitions", "tool_names", "template_id", "gateway_url"}


@tool
def update_agent(
    agent_id: str,
    agent_name: str,
    description: str = "",
    display_name: str = "",
    system_prompt: str = "",
    tool_definitions: str = "",
    tool_names: str = "",
    welcome_message: str = "",
    suggestions: str = "",
    template_id: str = "",
    gateway_url: str = "",
    supports_images: bool = False,
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
        gateway_url: Optional MCP Gateway URL.
        supports_images: Whether this agent can process image inputs.

    Returns:
        JSON with update status.
    """
    s3 = boto3.client("s3", region_name=REGION)

    # Ownership check
    caller = getattr(__import__('tools.update_agent', fromlist=['_caller_id']), '_caller_id', 'unknown')
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)
    record = table.get_item(Key={"agentId": agent_id}).get("Item")
    if record and record.get("owner") != caller:
        return json.dumps({"error": f"Permission denied: agent owned by {record['owner']}, you are {caller}"})

    # Read existing metadata
    existing_metadata = {}
    try:
        obj = s3.get_object(Bucket=S3_BUCKET, Key=f"agents/{agent_id}/metadata.json")
        existing_metadata = json.loads(obj["Body"].read().decode("utf-8"))
    except Exception:
        pass

    # Merge: use new values if provided, else keep existing
    final_prompt = system_prompt or existing_metadata.get("system_prompt", "You are a helpful assistant.")
    final_tools_def = tool_definitions or ""
    final_tools_names = tool_names or ""
    final_desc = description or existing_metadata.get("description", "")
    final_display = display_name or existing_metadata.get("display_name", agent_name)
    final_welcome = welcome_message or existing_metadata.get("welcome_message", "")
    final_suggestions = suggestions or "|".join(existing_metadata.get("suggestions", []))
    final_template = template_id or existing_metadata.get("template_id", "")

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
    if gateway_url:
        needs_redeploy = True

    status = "metadata_only"

    if needs_redeploy:
        # Apply template if specified
        if final_template:
            base_prompt = get_template_prompt(final_template)
            final_prompt = base_prompt + "\n\n## Specific Instructions\n" + final_prompt
        elif BASE_GUIDELINES not in final_prompt:
            final_prompt = final_prompt + "\n" + BASE_GUIDELINES

        safe_prompt = repr(final_prompt)

        # Generate code
        if gateway_url:
            agent_code = AGENT_CODE_WITH_MCP_TEMPLATE.format(
                model_id=MODEL_ID,
                system_prompt_repr=safe_prompt,
                tool_definitions=final_tools_def,
                tool_names=final_tools_names,
                gateway_url=gateway_url,
            )
        else:
            agent_code = AGENT_CODE_TEMPLATE.format(
                model_id=MODEL_ID,
                system_prompt_repr=safe_prompt,
                tool_definitions=final_tools_def,
                tool_names=final_tools_names,
            )

        # Build and upload
        package = build_deployment_package(agent_code)
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
        )

        status = wait_for_ready(agent_id)

    # Always update metadata
    suggestion_list = [s.strip() for s in final_suggestions.split("|") if s.strip()]
    metadata = {
        "agent_id": agent_id,
        "name": agent_name,
        "display_name": final_display,
        "description": final_desc,
        "model_id": MODEL_ID,
        "system_prompt": final_prompt,
        "welcome_message": final_welcome or f"I'm {agent_name}. {final_desc}",
        "suggestions": suggestion_list,
        "template_id": final_template,
        "tools": [t.strip() for t in final_tools_names.split(",") if t.strip()],
        "supports_images": supports_images or existing_metadata.get("supports_images", False),
        "created_at": existing_metadata.get("created_at", datetime.now(timezone.utc).isoformat()),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    s3.put_object(
        Bucket=S3_BUCKET,
        Key=f"agents/{agent_id}/metadata.json",
        Body=json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8"),
        ContentType="application/json",
    )

    # Update DynamoDB display_name
    table.update_item(
        Key={"agentId": agent_id},
        UpdateExpression="SET displayName = :dn",
        ExpressionAttributeValues={":dn": final_display},
    )

    return json.dumps({
        "agent_id": agent_id,
        "agent_name": agent_name,
        "status": status,
        "action": "redeployed" if needs_redeploy else "metadata_updated",
        "needs_redeploy": needs_redeploy,
    }, indent=2)
