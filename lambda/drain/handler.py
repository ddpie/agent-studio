"""MCP drain worker — async workspace-delete (spec D14).

SQS messages:
  {"action": "delete_runtime", "ws_id": "...", "target": "...",
   "runtime_id": "...", "role_name": "...", "grants_after": [...]}
  {"action": "cleanup_ws", "ws_id": "...", "role_name": "..."}

delete_runtime:
  1. DeleteAgentRuntime → poll until DELETED (max 4 min due to visibility 6 min).
  2. If final target, shrink WorkspaceGrants; else leave as-is.
  3. Remove DDB mcp_runtimes[target] entry.

cleanup_ws:
  Runs after all delete_runtime messages for a workspace have completed.
  If mcp_runtimes is empty: delete WorkspaceGrants policy + delete role +
  delete workspace META. Otherwise re-queue with delay.
"""
import os
import json
import time
import logging

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ["AGENT_STUDIO_REGION"]
WORKSPACES_TABLE = os.environ["WORKSPACES_TABLE"]

_control = None
_iam = None
_ddb = None


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


def _get_iam():
    global _iam
    if _iam is None:
        _iam = boto3.client("iam", region_name=REGION)
    return _iam


def _get_table():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _ddb


def _wait_runtime_deleted(runtime_id: str, timeout_sec: int = 240) -> bool:
    """Poll get_agent_runtime until not found or status=DELETED. Returns True if gone."""
    control = _get_control()
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        try:
            info = control.get_agent_runtime(agentRuntimeId=runtime_id)
            status = info.get("status", "")
            if status in ("DELETED", "NOT_FOUND"):
                return True
        except Exception:
            # ResourceNotFoundException → runtime is gone
            return True
        time.sleep(10)
    return False


def _handle_delete_runtime(msg: dict) -> None:
    ws_id = msg["ws_id"]
    target = msg["target"]
    runtime_id = msg.get("runtime_id")

    if runtime_id:
        try:
            _get_control().delete_agent_runtime(agentRuntimeId=runtime_id)
        except Exception as e:
            logger.info("delete_agent_runtime failed (maybe already gone): %s", e)

        ok = _wait_runtime_deleted(runtime_id)
        if not ok:
            # Return exception → SQS retry → DLQ after 3 tries.
            raise RuntimeError(f"runtime {runtime_id} still not DELETED after 4 min")

    # Remove DDB entry
    try:
        _get_table().update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="REMOVE mcp_runtimes.#t",
            ExpressionAttributeNames={"#t": target},
        )
    except Exception as e:
        logger.warning("DDB remove failed ws=%s target=%s: %s", ws_id, target, e)


def _handle_cleanup_ws(msg: dict) -> None:
    """Delete workspace role + META if no mcp_runtimes left."""
    ws_id = msg["ws_id"]
    role_name = msg.get("role_name")

    table = _get_table()
    resp = table.get_item(Key={"workspaceId": ws_id, "sk": "META"}, ConsistentRead=True)
    item = resp.get("Item")
    if not item:
        return
    remaining = item.get("mcp_runtimes") or {}
    if remaining:
        # Not ready yet — raise to trigger SQS retry with backoff.
        raise RuntimeError(f"workspace {ws_id} still has {len(remaining)} runtimes; re-queue")

    # Drop WorkspaceGrants policy + delete role
    if role_name:
        iam = _get_iam()
        try:
            iam.delete_role_policy(RoleName=role_name, PolicyName="WorkspaceGrants")
        except Exception:
            pass
        try:
            iam.delete_role_policy(RoleName=role_name, PolicyName="DefaultMinimal")
        except Exception:
            pass
        try:
            iam.delete_role(RoleName=role_name)
        except Exception as e:
            logger.warning("delete role failed: %s", e)

    # Delete META item
    try:
        table.delete_item(Key={"workspaceId": ws_id, "sk": "META"})
    except Exception as e:
        logger.warning("delete META failed: %s", e)


_HANDLERS = {
    "delete_runtime": _handle_delete_runtime,
    "cleanup_ws": _handle_cleanup_ws,
}


def handler(event, context):
    """SQS-triggered. One message per invocation (batchSize=1)."""
    for record in event.get("Records", []):
        try:
            msg = json.loads(record["body"])
        except Exception as e:
            logger.error("bad message body: %s", e)
            continue
        action = msg.get("action")
        handler_fn = _HANDLERS.get(action)
        if not handler_fn:
            logger.error("unknown action: %s", action)
            continue
        handler_fn(msg)
    return {"processed": len(event.get("Records", []))}
