"""preview_code — Preview the agent files before deployment."""

import json

from config import MODEL_ID, REGION, S3_BUCKET
from deploy import validate_agent_files
from strands import tool

from templates.agent_template_v2 import MAIN_PY_MCP_TEMPLATE, MAIN_PY_TEMPLATE, TOOLS_PY_HEADER
from templates.prompt_templates import get_base_guidelines


@tool
def preview_assembled_code(
    staging_key: str = "",
    system_prompt: str = "",
    tool_definitions: str = "",
    tool_names: str = "",
    template_id: str = "",
    gateway_url: str = "",
) -> str:
    """Preview the agent files that would be deployed.

    Returns all files (main.py, tools.py, prompt.txt, config.json) and validation result.
    Writes files to S3 for frontend to fetch.

    Args:
        staging_key: S3 key to staging config.
        system_prompt: The system prompt text.
        tool_definitions: Python @tool function code.
        tool_names: Comma-separated tool names.
        template_id: Deprecated; ignored. Prompt templates have been retired.
        gateway_url: Optional MCP Gateway URL.

    Returns:
        JSON with preview_key, validation result, and file sizes.
    """
    if staging_key:
        try:
            import boto3
            s3 = boto3.client("s3", region_name=REGION)
            obj = s3.get_object(Bucket=S3_BUCKET, Key=staging_key)
            staged = json.loads(obj["Body"].read().decode("utf-8"))
            system_prompt = staged.get("system_prompt", system_prompt) or system_prompt
            tool_definitions = staged.get("tool_definitions", tool_definitions) or tool_definitions
            tool_names = staged.get("tool_names", tool_names) or tool_names
            template_id = staged.get("template_id", template_id) or template_id
            gateway_url = staged.get("gateway_url", gateway_url) or gateway_url
        except Exception as e:
            return json.dumps({"error": f"Failed to read staging config: {e}"})

    # Compose final prompt. template_id is accepted for back-compat but
    # ignored — see create_agent.py for the rationale. The agent
    # always gets the user's raw system_prompt + BASE_GUIDELINES in the
    # creator's language.
    from tools._scope import current_creator_language
    base = system_prompt or "You are a helpful assistant."
    guidelines = get_base_guidelines(current_creator_language())
    final_prompt = base if guidelines in base else base + "\n" + guidelines

    # Build files
    tool_names_list = [t.strip() for t in tool_names.split(",") if t.strip()] if tool_names else []
    main_py = MAIN_PY_MCP_TEMPLATE if gateway_url else MAIN_PY_TEMPLATE
    tools_py = TOOLS_PY_HEADER + (tool_definitions or "")
    prompt_txt = final_prompt
    config_data = {"model_id": MODEL_ID, "tool_names": tool_names_list}
    if gateway_url:
        config_data["gateway_url"] = gateway_url
    config_json = json.dumps(config_data, indent=2, ensure_ascii=False)

    # Validate
    validation = validate_agent_files(main_py, tools_py, prompt_txt, config_json)

    # Write all files to S3 for frontend preview
    import time

    import boto3
    s3 = boto3.client("s3", region_name=REGION)
    base_key = staging_key.split("/")[-1].replace(".json", "") if staging_key else f"preview-{int(time.time() * 1000)}"
    preview_prefix = f"agents/_preview/{base_key}"

    combined = f"# === main.py ===\n{main_py}\n\n# === tools.py ===\n{tools_py}\n\n# === prompt.txt ===\n# {prompt_txt[:200]}...\n\n# === config.json ===\n# {config_json}"
    s3.put_object(Bucket=S3_BUCKET, Key=f"{preview_prefix}-main.py", Body=combined.encode("utf-8"), ContentType="text/x-python")

    return json.dumps({
        "preview_key": f"{preview_prefix}-main.py",
        "files": {
            "main.py": len(main_py),
            "tools.py": len(tools_py),
            "prompt.txt": len(prompt_txt),
            "config.json": len(config_json),
        },
        "valid": validation["valid"],
        "errors": validation["errors"],
        "warnings": validation["warnings"],
    })
