"""Per-workspace Kiro API key — stored in Secrets Manager.

Path convention: `agent-studio/workspaces/{wsId}/kiro-api-key`.

Admins configure the key once; the whole workspace shares it. GET is
visible to any member (viewer+) but never returns the plaintext value —
only whether it's configured and audit metadata. PUT / DELETE are admin-
only. The Invoke Lambda fetches the value at invoke time and injects it
into the Meta-Agent payload so the plaintext never lands in Runtime env.
"""
import json
from datetime import datetime, timezone

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError

from shared.config import REGION, COGNITO_USER_POOL_ID
from shared.middleware import auth_check
from shared.response import success, bad_request, forbidden, not_found, internal_error

router = Router()
logger = Logger(child=True)

_sm = None
_cognito = None
# Cache Cognito sub → human label to avoid an admin_get_user per GET.
# Safe to span workspaces: sub→attributes is global to the user pool.
_identity_cache: dict[str, str] = {}


def _get_sm():
    global _sm
    if _sm is None:
        _sm = boto3.client("secretsmanager", region_name=REGION)
    return _sm


def _get_cognito():
    global _cognito
    if _cognito is None:
        _cognito = boto3.client("cognito-idp", region_name=REGION)
    return _cognito


def _resolve_user_label(user_id: str) -> str:
    """Turn a raw Cognito sub into a human label like `name (email)`.

    Falls back to the raw sub on any Cognito failure — the UI is better
    off showing the id than nothing, and we don't want a transient
    Cognito blip to hide the audit trail entirely.
    """
    if not user_id or not COGNITO_USER_POOL_ID:
        return user_id
    cached = _identity_cache.get(user_id)
    if cached is not None:
        return cached
    try:
        resp = _get_cognito().admin_get_user(
            UserPoolId=COGNITO_USER_POOL_ID, Username=user_id,
        )
        attrs = {a["Name"]: a["Value"] for a in resp.get("UserAttributes", [])}
        name = attrs.get("name") or ""
        email = attrs.get("email", "")
        if name and email:
            label = f"{name} ({email})"
        else:
            label = name or email or user_id
    except Exception as e:  # noqa: BLE001
        logger.warning("cognito admin_get_user failed",
                       extra={"userId": user_id, "error": str(e)})
        label = user_id
    _identity_cache[user_id] = label
    return label


def _secret_name(ws_id: str) -> str:
    return f"agent-studio/workspaces/{ws_id}/kiro-api-key"


def _describe(ws_id: str) -> dict | None:
    """Return Describe response or None if the secret doesn't exist."""
    try:
        return _get_sm().describe_secret(SecretId=_secret_name(ws_id))
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return None
        raise


@router.get("/api/workspaces/<wsId>/kiro-key")
def get_kiro_key(wsId: str):
    """Report whether the Kiro key is configured. Never returns plaintext."""
    _, ws_id, _, err = auth_check(router.current_event, min_role="viewer", ws_id=wsId)
    if err:
        return err
    try:
        info = _describe(ws_id)
    except Exception:
        logger.exception("describe_secret failed")
        return internal_error()

    if not info:
        return success({"configured": False, "lastUpdated": None, "updatedBy": None})

    tags = {t["Key"]: t["Value"] for t in info.get("Tags", [])}
    last_changed = info.get("LastChangedDate") or info.get("CreatedDate")
    raw_sub = tags.get("updatedBy")
    return success({
        "configured": True,
        "lastUpdated": last_changed.isoformat() if last_changed else None,
        "updatedBy": _resolve_user_label(raw_sub) if raw_sub else None,
    })


@router.put("/api/workspaces/<wsId>/kiro-key")
def put_kiro_key(wsId: str):
    """Create or overwrite the workspace's Kiro API key (admin only)."""
    user_id, ws_id, _, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    api_key = (body.get("apiKey") or "").strip()
    if not api_key:
        return bad_request("apiKey is required")
    # Kiro keys are short — cap at 4KB to reject anyone pasting a whole
    # JSON blob by accident and keep Secrets Manager happy.
    if len(api_key) > 4096:
        return bad_request("apiKey is too long")

    sm = _get_sm()
    name = _secret_name(ws_id)
    now = datetime.now(timezone.utc).isoformat()

    # Last-write-wins across admins. Workspace-wide secret, no finer
    # concurrency primitive needed — the UI surfaces `updatedBy` so users
    # can see who stomped on whom.
    def _put_and_tag():
        sm.put_secret_value(SecretId=name, SecretString=api_key)
        # Refresh the updatedBy tag on overwrite so Describe reflects the
        # caller who just rotated the key, not whoever set it originally.
        sm.tag_resource(SecretId=name, Tags=[
            {"Key": "updatedBy", "Value": user_id},
            {"Key": "updatedAt", "Value": now},
        ])

    try:
        _put_and_tag()
    except sm.exceptions.ResourceNotFoundException:
        # Try to create. If two admins race the PUT both see
        # ResourceNotFoundException and both attempt CreateSecret — the
        # loser gets ResourceExistsException. Fall back to put_and_tag
        # once in that case (the secret exists now, we just didn't win
        # the creation).
        try:
            sm.create_secret(
                Name=name,
                SecretString=api_key,
                Tags=[
                    {"Key": "updatedBy", "Value": user_id},
                    {"Key": "updatedAt", "Value": now},
                    {"Key": "purpose", "Value": "kiro-api-key"},
                    {"Key": "workspaceId", "Value": ws_id},
                ],
            )
        except sm.exceptions.ResourceExistsException:
            _put_and_tag()

    return success({
        "configured": True,
        "lastUpdated": now,
        "updatedBy": _resolve_user_label(user_id),
    })


@router.delete("/api/workspaces/<wsId>/kiro-key")
def delete_kiro_key(wsId: str):
    """Remove the workspace's Kiro API key (admin only)."""
    _, ws_id, _, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err
    try:
        _get_sm().delete_secret(
            SecretId=_secret_name(ws_id),
            ForceDeleteWithoutRecovery=True,
        )
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return success({"configured": False})
        logger.exception("delete_secret failed")
        return internal_error()
    return success({"configured": False})
