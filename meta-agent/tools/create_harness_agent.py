"""create_harness_agent — Create an AgentCore Harness-based Agent (MVP: text-only).

Companion to create_agent (zip runtime). Distinguishing feature: no code
generation, no deployment.zip — harness is a declarative AWS-managed agent.

Selection rule for the Meta-Agent:
- Call this when the user wants a harness-runtime agent (conversational
  creation: pass name/system_prompt/model_id directly; form-driven:
  pass staging_key pointing to the uploaded staging.json).
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
def create_harness_agent(
    name: str = "",
    system_prompt: str = "",
    model_id: str = "",
    display_name: str = "",
    description: str = "",
    welcome_message: str = "",
    supports_images: bool = False,
    staging_key: str = "",
) -> str:
    """Create an AgentCore Harness-based Agent (text-only, no tools/skills/MCP).

    MVP scope: Harness agents only support a system prompt + model. If the
    user wants tools, skills, MCP, or memory, use create_agent (zip)
    instead.

    Two calling modes:
      1. Conversational (from chat): pass name + system_prompt + model_id
         directly. Leave staging_key empty. Use this when the user tells
         the Meta-Agent what they want in chat and expects immediate
         creation.
      2. Form-driven (from Agent edit page): the frontend uploads
         staging.json to S3 and passes staging_key; direct params are
         ignored in that mode. staging.json MUST include runtime_type:
         "harness" plus name/system_prompt/model_id/workspace_id.

    Args:
        name: agent name. Alphanumeric only, max 36 chars (AgentCore rule).
        system_prompt: the agent's system prompt. Required.
        model_id: Bedrock model id (e.g. "us.anthropic.claude-haiku-4-5-20251001-v1:0").
        display_name: UI display name (optional; defaults to name).
        description: one-liner (optional).
        welcome_message: greeting shown in chat (optional).
        supports_images: multimodal flag (optional, default False).
        staging_key: S3 key to staging.json if using form-driven mode
                     (mutually exclusive with direct params).

    Returns:
        JSON: {"ok": true, "agentId": "...", "harnessArn": "..."} on success,
              {"error": "..."} on failure.
    """
    try:
        if staging_key:
            staged = _read_staging(staging_key)
            if staged.get("runtime_type") != "harness":
                return json.dumps({"error": "staging.json runtime_type must be 'harness'"})
            name = (staged.get("name") or "").strip()
            system_prompt = (staged.get("system_prompt") or "").strip()
            model_id = (staged.get("model_id") or "").strip()
            display_name = staged.get("display_name", "")
            description = staged.get("description", "")
            welcome_message = staged.get("welcome_message", "")
            supports_images = bool(staged.get("supports_images", False))
            suggestions = staged.get("suggestions", [])
            default_model_id = staged.get("default_model_id", "")
            workspace_id = (staged.get("workspace_id") or "").strip() or current_workspace()
        else:
            name = (name or "").strip()
            system_prompt = (system_prompt or "").strip()
            model_id = (model_id or "").strip()
            suggestions = []
            default_model_id = ""
            workspace_id = current_workspace()

        if not name:
            return json.dumps({"error": "name is required"})
        if not system_prompt:
            return json.dumps({"error": "system_prompt is required"})
        if not model_id:
            return json.dumps({"error": "model_id is required"})
        if not workspace_id:
            return json.dumps({"error": "workspace_id is required (no active workspace scope)"})

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
            "display_name": display_name or name,
            "description": description,
            "model_id": model_id,
            "default_model_id": default_model_id,
            "supports_images": supports_images,
            "welcome_message": welcome_message,
            "suggestions": suggestions,
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
