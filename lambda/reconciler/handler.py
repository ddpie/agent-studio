"""MCP runtime reconciler — runs hourly via EventBridge (spec §6.5).

Scans workspaces META for mcp_runtimes.*.status ∈ {CREATING|UPDATING|DELETING}
with updated_at > 15 min old. Calls bedrock-agentcore:GetAgentRuntime and
updates DDB status if control-plane state has advanced.

READ-ONLY on runtimes. Never deletes. Orphan cleanup is a separate
human-initiated script (mcp-runtime-audit.py).
"""
import os
import json
import logging
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

REGION = os.environ["AGENT_STUDIO_REGION"]
WORKSPACES_TABLE = os.environ["WORKSPACES_TABLE"]

_control = None
_ddb = None


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


def _get_table():
    global _ddb
    if _ddb is None:
        _ddb = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _ddb


NON_TERMINAL = {"CREATING", "UPDATING", "DELETING"}
STALE_MINUTES = 15
_now_iso_fn = lambda: datetime.utcnow().isoformat() + "Z"


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).replace(tzinfo=None)
    except Exception:
        return None


def _reconcile_entry(ws_id: str, target: str, entry: dict) -> dict | None:
    """Check control-plane status; return updated entry dict or None if no change."""
    runtime_id = entry.get("runtime_id")
    if not runtime_id:
        return None
    try:
        info = _get_control().get_agent_runtime(agentRuntimeId=runtime_id)
    except Exception as e:
        # Runtime not found → terminal for DELETING, stuck for CREATING
        if entry.get("status") == "DELETING":
            # Mark DELETED
            return {"status": "DELETED", "inflight_action": None, "inflight_actor": None}
        logger.info("get_agent_runtime failed (ws=%s target=%s): %s", ws_id, target, e)
        return None

    live_status = info.get("status", "")
    db_status = entry.get("status", "")

    if live_status == db_status:
        return None
    if live_status in ("READY", "ACTIVE"):
        return {"status": "READY", "inflight_action": None, "inflight_actor": None}
    if live_status in ("FAILED", "CREATE_FAILED"):
        reason = info.get("failureReason") or info.get("statusReason") or "unknown"
        return {
            "status": "FAILED",
            "last_error": reason,
            "inflight_action": None,
            "inflight_actor": None,
        }
    if live_status == "DELETED":
        return {"status": "DELETED", "inflight_action": None, "inflight_actor": None}
    # Still progressing — no change (db already says CREATING/UPDATING/DELETING)
    return None


def handler(event, context):
    """EventBridge-triggered."""
    table = _get_table()
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=STALE_MINUTES)

    stats = {"scanned": 0, "reconciled": 0, "errors": 0}
    scan_kwargs = {
        "FilterExpression": "sk = :meta AND attribute_exists(mcp_runtimes)",
        "ExpressionAttributeValues": {":meta": "META"},
    }

    while True:
        try:
            resp = table.scan(**scan_kwargs)
        except Exception as e:
            logger.exception("scan failed: %s", e)
            stats["errors"] += 1
            break

        for item in resp.get("Items", []):
            stats["scanned"] += 1
            ws_id = item.get("workspaceId", "")
            mcp_runtimes = item.get("mcp_runtimes") or {}
            for target, entry in mcp_runtimes.items():
                status = entry.get("status", "")
                if status not in NON_TERMINAL:
                    continue
                updated = _parse_iso(entry.get("updated_at", ""))
                if updated and updated > cutoff:
                    # Too fresh — skip; user polls heal on-read.
                    continue
                change = _reconcile_entry(ws_id, target, entry)
                if not change:
                    continue
                # Apply change
                try:
                    update_expr = "SET mcp_runtimes.#t.#s = :s, mcp_runtimes.#t.updated_at = :now"
                    attr_names = {"#t": target, "#s": "status"}
                    attr_vals = {":s": change["status"], ":now": _now_iso_fn()}
                    for k, v in change.items():
                        if k == "status":
                            continue
                        update_expr += f", mcp_runtimes.#t.#{k} = :{k}"
                        attr_names[f"#{k}"] = k
                        attr_vals[f":{k}"] = v
                    table.update_item(
                        Key={"workspaceId": ws_id, "sk": "META"},
                        UpdateExpression=update_expr,
                        ExpressionAttributeNames=attr_names,
                        ExpressionAttributeValues=attr_vals,
                    )
                    stats["reconciled"] += 1
                    logger.info("reconciled ws=%s target=%s → %s", ws_id, target, change["status"])
                except Exception as e:
                    stats["errors"] += 1
                    logger.exception("update failed ws=%s target=%s: %s", ws_id, target, e)

        if resp.get("LastEvaluatedKey"):
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        else:
            break

    logger.info("reconciler complete: %s", stats)
    return stats
