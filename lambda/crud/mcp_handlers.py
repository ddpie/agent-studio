"""Per-workspace MCP HTTP routes (spec §6).

Thin HTTP layer over crud/mcp_runtime_manager.py. Maps manager's typed dicts
to HTTP responses per spec §6.4 error codes.
"""
from __future__ import annotations

import json

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.config import REGION, WORKSPACES_TABLE, COGNITO_USER_POOL_ID
from shared.middleware import auth_check, check_platform_admin
from shared.response import success, error, bad_request, forbidden, not_found, internal_error
from crud import mcp_runtime_manager as mgr

router = Router()
logger = Logger(child=True)

_cognito = None


def _get_cognito():
    global _cognito
    if _cognito is None:
        _cognito = boto3.client("cognito-idp", region_name=REGION)
    return _cognito


_ERROR_CODE_TO_STATUS = {
    "unknown_target": 400,
    "env_missing": 400,
    "boundary_gap": 400,
    "workspace_not_found": 404,
    "role_missing": 412,
    "policy_too_large": 413,
    "in_flight": 409,
    "not_enabled": 404,
    "image_missing": 400,
    "quota_exceeded": 507,
    "conflict_unresolved": 424,
    "resource_policy_failed": 500,
    "policy_write_failed": 500,
    "create_failed": 424,
    "update_failed": 424,
    "ceiling_stale": 500,
}


def _resolve_sensitivity_required_role(target: str) -> str:
    """Server-side sensitivity gate (spec §6.5)."""
    meta = mgr._load_registry_meta(target)
    sens = (meta or {}).get("sensitivity", "low")
    return mgr._required_role_for_sensitivity(sens)


def _resp_from_manager(result: dict):
    """Convert manager dict → HTTP Response."""
    if result.get("status") == "error":
        code = result.get("error", "unknown")
        status = _ERROR_CODE_TO_STATUS.get(code, 500)
        body = {"error": code, "message": result.get("message", "")}
        # Pass through extras (missing_actions, inflight_*, etc.)
        for k in ("missing_actions", "inflight_action", "inflight_actor", "detail"):
            if k in result:
                body[k] = result[k]
        return error(result.get("message", code), code, status)
    return success(result, status_code=202 if result.get("status") == "CREATING" else 200)


def _admin_contacts_for_workspace(ws_id: str) -> dict:
    """Query members table for admins (+emails). For 412 response body."""
    table = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    resp = table.query(
        KeyConditionExpression="workspaceId = :w AND begins_with(sk, :prefix)",
        ExpressionAttributeValues={":w": ws_id, ":prefix": "MEMBER#"},
    )
    admins = [m for m in resp.get("Items", []) if m.get("role") == "admin" or m.get("role") == "owner"]
    if not admins and COGNITO_USER_POOL_ID:
        return {"admins": [], "adminConsoleUrl": f"/#/admin/workspaces?ws={ws_id}"}

    # Batch-fetch emails from Cognito (best-effort)
    details = []
    cog = _get_cognito()
    for m in admins:
        uid = m.get("userId")
        if not uid:
            continue
        try:
            u = cog.admin_get_user(UserPoolId=COGNITO_USER_POOL_ID, Username=uid)
            attrs = {a["Name"]: a["Value"] for a in u.get("UserAttributes", [])}
            details.append({
                "userId": uid,
                "email": attrs.get("email", ""),
            })
        except Exception:
            details.append({"userId": uid, "email": ""})
    return {
        "admins": details,
        "adminConsoleUrl": f"/#/admin/workspaces?ws={ws_id}",
    }


# ─── GET /api/workspaces/{wsId}/mcp/catalog ───────────────────────
@router.get("/api/workspaces/<wsId>/mcp/catalog")
def get_catalog(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="viewer", ws_id=wsId)
    if err:
        return err
    try:
        data = mgr.list_catalog(ws_id)
        return success(data)
    except Exception as e:
        logger.exception("list_catalog failed")
        return internal_error(str(e))


# ─── POST /api/workspaces/{wsId}/mcp/{target} — enable ─────────────
@router.post("/api/workspaces/<wsId>/mcp/<target>")
def enable_mcp(wsId: str, target: str):
    # Server-side sensitivity gate: read registry sensitivity BEFORE auth_check.
    required_role = _resolve_sensitivity_required_role(target)
    user_id, ws_id, member, err = auth_check(router.current_event, min_role=required_role, ws_id=wsId)
    if err:
        return err

    # Pre-flight: workspace role exists? 412 with admin contacts.
    ws_meta = mgr._get_ws_meta(ws_id)
    if ws_meta is None:
        return not_found("Workspace not found")
    if not ws_meta.get("roleArn"):
        contacts = _admin_contacts_for_workspace(ws_id)
        return error(
            "Workspace role must be created by an admin before MCPs can be enabled.",
            "workspace_role_missing",
            412,
        )

    body = router.current_event.json_body or {}
    env = body.get("env")

    result = mgr.enable_target(ws_id, target, actor=user_id, env=env)
    return _resp_from_manager(result)


# ─── DELETE /api/workspaces/{wsId}/mcp/{target} — disable ──────────
@router.delete("/api/workspaces/<wsId>/mcp/<target>")
def disable_mcp(wsId: str, target: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err
    result = mgr.disable_target(ws_id, target, actor=user_id)
    return _resp_from_manager(result)


# ─── POST /api/workspaces/{wsId}/mcp/{target}/upgrade ──────────────
@router.post("/api/workspaces/<wsId>/mcp/<target>/upgrade")
def upgrade_mcp(wsId: str, target: str):
    # Recompute sensitivity from CURRENT registry (may have risen — spec §6.5).
    required_role = _resolve_sensitivity_required_role(target)
    user_id, ws_id, member, err = auth_check(router.current_event, min_role=required_role, ws_id=wsId)
    if err:
        return err
    result = mgr.upgrade_target(ws_id, target, actor=user_id)
    return _resp_from_manager(result)


# ─── GET /api/workspaces/{wsId}/mcp/{target} — status poll ─────────
@router.get("/api/workspaces/<wsId>/mcp/<target>")
def get_mcp_status(wsId: str, target: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="viewer", ws_id=wsId)
    if err:
        return err
    result = mgr.get_target_status(ws_id, target)
    return _resp_from_manager(result)


# ─── PUT /api/workspaces/{wsId}/mcp/{target}/env ───────────────────
@router.put("/api/workspaces/<wsId>/mcp/<target>/env")
def set_mcp_env(wsId: str, target: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err
    # v4: requires_env flow deferred (spec §14 item 5). Reject with 501.
    return error(
        "Env configuration is not supported in v1. Contact platform admin.",
        "not_implemented",
        501,
    )


# ─── GET /api/workspaces/{wsId}/mcp/agents-using/{target} ─────────
@router.get("/api/workspaces/<wsId>/mcp/agents-using/<target>")
def get_agents_using_mcp(wsId: str, target: str):
    """Return Agents referencing this target (for disable confirm modal)."""
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err
    from shared.config import AGENTS_TABLE
    ddb = boto3.resource("dynamodb", region_name=REGION)
    table = ddb.Table(AGENTS_TABLE)
    # Workspace-scoped query; filter in-memory on mcp_targets.
    # Agents table has workspace_id as an attribute; use GSI if present,
    # otherwise Scan with FilterExpression (workspaces are small).
    try:
        resp = table.scan(
            FilterExpression="workspace_id = :w",
            ExpressionAttributeValues={":w": ws_id},
        )
        matched = []
        for a in resp.get("Items", []):
            mcp_targets = a.get("mcp_targets") or []
            if target in mcp_targets:
                matched.append({
                    "agentId": a.get("agentId") or a.get("id"),
                    "name": a.get("name"),
                    "lastInvokedAt": a.get("last_invoked_at"),
                })
        return success({"agents": matched})
    except Exception as e:
        logger.exception("agents-using scan failed")
        return internal_error(str(e))
