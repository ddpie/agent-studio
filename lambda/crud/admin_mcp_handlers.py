"""Platform-admin MCP routes (spec §6.1, D13).

Admin-only:
  - GET /api/admin/mcp/fleet — workspace × target → status matrix
  - POST /api/admin/mcp/broadcast-upgrade — rolling upgrade of a target
     across all workspaces that have it enabled
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.config import REGION
from shared.middleware import check_platform_admin
from shared.response import success, error, forbidden, bad_request, internal_error
from crud import mcp_runtime_manager as mgr

router = Router()
logger = Logger(child=True)

AUDIT_TABLE = os.environ.get("AUDIT_TABLE", "")
_audit_table = None


def _get_audit_table():
    global _audit_table
    if _audit_table is None and AUDIT_TABLE:
        _audit_table = boto3.resource("dynamodb", region_name=REGION).Table(AUDIT_TABLE)
    return _audit_table


def _write_audit(pk: str, actor: str, action: str, params: dict, result: dict) -> None:
    """Write to audit table with 1-year TTL."""
    table = _get_audit_table()
    if not table:
        logger.info("audit table not configured; skipping audit write")
        return
    now_iso = datetime.utcnow().isoformat() + "Z"
    expires = int(time.time()) + 365 * 86400
    try:
        table.put_item(Item={
            "pk": pk,
            "sk": now_iso,
            "actor": actor,
            "action": action,
            "params": json.dumps(params),
            "result": json.dumps(result),
            "expires_at": expires,
        })
    except Exception as e:
        logger.warning("audit write failed: %s", e)


# ─── GET /api/admin/mcp/fleet ───────────────────────────────────
@router.get("/api/admin/mcp/fleet")
def get_mcp_fleet():
    user_id, is_admin, err = check_platform_admin(router.current_event)
    if err:
        return err
    if not is_admin:
        return forbidden()
    try:
        data = mgr.get_fleet()
        return success(data)
    except Exception as e:
        logger.exception("get_fleet failed")
        return internal_error(str(e))


# ─── POST /api/admin/mcp/broadcast-upgrade ──────────────────────
@router.post("/api/admin/mcp/broadcast-upgrade")
def broadcast_upgrade():
    """Rolling upgrade across all workspaces with target enabled.

    Body: {"target": "cloudwatch"}
    Batches 10 workspaces at a time with 30s delay (quota safety).
    """
    user_id, is_admin, err = check_platform_admin(router.current_event)
    if err:
        return err
    if not is_admin:
        return forbidden()

    body = router.current_event.json_body or {}
    target = body.get("target")
    if not target:
        return bad_request("target is required")

    # Collect workspaces with target enabled
    fleet = mgr.get_fleet()
    workspaces = sorted({
        row["workspaceId"] for row in fleet["rows"]
        if row["target"] == target and row.get("status") in ("READY", "ACTIVE")
    })
    if not workspaces:
        return success({"target": target, "upgraded": 0, "message": "No workspaces have this target enabled."})

    # Fire upgrades in batches of 10 with 30s delay.
    upgraded = 0
    errors = []
    for i in range(0, len(workspaces), 10):
        batch = workspaces[i:i + 10]
        for ws_id in batch:
            try:
                r = mgr.upgrade_target(ws_id, target, actor=user_id)
                if r.get("status") == "UPDATING":
                    upgraded += 1
                else:
                    errors.append({"workspaceId": ws_id, "error": r.get("error"), "message": r.get("message")})
            except Exception as e:
                errors.append({"workspaceId": ws_id, "error": "exception", "message": str(e)})
        if i + 10 < len(workspaces):
            time.sleep(30)

    result = {"target": target, "upgraded": upgraded, "errors": errors, "total": len(workspaces)}
    _write_audit(
        pk="AUDIT#platform-admin",
        actor=user_id,
        action="broadcast_upgrade",
        params={"target": target, "workspaceCount": len(workspaces)},
        result=result,
    )
    return success(result)
