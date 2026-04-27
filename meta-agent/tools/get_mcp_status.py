"""get_mcp_status — One-shot status check for an enabled MCP target.

Heals DDB by calling control-plane GetAgentRuntime when status is non-terminal.
"""
import json
import os
from datetime import datetime

import boto3
from strands import tool

from config import REGION
from tools import _scope


_WORKSPACES_TABLE = os.getenv("WORKSPACES_TABLE", "agent-studio-workspaces")
_NON_TERMINAL = {"CREATING", "UPDATING", "DELETING"}


def _control():
    return boto3.client("bedrock-agentcore-control", region_name=REGION)


def _ws_table():
    return boto3.resource("dynamodb", region_name=REGION).Table(_WORKSPACES_TABLE)


def _get_ws_meta(ws_id: str) -> dict | None:
    return _ws_table().get_item(
        Key={"workspaceId": ws_id, "sk": "META"},
        ConsistentRead=True,
    ).get("Item")


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


@tool
def get_mcp_status(target: str) -> str:
    """Get status of an MCP target. Heals DDB if control-plane state advanced.

    Args:
        target: Registry target name.

    Returns:
        JSON: {"status": "CREATING|READY|FAILED|DELETED|UNKNOWN|error",
               "runtime_name": ..., "image_version": ..., "last_error": ...}.
    """
    err = _scope.require_role("viewer")
    if err:
        return json.dumps({"status": "error", **err})

    ws_id = _scope.current_workspace()
    ws_meta = _get_ws_meta(ws_id)
    if not ws_meta:
        return json.dumps({"status": "error", "error": "workspace_not_found"})

    entry = (ws_meta.get("mcp_runtimes") or {}).get(target)
    if not entry:
        return json.dumps({"status": "error", "error": "not_enabled"})

    current_status = entry.get("status")
    if current_status in _NON_TERMINAL:
        # Heal on read
        runtime_id = entry.get("runtime_id")
        if runtime_id:
            try:
                info = _control().get_agent_runtime(agentRuntimeId=runtime_id)
                live_status = info.get("status", "")
                if live_status in ("READY", "ACTIVE"):
                    _ws_table().update_item(
                        Key={"workspaceId": ws_id, "sk": "META"},
                        UpdateExpression=(
                            "SET mcp_runtimes.#t.#s = :r, "
                            "mcp_runtimes.#t.inflight_action = :null, "
                            "mcp_runtimes.#t.inflight_actor = :null, "
                            "mcp_runtimes.#t.updated_at = :now"
                        ),
                        ExpressionAttributeNames={"#t": target, "#s": "status"},
                        ExpressionAttributeValues={
                            ":r": "READY", ":null": None, ":now": _now_iso(),
                        },
                    )
                    entry["status"] = "READY"
                    entry["inflight_action"] = None
                elif live_status in ("FAILED", "CREATE_FAILED"):
                    reason = info.get("failureReason") or info.get("statusReason") or "unknown"
                    _ws_table().update_item(
                        Key={"workspaceId": ws_id, "sk": "META"},
                        UpdateExpression=(
                            "SET mcp_runtimes.#t.#s = :f, "
                            "mcp_runtimes.#t.last_error = :e, "
                            "mcp_runtimes.#t.inflight_action = :null, "
                            "mcp_runtimes.#t.inflight_actor = :null, "
                            "mcp_runtimes.#t.updated_at = :now"
                        ),
                        ExpressionAttributeNames={"#t": target, "#s": "status"},
                        ExpressionAttributeValues={
                            ":f": "FAILED", ":e": reason, ":null": None, ":now": _now_iso(),
                        },
                    )
                    entry["status"] = "FAILED"
                    entry["last_error"] = reason
            except Exception:
                pass

    return json.dumps({
        "target": target,
        "status": entry.get("status", "UNKNOWN"),
        "runtime_name": entry.get("runtime_name"),
        "runtime_arn": entry.get("runtime_arn"),
        "image_version": entry.get("image_version"),
        "last_error": entry.get("last_error"),
        "updated_at": entry.get("updated_at"),
    }, default=str)
