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
from tools._harness_mcp import resolve_mcp_targets_to_harness_tools

# Workspaces table isn't in meta-agent/config.py (Meta-Agent rarely needs it);
# hardcode the default here + env override for parity with Lambda naming.
import os as _os
_WORKSPACES_TABLE = _os.getenv("AGENT_STUDIO_WORKSPACES_TABLE", "agent-studio-workspaces")


def _get_workspace_memory_id(workspace_id: str) -> str | None:
    ddb = boto3.resource("dynamodb", region_name=REGION).Table(_WORKSPACES_TABLE)
    resp = ddb.get_item(Key={"workspaceId": workspace_id, "sk": "META"})
    item = resp.get("Item") or {}
    mem = item.get("memory_id")
    return mem if mem else None


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
    mcp_targets: str = "",
    staging_key: str = "",
) -> str:
    """Create an AgentCore Harness-based Agent.

    Supported: system prompt, model, and MCP tools (via workspace MCP
    gateway). NOT YET supported: custom Python tools, skills, memory.
    If the user wants any of the unsupported features, use create_agent
    (zip) instead.

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
        mcp_targets: comma-separated MCP target names (e.g. "cloudwatch,iam").
                     Each target is resolved to its gateway; harness gets
                     one agentcore_gateway tool entry per gateway.
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
            staged_targets = staged.get("mcp_targets", [])
            if isinstance(staged_targets, list):
                mcp_targets_list = [t for t in staged_targets if isinstance(t, str) and t.strip()]
            else:
                mcp_targets_list = [t.strip() for t in str(staged_targets).split(",") if t.strip()]
            memory_cfg = staged.get("memory") or {}
        else:
            name = (name or "").strip()
            system_prompt = (system_prompt or "").strip()
            model_id = (model_id or "").strip()
            suggestions = []
            default_model_id = ""
            workspace_id = current_workspace()
            mcp_targets_list = [t.strip() for t in (mcp_targets or "").split(",") if t.strip()]
            memory_cfg = {}

        if not name:
            return json.dumps({"error": "name is required"})
        if not system_prompt:
            return json.dumps({"error": "system_prompt is required"})
        if not model_id:
            return json.dumps({"error": "model_id is required"})
        if not workspace_id:
            return json.dumps({"error": "workspace_id is required (no active workspace scope)"})

        role_arn = _get_agent_role_arn(workspace_id)

        # Resolve MCP targets → one agentcore_gateway tool entry per gateway.
        # Raises if any target isn't reachable on a READY gateway.
        harness_tools = []
        if mcp_targets_list:
            try:
                harness_tools = resolve_mcp_targets_to_harness_tools(mcp_targets_list)
            except ValueError as ve:
                return json.dumps({"error": str(ve)})

        # Memory: only pass to harness if the staging cfg opted in AND the
        # workspace has a memory resource provisioned. Silent no-op otherwise
        # — the frontend already surfaces "memory unavailable" when the WS
        # memory id is missing.
        harness_memory = None
        if isinstance(memory_cfg, dict) and memory_cfg.get("enabled"):
            ws_memory_id = _get_workspace_memory_id(workspace_id)
            if ws_memory_id:
                sts = boto3.client("sts", region_name=REGION)
                account_id = sts.get_caller_identity()["Account"]
                mem_arn = (
                    f"arn:aws:bedrock-agentcore:{REGION}:{account_id}:memory/{ws_memory_id}"
                )
                harness_memory = {
                    "agentCoreMemoryConfiguration": {"arn": mem_arn},
                }

        cp = _get_control_client()
        create_kwargs = dict(
            harnessName=name,
            executionRoleArn=role_arn,
            model={"bedrockModelConfig": {"modelId": model_id}},
            systemPrompt=[{"text": system_prompt}],
        )
        if harness_tools:
            create_kwargs["tools"] = harness_tools
        if harness_memory:
            create_kwargs["memory"] = harness_memory
        resp = cp.create_harness(**create_kwargs)

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
            "mcp_targets": mcp_targets_list,
            "memory": {
                "enabled": bool(harness_memory),
                "strategies": (memory_cfg.get("strategies", []) if isinstance(memory_cfg, dict) else []),
            },
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
