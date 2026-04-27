"""upgrade_mcp — Pull latest registry image for an enabled MCP target."""
import json
import os
from datetime import datetime

import boto3
from strands import tool

from config import REGION, ACCOUNT_ID
from tools import _scope
from tools.enable_mcp import _load_registry_target


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
def upgrade_mcp(target: str) -> str:
    """Upgrade an enabled MCP target to the latest registry image version.

    Re-checks current-registry sensitivity (may have risen since enable) —
    admin required if high.

    Args:
        target: Registry target name.

    Returns:
        JSON: {"status": "UPDATING|error", "image_version": "...", ...}.
    """
    ws_id = _scope.current_workspace()
    caller = _scope.current_caller()
    if not ws_id:
        return json.dumps({"status": "error", "error": "no_workspace"})

    # Sensitivity gate against CURRENT registry (spec §6.5)
    reg_meta = _load_registry_target(target)
    if not reg_meta:
        return json.dumps({"status": "error", "error": "unknown_target"})
    sensitivity = reg_meta.get("sensitivity", "low")
    required_role = "admin" if sensitivity == "high" else "editor"
    err = _scope.require_role(required_role)
    if err:
        return json.dumps({"status": "error", **err})

    ws_meta = _get_ws_meta(ws_id)
    if not ws_meta:
        return json.dumps({"status": "error", "error": "workspace_not_found"})
    entry = (ws_meta.get("mcp_runtimes") or {}).get(target)
    if not entry:
        return json.dumps({"status": "error", "error": "not_enabled"})

    new_version = reg_meta.get("version") or "latest"
    image_uri = f"{ACCOUNT_ID}.dkr.ecr.{REGION}.amazonaws.com/mcp-{target}:{new_version}"

    try:
        _control().update_agent_runtime(
            agentRuntimeId=entry["runtime_id"],
            agentRuntimeArtifact={"containerConfiguration": {"containerUri": image_uri}},
        )
    except Exception as e:
        return json.dumps({"status": "error", "error": "update_failed",
                           "message": str(e)})

    now_iso = datetime.utcnow().isoformat() + "Z"
    try:
        _ws_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression=(
                "SET mcp_runtimes.#t.#s = :u, "
                "mcp_runtimes.#t.image_version = :v, "
                "mcp_runtimes.#t.image_uri = :uri, "
                "mcp_runtimes.#t.inflight_action = :u, "
                "mcp_runtimes.#t.inflight_actor = :actor, "
                "mcp_runtimes.#t.updated_at = :now"
            ),
            ExpressionAttributeNames={"#t": target, "#s": "status"},
            ExpressionAttributeValues={
                ":u": "UPDATING", ":v": new_version, ":uri": image_uri,
                ":actor": caller, ":now": now_iso,
            },
        )
    except Exception:
        pass

    return json.dumps({
        "status": "UPDATING", "image_version": new_version,
        "message": f"Upgrading '{target}' to {new_version}. Poll with get_mcp_status.",
    })
