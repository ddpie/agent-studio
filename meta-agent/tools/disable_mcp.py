"""disable_mcp — Disable an MCP target for the current workspace.

Deletes the per-workspace runtime and removes the target's grants from the
workspace role. Safe to re-run (idempotent).
"""
import json
import os
from datetime import datetime

import boto3
from strands import tool

from config import REGION
from tools import _scope
from tools.enable_mcp import _merge_workspace_grants


_WORKSPACES_TABLE = os.getenv("WORKSPACES_TABLE", "agent-studio-workspaces")


def _control():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def _ws_table():
    return boto3.resource("dynamodb", region_name=REGION).Table(_WORKSPACES_TABLE)


def _get_ws_meta(ws_id: str) -> dict | None:
    return _ws_table().get_item(
        Key={"workspaceId": ws_id, "sk": "META"},
        ConsistentRead=True,
    ).get("Item")


@tool
def disable_mcp(target: str) -> str:
    """Disable an MCP target. Deletes the per-workspace runtime.

    Agents in this workspace that reference the target will fail at invocation
    time until re-enabled. Requires editor role.

    Args:
        target: Registry target name.

    Returns:
        JSON: {"status": "DELETED|error", "message": "..."}.
    """
    err = _scope.require_role("editor")
    if err:
        return json.dumps({"status": "error", **err})

    ws_id = _scope.current_workspace()
    caller = _scope.current_caller()
    ws_meta = _get_ws_meta(ws_id)
    if not ws_meta:
        return json.dumps({"status": "error", "error": "workspace_not_found"})

    entry = (ws_meta.get("mcp_runtimes") or {}).get(target)
    if not entry:
        return json.dumps({"status": "error", "error": "not_enabled"})

    role_name = ws_meta.get("roleName")
    runtime_id = entry.get("runtime_id")

    # Delete runtime (best-effort)
    if runtime_id:
        try:
            _control().delete_agent_runtime(agentRuntimeId=runtime_id)
        except Exception:
            pass

    # Shrink grants
    current_grants = list(ws_meta.get("mcpGrants", []) or [])
    new_grants = sorted(set(current_grants) - {target})
    if role_name:
        try:
            _merge_workspace_grants(role_name, new_grants)
        except Exception:
            pass

    # Pop DDB entry
    now_iso = datetime.utcnow().isoformat() + "Z"
    try:
        _ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="REMOVE mcp_runtimes.#t SET mcpGrants = :g, updated_at = :now",
            ExpressionAttributeNames={"#t": target},
            ExpressionAttributeValues={":g": new_grants, ":now": now_iso},
        )
    except Exception:
        pass

    return json.dumps({"status": "DELETED", "message": f"Disabled '{target}' in this workspace."})
