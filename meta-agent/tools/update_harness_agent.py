"""update_harness_agent — Update prompt and/or model on an existing Harness Agent.

Only applies to agents with runtime_type == "harness". For zip agents, use
update_agent instead.
"""
import json
from datetime import datetime

import boto3
from strands import tool

from config import REGION, AGENTS_TABLE, S3_BUCKET
from tools._harness_mcp import resolve_mcp_targets_to_harness_tools


def _get_control_client():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def _get_agents_table():
    return boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)


def _read_staging(staging_key: str) -> dict:
    s3 = boto3.client("s3", region_name=REGION)
    obj = s3.get_object(Bucket=S3_BUCKET, Key=staging_key)
    return json.loads(obj["Body"].read())


@tool
def update_harness_agent(agent_id: str, staging_key: str) -> str:
    """Update prompt and/or model on an existing Harness Agent.

    Use for runtime_type=="harness" agents only. Recognized staging.json keys:
      - system_prompt, model_id (trigger control-plane update_harness)
      - display_name, description, welcome_message, suggestions, supports_images
        (DDB-only updates)

    Args:
        agent_id: harness agent id (same as DDB agentId).
        staging_key: S3 key to staging JSON file.

    Returns:
        JSON-encoded {"ok": true, "agentId": "..."} or {"error": "..."}.
    """
    try:
        table = _get_agents_table()
        existing = table.get_item(Key={"agentId": agent_id}).get("Item")
        if not existing:
            return json.dumps({"error": f"agent not found: {agent_id}"})
        if existing.get("runtime_type") != "harness":
            return json.dumps({
                "error": f"agent runtime_type is not 'harness': {existing.get('runtime_type')}"
            })

        staged = _read_staging(staging_key)

        # Control-plane update — only if prompt, model, or mcp_targets changed.
        cp_kwargs = {"harnessId": agent_id}
        if staged.get("model_id"):
            cp_kwargs["model"] = {"bedrockModelConfig": {"modelId": staged["model_id"]}}
        if staged.get("system_prompt"):
            cp_kwargs["systemPrompt"] = [{"text": staged["system_prompt"]}]

        # mcp_targets: absent means "don't touch"; [] means "remove all".
        # Distinguish via the key's presence in the staging dict.
        mcp_targets_list = None
        if "mcp_targets" in staged:
            raw = staged.get("mcp_targets", [])
            if isinstance(raw, list):
                mcp_targets_list = [t for t in raw if isinstance(t, str) and t.strip()]
            else:
                mcp_targets_list = [t.strip() for t in str(raw).split(",") if t.strip()]
            try:
                cp_kwargs["tools"] = resolve_mcp_targets_to_harness_tools(mcp_targets_list)
            except ValueError as ve:
                return json.dumps({"error": str(ve)})

        if "model" in cp_kwargs or "systemPrompt" in cp_kwargs or "tools" in cp_kwargs:
            _get_control_client().update_harness(**cp_kwargs)

        # DDB update — always write updated_at + any provided metadata fields.
        now = datetime.utcnow().isoformat() + "Z"
        set_parts = ["updated_at = :updated_at"]
        values = {":updated_at": now}
        for k in ("display_name", "description", "welcome_message",
                  "suggestions", "supports_images", "model_id", "system_prompt"):
            if k in staged:
                ph = f":{k}"
                values[ph] = staged[k]
                set_parts.append(f"{k} = {ph}")
        if mcp_targets_list is not None:
            values[":mcp_targets"] = mcp_targets_list
            set_parts.append("mcp_targets = :mcp_targets")
        table.update_item(
            Key={"agentId": agent_id},
            UpdateExpression="SET " + ", ".join(set_parts),
            ExpressionAttributeValues=values,
        )
        return json.dumps({"ok": True, "agentId": agent_id}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})
