"""Per-workspace Kiro API key — stored in Secrets Manager.

Path convention: `agent-studio/workspaces/{wsId}/kiro-api-key`.

Admins configure the key once; the whole workspace shares it. GET is
visible to any member (viewer+) but never returns the plaintext value —
only whether it's configured and audit metadata. PUT / DELETE are admin-
only. The Invoke Lambda fetches the value at invoke time and injects it
into the Meta-Agent payload so the plaintext never lands in Runtime env.

Usage (GET /kiro-key/usage): viewer+ can see live credit balance +
subscription tier. We shell out to the Meta-Agent runtime's embedded
`kiro-cli-chat chat "/usage"` command via InvokeAgentRuntime; the
runtime parses the TUI output and returns structured JSON. Cached
in-process for 60s to avoid hammering Kiro on every page visit.
"""
import json
import time
from datetime import datetime, timezone

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError

from shared.config import COGNITO_USER_POOL_ID, META_AGENT_ARN, REGION
from shared.middleware import auth_check
from shared.response import bad_request, internal_error, success

router = Router()
logger = Logger(child=True)

_sm = None
_cognito = None
_agentcore = None
# Cache Cognito sub → human label to avoid an admin_get_user per GET.
# Safe to span workspaces: sub→attributes is global to the user pool.
_identity_cache: dict[str, str] = {}

# Usage cache: {ws_id: (epoch_seconds_fetched, payload_dict)}. The TTL
# exists purely to keep us from re-invoking the Meta-Agent runtime on
# every settings-page refresh — Kiro's own dashboard updates every ~5
# min anyway, so 60s is tighter than the source of truth. Per-process
# so a fleet of warm Lambdas can each hold a copy without coordination.
_usage_cache: dict[str, tuple[float, dict]] = {}
_USAGE_CACHE_TTL_S = 60.0

# Whitelist enforced server-side. Kiro currently serves us-east-1 and
# eu-central-1 for commercial customers; GovCloud is out of scope per
# product decision. New regions can be added here without a frontend
# change (the dropdown also sources this list via the same env).
_ALLOWED_REGIONS = {"us-east-1", "eu-central-1"}
_DEFAULT_REGION = "us-east-1"


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


def _get_agentcore():
    global _agentcore
    if _agentcore is None:
        _agentcore = boto3.client("bedrock-agentcore", region_name=REGION)
    return _agentcore


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
    except Exception as e:
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


def _region_from_describe(info: dict) -> str:
    """Pull `kiroRegion` tag off a Describe response, default us-east-1."""
    for t in info.get("Tags", []) or []:
        if t.get("Key") == "kiroRegion":
            val = (t.get("Value") or "").strip()
            if val in _ALLOWED_REGIONS:
                return val
    return _DEFAULT_REGION


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
        return success({
            "configured": False,
            "lastUpdated": None,
            "updatedBy": None,
            "region": _DEFAULT_REGION,
        })

    tags = {t["Key"]: t["Value"] for t in info.get("Tags", [])}
    last_changed = info.get("LastChangedDate") or info.get("CreatedDate")
    raw_sub = tags.get("updatedBy")
    return success({
        "configured": True,
        "lastUpdated": last_changed.isoformat() if last_changed else None,
        "updatedBy": _resolve_user_label(raw_sub) if raw_sub else None,
        "region": _region_from_describe(info),
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

    region = (body.get("region") or _DEFAULT_REGION).strip()
    if region not in _ALLOWED_REGIONS:
        return bad_request(f"region must be one of {sorted(_ALLOWED_REGIONS)}")

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
            {"Key": "kiroRegion", "Value": region},
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
                    {"Key": "kiroRegion", "Value": region},
                ],
            )
        except sm.exceptions.ResourceExistsException:
            _put_and_tag()

    # Rotating the key (or changing region) invalidates any cached
    # usage snapshot for this workspace — the next GET /usage must hit
    # Kiro fresh so admins see their new subscription plan immediately.
    _usage_cache.pop(ws_id, None)

    return success({
        "configured": True,
        "lastUpdated": now,
        "updatedBy": _resolve_user_label(user_id),
        "region": region,
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
    _usage_cache.pop(ws_id, None)
    return success({"configured": False})


def _fetch_key_and_region(ws_id: str) -> tuple[str, str] | None:
    """Read the plaintext key + region tag for a workspace.

    Returns None if the secret doesn't exist. Bubbles up the exception
    on any other failure.
    """
    sm = _get_sm()
    name = _secret_name(ws_id)
    try:
        value = sm.get_secret_value(SecretId=name)
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return None
        raise
    api_key = (value.get("SecretString") or "").strip()

    # Describe separately so we can read tags without a second API call
    # on the hot path — GetSecretValue doesn't return tags.
    try:
        info = sm.describe_secret(SecretId=name)
        region = _region_from_describe(info)
    except Exception:
        region = _DEFAULT_REGION
    return api_key, region


def _invoke_runtime_for_usage(api_key: str, region: str) -> dict:
    """Call the Meta-Agent runtime with action=get_usage; return its frame.

    Returns the decoded JSON dict the runtime yields (either
    `{"__usage": {...}}` or `{"__error": "..."}`). Raises on transport
    failure — the caller translates to a 5xx.
    """
    if not META_AGENT_ARN:
        raise RuntimeError("META_AGENT_ARN not configured")

    payload = {
        "action": "get_usage",
        "kiro_api_key": api_key,
        "kiro_region": region,
        # Unused in the get_usage branch but harmless — some code paths
        # assume these keys exist.
        "prompt": "",
        "history": [],
    }
    resp = _get_agentcore().invoke_agent_runtime(
        agentRuntimeArn=META_AGENT_ARN,
        qualifier="DEFAULT",
        payload=json.dumps(payload).encode("utf-8"),
    )
    body = resp.get("response")
    if body is None:
        raise RuntimeError("empty runtime response")

    # AgentCore serializes each yielded frame as an SSE message of the
    # form:  `data: "<json-escaped-string>"\n\n`
    # So the raw response is one-or-more SSE records, each wrapping a
    # double-encoded JSON blob — json.loads once to strip the SSE outer
    # string, then again to get the object.
    raw = body.read() if hasattr(body, "read") else body
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")

    last_obj = None
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            # First decode: the SSE-escaped outer string.
            inner = json.loads(payload)
        except json.JSONDecodeError:
            continue
        # If the first decode already returned an object (framework
        # changed to send raw JSON without wrapping), use it directly.
        if isinstance(inner, dict):
            last_obj = inner
            continue
        if isinstance(inner, str):
            try:
                last_obj = json.loads(inner)
            except json.JSONDecodeError:
                continue
    if last_obj is None:
        raise RuntimeError(
            f"unparseable runtime response: {(raw or '')[:200]!r}"
        )
    return last_obj


@router.get("/api/workspaces/<wsId>/kiro-key/usage")
def get_kiro_usage(wsId: str):
    """Return live credit usage for the workspace's Kiro key (viewer+).

    Cached per-workspace for 60s to shield the Meta-Agent runtime from
    being hammered every time the settings tab is viewed. Cache entry
    is busted on PUT/DELETE of the key.

    Response shape:

        {
          "configured": true,
          "region": "us-east-1",
          "tier": "KIRO POWER",
          "currentUsage": 204.98,
          "usageLimit": 10000,
          "resetsOn": "2026-05-01",
          "overagesEnabled": true,
          "overageRate": 0.04,
          "overageUsed": 0.0,
          "currency": "USD",
          "fetchedAt": "2026-04-25T12:34:56.000Z"
        }

    Any failure to reach Kiro surfaces as
    `{"configured": true, "error": "<code>"}` with HTTP 200 — the
    settings panel shows a soft error rather than spinning forever.
    """
    _, ws_id, _, err = auth_check(router.current_event, min_role="viewer", ws_id=wsId)
    if err:
        return err

    # Serve from cache when fresh. Skip the 1-entry branch when empty so
    # the first call always hits the runtime.
    cached = _usage_cache.get(ws_id)
    if cached:
        cached_at, cached_body = cached
        if time.time() - cached_at < _USAGE_CACHE_TTL_S:
            return success(cached_body)

    try:
        pair = _fetch_key_and_region(ws_id)
    except Exception:
        logger.exception("fetch_key_and_region failed")
        return internal_error()

    if pair is None:
        return success({"configured": False})

    api_key, region = pair
    if not api_key:
        return success({"configured": False})

    try:
        frame = _invoke_runtime_for_usage(api_key, region)
    except Exception as e:
        logger.exception("invoke_runtime for usage failed")
        # Soft-error: return 200 so the UI shows a warning banner
        # instead of a crash screen. Admin can retry, or refresh the
        # key if auth has drifted.
        return success({
            "configured": True,
            "region": region,
            "error": "runtime_invoke_failed",
            "detail": str(e)[:200],
        })

    if "__error" in frame:
        return success({
            "configured": True,
            "region": region,
            "error": frame.get("__error"),
        })

    data = frame.get("__usage") or {}
    body = {
        "configured": True,
        "region": region,
        "tier": data.get("tier") or "",
        "currentUsage": data.get("currentUsage"),
        "usageLimit": data.get("usageLimit"),
        "resetsOn": data.get("resetsOn") or "",
        "overagesEnabled": bool(data.get("overagesEnabled")),
        "overageRate": data.get("overageRate") or 0.0,
        "overageUsed": data.get("overageUsed") or 0.0,
        "currency": data.get("currency") or "USD",
        "fetchedAt": datetime.now(timezone.utc).isoformat(),
    }
    _usage_cache[ws_id] = (time.time(), body)
    return success(body)
