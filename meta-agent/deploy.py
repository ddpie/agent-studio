"""Deployment utilities for packaging and deploying agents to AgentCore Runtime."""

import ast
import boto3
import io
import json
import time
import zipfile

from config import REGION, ACCOUNT_ID, S3_BUCKET, AGENT_ROLE_ARN, BASE_DEPLOYMENT_KEY

# Always inject the latest stream_utils.py into deployment packages
try:
    from templates.agent_template_v2 import STREAM_UTILS_CODE as _LATEST_STREAM_UTILS
except ImportError:
    _LATEST_STREAM_UTILS = None

try:
    from templates.agent_template_v2 import BUILTIN_TOOLS_CODE as _LATEST_BUILTIN_TOOLS
except ImportError:
    _LATEST_BUILTIN_TOOLS = None


def validate_agent_files(main_py: str, tools_py: str, prompt_txt: str, config_json: str) -> dict:
    """Validate agent files independently before deployment.

    Unlike the old validate_assembled_code which checked a single monolithic main.py,
    this validates each file separately — catching errors at the source.
    """
    errors = []
    warnings = []

    # 1. Validate tools.py syntax independently
    if tools_py.strip():
        try:
            ast.parse(tools_py)
        except SyntaxError as e:
            errors.append(f"tools.py SyntaxError: {e.msg} (line {e.lineno})")

    # 2. Validate main.py syntax (should always pass since it's a template)
    try:
        ast.parse(main_py)
    except SyntaxError as e:
        errors.append(f"main.py SyntaxError: {e.msg} (line {e.lineno})")

    # 3. Validate config.json
    try:
        config = json.loads(config_json)
        if not config.get("model_id"):
            errors.append("config.json missing model_id")
        if not isinstance(config.get("tool_names", []), list):
            errors.append("config.json tool_names must be a list")
    except json.JSONDecodeError as e:
        errors.append(f"config.json invalid JSON: {e}")

    # 4. Validate prompt.txt
    if not prompt_txt.strip():
        warnings.append("prompt.txt is empty")

    # 5. Check main.py has required structure
    if "@app.entrypoint" not in main_py:
        errors.append("main.py missing @app.entrypoint")
    if "BedrockAgentCoreApp()" not in main_py:
        errors.append("main.py missing BedrockAgentCoreApp()")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


# Keep old function for backward compatibility during transition
def validate_assembled_code(agent_code: str) -> dict:
    """Legacy: validate a single-file agent code."""
    errors = []
    try:
        ast.parse(agent_code)
    except SyntaxError as e:
        errors.append(f"Assembled code SyntaxError: {e.msg} (line {e.lineno})")
        return {"valid": False, "errors": errors, "warnings": []}
    if "@app.entrypoint" not in agent_code:
        errors.append("Missing @app.entrypoint")
    count = agent_code.count("@app.entrypoint")
    if count > 1:
        errors.append(f"Duplicate @app.entrypoint ({count} times)")
    return {"valid": len(errors) == 0, "errors": errors, "warnings": []}


def build_deployment_package_v2(
    main_py: str,
    tools_py: str,
    prompt_txt: str,
    config_json: str,
    skill_scripts: dict | None = None,
) -> bytes:
    """Build deployment zip with multi-file structure.

    Files written: main.py, tools.py, prompt.txt, config.json
    stream_utils.py is expected to be in the base zip already.
    """
    s3 = boto3.client("s3", region_name=REGION)
    base_resp = s3.get_object(Bucket=S3_BUCKET, Key=BASE_DEPLOYMENT_KEY)
    base_data = base_resp["Body"].read()

    agent_files = {"main.py", "tools.py", "prompt.txt", "config.json"}
    # Always overwrite stream_utils.py and builtin_tools.py with latest version
    if _LATEST_STREAM_UTILS:
        agent_files.add("stream_utils.py")
    if _LATEST_BUILTIN_TOOLS:
        agent_files.add("builtin_tools.py")

    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base_data), "r") as base_zip:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as new_zip:
            for item in base_zip.namelist():
                if item in agent_files:
                    continue  # Will be replaced below
                if item.startswith(("mcp_client/", "model/")):
                    continue  # Skip old template-specific modules
                new_zip.writestr(item, base_zip.read(item))

            # Write agent-specific files
            new_zip.writestr("main.py", main_py)
            new_zip.writestr("tools.py", tools_py)
            new_zip.writestr("prompt.txt", prompt_txt)
            new_zip.writestr("config.json", config_json)
            # Always inject latest stream_utils.py
            if _LATEST_STREAM_UTILS:
                new_zip.writestr("stream_utils.py", _LATEST_STREAM_UTILS)
            # Always inject latest builtin_tools.py
            if _LATEST_BUILTIN_TOOLS:
                new_zip.writestr("builtin_tools.py", _LATEST_BUILTIN_TOOLS)

            # Write skill scripts into subdirectories
            if skill_scripts:
                for skill_name, files in skill_scripts.items():
                    for filepath, content in files.items():
                        zip_path = f"skills/{skill_name}/scripts/{filepath}"
                        new_zip.writestr(zip_path, content)

    return buf.getvalue()


def build_skill_prompt_section(skills_data: list[dict]) -> str:
    """Build progressive disclosure prompt section from skill data."""
    if not skills_data:
        return ""

    lines = ["\n\n## Available Skills"]
    for s in skills_data:
        lines.append(f"- {s['name']}: {s['description']}")

    for s in skills_data:
        lines.append(f"\n## Skill: {s['name']}")
        lines.append(s.get("skill_md_content", ""))

    return "\n".join(lines)


# Keep old function for backward compatibility
def build_deployment_package(agent_code: str) -> bytes:
    """Legacy: build deployment with single main.py."""
    s3 = boto3.client("s3", region_name=REGION)
    base_resp = s3.get_object(Bucket=S3_BUCKET, Key=BASE_DEPLOYMENT_KEY)
    base_data = base_resp["Body"].read()

    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base_data), "r") as base_zip:
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as new_zip:
            for item in base_zip.namelist():
                if item == "main.py":
                    new_zip.writestr("main.py", agent_code)
                elif item.startswith(("mcp_client/", "model/")):
                    continue
                else:
                    new_zip.writestr(item, base_zip.read(item))

    return buf.getvalue()


def upload_deployment(agent_id_or_name: str, package: bytes) -> str:
    """Upload deployment package to S3. Returns S3 key.

    Note: For create_agent, agent_name is passed (agentId not yet known).
    For update_agent, agent_id is passed. Both are valid S3 path segments.
    """
    s3 = boto3.client("s3", region_name=REGION)
    s3_key = f"agents/{agent_id_or_name}/deployment.zip"
    s3.put_object(Bucket=S3_BUCKET, Key=s3_key, Body=package)
    return s3_key


def create_runtime(agent_name: str, description: str, s3_key: str, role_arn: str = AGENT_ROLE_ARN) -> dict:
    """Create an AgentCore Runtime. Returns {agent_id, agent_arn}."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    resp = control.create_agent_runtime(
        agentRuntimeName=agent_name,
        description=description,
        roleArn=role_arn,
        agentRuntimeArtifact={
            "codeConfiguration": {
                "code": {"s3": {"bucket": S3_BUCKET, "prefix": s3_key}},
                "runtime": "PYTHON_3_10",
                "entryPoint": ["main.py"],
            }
        },
        networkConfiguration={"networkMode": "PUBLIC"},
        protocolConfiguration={"serverProtocol": "HTTP"},
        filesystemConfigurations=[{
            "sessionStorage": {
                "mountPath": "/mnt/workspace"
            }
        }],
    )

    return {
        "agent_id": resp["agentRuntimeId"],
        "agent_arn": resp["agentRuntimeArn"],
    }


def wait_for_ready(agent_id: str, timeout: int = 300) -> str:
    """Wait for agent to become READY. Returns final status."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    start = time.time()

    while time.time() - start < timeout:
        resp = control.get_agent_runtime(agentRuntimeId=agent_id)
        status = resp["status"]
        if status == "READY":
            return status
        if status in ("FAILED", "DELETING"):
            return status
        time.sleep(10)

    return "TIMEOUT"


def invoke_runtime(agent_id: str, prompt: str) -> str:
    """Invoke a deployed agent. Returns the streamed text response."""
    client = boto3.client("bedrock-agentcore", region_name=REGION)

    resp = client.invoke_agent_runtime(
        agentRuntimeArn=f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/{agent_id}",
        qualifier="DEFAULT",
        payload=json.dumps({"prompt": prompt}).encode(),
    )

    parts = []
    for event in resp["response"]:
        if isinstance(event, bytes):
            text = event.decode("utf-8")
            for line in text.strip().split("\n"):
                if line.startswith("data: "):
                    content = line[6:].strip().strip('"')
                    parts.append(content)
        elif isinstance(event, dict):
            for v in event.values():
                if isinstance(v, bytes):
                    parts.append(v.decode("utf-8"))

    return "".join(parts)


def delete_runtime(agent_id: str):
    """Delete an agent runtime."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    control.delete_agent_runtime(agentRuntimeId=agent_id)

