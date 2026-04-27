"""create_harness_agent — Create an AgentCore Harness-based Agent (MVP: text-only).

Companion to create_agent (zip runtime). Distinguishing feature: no code
generation, no deployment.zip — harness is a declarative AWS-managed agent.

Selection rule for the Meta-Agent:
- Only call this when the staging.json's runtime_type == "harness".
- Do NOT call this for zip agents — use create_agent instead.
- MVP scope: prompt + model only. tools/skills/MCP are not supported here.
"""
import json
from datetime import datetime

import boto3
from strands import tool

from config import REGION, AGENTS_TABLE, S3_BUCKET
from tools._scope import current_caller, current_workspace
from tools._workspace import _get_agent_role_arn


def _get_control_client():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def _get_agents_table():
    return boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)


def _read_staging(staging_key: str) -> dict:
    s3 = boto3.client("s3", region_name=REGION)
    obj = s3.get_object(Bucket=S3_BUCKET, Key=staging_key)
    return json.loads(obj["Body"].read())


@tool
def create_harness_agent(staging_key: str) -> str:
    """Create an AgentCore Harness-based Agent.

    MVP scope: text-only conversation. Does NOT support tool / skill / MCP /
    memory. If the user wants any of those features, use create_agent (zip
    runtime) instead.

    staging.json (referenced by staging_key in S3) MUST contain:
      - name: alphanumeric, max 36 chars (enforced by AgentCore)
      - system_prompt: non-empty
      - model_id: non-empty Bedrock model id
      - runtime_type: must equal "harness"
      - workspace_id: workspace this agent belongs to
    Optional:
      - display_name, description, welcome_message, suggestions, supports_images

    Args:
        staging_key: S3 key to the staging JSON file.

    Returns:
        JSON-encoded: {"ok": true, "agentId": "...", "harnessArn": "..."} on success,
        {"error": "..."} on any failure.
    """
    try:
        staged = _read_staging(staging_key)

        if staged.get("runtime_type") != "harness":
            return json.dumps({"error": "staging.json runtime_type must be 'harness'"})

        name = (staged.get("name") or "").strip()
        if not name:
            return json.dumps({"error": "name is required"})

        system_prompt = (staged.get("system_prompt") or "").strip()
        if not system_prompt:
            return json.dumps({"error": "system_prompt is required"})

        model_id = (staged.get("model_id") or "").strip()
        if not model_id:
            return json.dumps({"error": "model_id is required"})

        workspace_id = (staged.get("workspace_id") or "").strip() or current_workspace()
        if not workspace_id:
            return json.dumps({"error": "workspace_id is required"})

        role_arn = _get_agent_role_arn(workspace_id)

        cp = _get_control_client()
        resp = cp.create_harness(
            harnessName=name,
            executionRoleArn=role_arn,
            model={"bedrockModelConfig": {"modelId": model_id}},
            systemPrompt=[{"text": system_prompt}],
        )

        # Response shape (from Phase 0 spike): resp["harness"]["arn"] and ["harnessId"]
        harness = resp.get("harness", resp)  # tolerate both shapes
        harness_arn = harness["arn"] if "arn" in harness else harness.get("harnessArn", "")
        harness_id = harness.get("harnessId") or harness_arn.rsplit("/", 1)[-1]
        if not harness_arn or not harness_id:
            return json.dumps({"error": f"unexpected create_harness response: {resp}"})

        now = datetime.utcnow().isoformat() + "Z"
        item = {
            "agentId": harness_id,
            "workspace_id": workspace_id,
            "name": name,
            "display_name": staged.get("display_name", ""),
            "description": staged.get("description", ""),
            "model_id": model_id,
            "default_model_id": staged.get("default_model_id", ""),
            "supports_images": bool(staged.get("supports_images", False)),
            "welcome_message": staged.get("welcome_message", ""),
            "suggestions": staged.get("suggestions", []),
            "system_prompt": system_prompt,
            "tool_names": [],
            "skill_ids": [],
            "skills": [],
            "mcp_targets": [],
            "runtime_type": "harness",
            "harness_arn": harness_arn,
            "status": "active",
            "visibility": "private",
            "created_by": current_caller() or "unknown",
            "created_at": now,
            "updated_at": now,
        }
        _get_agents_table().put_item(Item=item)

        return json.dumps({
            "ok": True,
            "agentId": harness_id,
            "harnessArn": harness_arn,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})
