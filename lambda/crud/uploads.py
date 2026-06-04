"""Upload presigned URL generation and public listing endpoints."""
import json
import uuid
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key

from shared.auth import check_permission, get_membership, verify_jwt
from shared.config import AGENTS_TABLE, ASSETS_BUCKET, REGION, SKILLS_TABLE, TOOLS_TABLE
from shared.middleware import auth_check
from shared.response import bad_request, forbidden, internal_error, not_found, paginated, success
from shared.validators import parse_pagination, validate_id, validate_path

router = Router()
logger = Logger(child=True)

_s3 = None
_agents_table = None
_skills_table = None
_tools_table = None


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _get_agents_table():
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _agents_table


def _get_skills_table():
    global _skills_table
    if _skills_table is None:
        _skills_table = boto3.resource("dynamodb", region_name=REGION).Table(SKILLS_TABLE)
    return _skills_table


def _get_tools_table():
    global _tools_table
    if _tools_table is None:
        _tools_table = boto3.resource("dynamodb", region_name=REGION).Table(TOOLS_TABLE)
    return _tools_table


def _verify_bearer_jwt():
    """Verify Cognito JWT header for public endpoints. Returns (user_id, error_response)."""
    auth_header = router.current_event.get_header_value("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return None, forbidden()
    try:
        claims = verify_jwt(auth_header[7:])
        return claims.get("sub"), None
    except Exception:
        return None, forbidden()


def _resolve_caller_workspace(user_id: str):
    """Resolve the caller's current workspace (must be editor+ to clone into it).

    Returns (ws_id, error_response).
    """
    ws_id = router.current_event.get_header_value("x-workspace-id") or ""
    ws_err = validate_id(ws_id, "workspaceId")
    if ws_err:
        return None, bad_request("x-workspace-id header is required")
    member = get_membership(ws_id, user_id)
    if not check_permission(member, "editor"):
        return None, forbidden()
    return ws_id, None


ALLOWED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml"}
ALLOWED_ATTACHMENT_TYPES = {
    "application/pdf", "text/plain", "text/markdown",
    "application/json", "text/csv",
}
MAX_IMAGE_SIZE = 5 * 1024 * 1024
MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024
PRESIGNED_URL_EXPIRY = 900


@router.post("/api/workspaces/<wsId>/uploads/images")
def upload_image(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    content_type = body.get("content_type", "").strip()
    filename = body.get("filename", "").strip()

    if not content_type:
        return bad_request("content_type is required")
    if content_type not in ALLOWED_IMAGE_TYPES:
        return bad_request(f"content_type must be one of: {', '.join(sorted(ALLOWED_IMAGE_TYPES))}")
    if not filename:
        return bad_request("filename is required")

    import re
    if not filename or len(filename) > 255 or re.search(r'[/\\\x00]|\.\.', filename):
        return bad_request("Invalid filename: must not contain / \\ .. or null bytes, max 255 chars")

    ext = filename.rsplit(".", 1)[-1] if "." in filename else "png"
    upload_id = str(uuid.uuid4())
    s3_key = f"uploads/images/{upload_id}.{ext}"

    s3 = _get_s3()
    presigned = s3.generate_presigned_post(
        Bucket=ASSETS_BUCKET,
        Key=s3_key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, MAX_IMAGE_SIZE],
        ],
        ExpiresIn=PRESIGNED_URL_EXPIRY,
    )

    return success({
        "uploadUrl": presigned["url"],
        "fields": presigned["fields"],
        "s3Key": s3_key,
        "expiresIn": PRESIGNED_URL_EXPIRY,
    })


@router.post("/api/workspaces/<wsId>/uploads/attachments")
def upload_attachment(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    content_type = body.get("content_type", "").strip()
    filename = body.get("filename", "").strip()
    session_id = body.get("sessionId", "").strip()

    if not content_type:
        return bad_request("content_type is required")
    if content_type not in ALLOWED_ATTACHMENT_TYPES:
        return bad_request(f"content_type must be one of: {', '.join(sorted(ALLOWED_ATTACHMENT_TYPES))}")
    if not filename:
        return bad_request("filename is required")
    if not session_id:
        return bad_request("sessionId is required")

    import re
    if not re.match(r"^[a-zA-Z0-9_-]+$", session_id):
        return bad_request("Invalid sessionId: must match [a-zA-Z0-9_-]+")
    if not filename or len(filename) > 255 or re.search(r'[/\\\x00]|\.\.', filename):
        return bad_request("Invalid filename: must not contain / \\ .. or null bytes, max 255 chars")

    s3_key = f"uploads/attachments/{session_id}/{filename}"

    s3 = _get_s3()
    presigned = s3.generate_presigned_post(
        Bucket=ASSETS_BUCKET,
        Key=s3_key,
        Fields={"Content-Type": content_type},
        Conditions=[
            {"Content-Type": content_type},
            ["content-length-range", 1, MAX_ATTACHMENT_SIZE],
        ],
        ExpiresIn=PRESIGNED_URL_EXPIRY,
    )

    return success({
        "uploadUrl": presigned["url"],
        "fields": presigned["fields"],
        "s3Key": s3_key,
        "expiresIn": PRESIGNED_URL_EXPIRY,
    })


@router.get("/api/workspaces/<wsId>/downloads")
def get_download_url(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    key = (router.current_event.query_string_parameters or {}).get("key", "")
    key_err = validate_path(key)
    if key_err:
        return bad_request(key_err)

    allowed_prefixes = ("outputs/", "agents/", "uploads/", "skills/")
    if not any(key.startswith(p) for p in allowed_prefixes):
        return bad_request("Invalid key prefix")

    # Resource ownership check
    try:
        if key.startswith("agents/"):
            parts = key.split("/")
            if len(parts) >= 2:
                agent = _get_agents_table().get_item(Key={"agentId": parts[1]}).get("Item")
                if not agent or agent.get("workspace_id") != ws_id:
                    logger.warning("Download denied: agent not in workspace", extra={"key": key, "ws_id": ws_id})
                    return forbidden()
                if agent.get("status") == "archived":
                    return forbidden()
        elif key.startswith("skills/"):
            parts = key.split("/")
            if len(parts) >= 2:
                skill = _get_skills_table().get_item(Key={"skillId": parts[1]}).get("Item")
                if not skill or skill.get("workspace_id") != ws_id:
                    logger.warning("Download denied: skill not in workspace", extra={"key": key, "ws_id": ws_id})
                    return forbidden()
                if skill.get("deleted"):
                    return forbidden()
        # outputs/ and uploads/ rely on UUID unguessability
    except Exception:
        logger.exception("Error checking resource ownership for download")
        return forbidden()

    try:
        s3 = _get_s3()
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": ASSETS_BUCKET,
                "Key": key,
                "ResponseContentDisposition": "attachment",
            },
            ExpiresIn=300,
        )
        return success({"url": url, "expiresIn": 300})
    except Exception:
        logger.exception("Failed to generate presigned download URL")
        return bad_request("Failed to generate download URL")


_STORAGE_PREFIXES = ("tool-history/", "staging/", "drafts/")
MAX_STORAGE_SIZE = 1_048_576  # 1MB


@router.get("/api/workspaces/<wsId>/storage")
def get_storage(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    key = (router.current_event.query_string_parameters or {}).get("key", "")
    key_err = validate_path(key)
    if key_err:
        return bad_request(key_err)
    if not any(key.startswith(p) for p in _STORAGE_PREFIXES):
        return bad_request("Invalid key prefix")

    # staging/drafts auto-inject userId for user isolation
    if key.startswith("staging/") or key.startswith("drafts/"):
        parts = key.split("/", 1)
        key = f"{parts[0]}/{user_id}/{parts[1]}"

    s3_key = f"workspaces/{ws_id}/storage/{key}"
    s3 = _get_s3()
    try:
        obj = s3.get_object(Bucket=ASSETS_BUCKET, Key=s3_key)
        content = obj["Body"].read().decode("utf-8")
        import json as _json
        try:
            data = _json.loads(content)
        except Exception:
            logger.warning("Storage value is not valid JSON", extra={"key": key})
            data = None
    except s3.exceptions.NoSuchKey:
        data = None
    except Exception:
        logger.exception("Failed to read storage", extra={"key": key, "ws_id": ws_id})
        data = None

    return success({"key": key, "data": data})


@router.put("/api/workspaces/<wsId>/storage")
def put_storage(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    key = body.get("key", "")
    data = body.get("data")

    key_err = validate_path(key)
    if key_err:
        return bad_request(key_err)
    if not any(key.startswith(p) for p in _STORAGE_PREFIXES):
        return bad_request("Invalid key prefix")

    # staging/drafts auto-inject userId for user isolation
    if key.startswith("staging/") or key.startswith("drafts/"):
        parts = key.split("/", 1)
        key = f"{parts[0]}/{user_id}/{parts[1]}"

    import json as _json
    content = _json.dumps(data)
    if len(content) > MAX_STORAGE_SIZE:
        return bad_request("Content too large (max 1MB)")

    s3_key = f"workspaces/{ws_id}/storage/{key}"
    s3 = _get_s3()
    try:
        s3.put_object(Bucket=ASSETS_BUCKET, Key=s3_key, Body=content.encode("utf-8"), ContentType="application/json")
    except Exception:
        logger.exception("Failed to write storage", extra={"key": key, "ws_id": ws_id})
        return internal_error()
    return success({"key": key})


@router.delete("/api/workspaces/<wsId>/storage")
def delete_storage(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    key = (router.current_event.query_string_parameters or {}).get("key", "")
    key_err = validate_path(key)
    if key_err:
        return bad_request(key_err)
    if not any(key.startswith(p) for p in _STORAGE_PREFIXES):
        return bad_request("Invalid key prefix")

    # staging/drafts auto-inject userId for user isolation
    if key.startswith("staging/") or key.startswith("drafts/"):
        parts = key.split("/", 1)
        key = f"{parts[0]}/{user_id}/{parts[1]}"

    s3_key = f"workspaces/{ws_id}/storage/{key}"
    s3 = _get_s3()
    try:
        s3.delete_object(Bucket=ASSETS_BUCKET, Key=s3_key)
    except Exception:
        logger.exception("Failed to delete storage", extra={"key": key, "ws_id": ws_id})
        return internal_error()
    return success({"deleted": True})


@router.get("/api/public/agents")
def list_public_agents():
    _, err = _verify_bearer_jwt()
    if err:
        return err

    limit, cursor = parse_pagination(router.current_event.query_string_parameters or {})
    table = _get_agents_table()

    query_kwargs = {
        "IndexName": "public-index",
        "KeyConditionExpression": Key("visibility").eq("public"),
        "ScanIndexForward": False,
        "Limit": limit,
        "FilterExpression": "attribute_not_exists(#st) OR #st <> :archived",
        "ExpressionAttributeNames": {"#st": "status"},
        "ExpressionAttributeValues": {":archived": "archived"},
    }
    if cursor:
        import base64
        try:
            query_kwargs["ExclusiveStartKey"] = json.loads(base64.b64decode(cursor).decode())
        except Exception:
            return bad_request("Invalid cursor")

    resp = table.query(**query_kwargs)
    items = []
    for item in resp.get("Items", []):
        items.append({
            "agentId": item.get("agentId", ""),
            "name": item.get("name", ""),
            "description": item.get("description", ""),
            "model_id": item.get("model_id", ""),
            "supports_images": item.get("supports_images", False),
            "welcome_message": item.get("welcome_message", ""),
            "created_at": item.get("created_at", ""),
        })

    last_key = resp.get("LastEvaluatedKey")
    while len(items) < limit and last_key:
        query_kwargs["ExclusiveStartKey"] = last_key
        query_kwargs["Limit"] = limit - len(items)
        resp = table.query(**query_kwargs)
        for item in resp.get("Items", []):
            items.append({
                "agentId": item.get("agentId", ""),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "model_id": item.get("model_id", ""),
                "supports_images": item.get("supports_images", False),
                "welcome_message": item.get("welcome_message", ""),
                "created_at": item.get("created_at", ""),
            })
        last_key = resp.get("LastEvaluatedKey")

    next_cursor = None
    if last_key:
        import base64
        next_cursor = base64.b64encode(json.dumps(last_key).encode()).decode()

    result = paginated(items, next_cursor)
    try:
        result.headers["Cache-Control"] = "public, max-age=30"
    except Exception:
        pass
    return result


@router.get("/api/public/skills")
def list_public_skills():
    _, err = _verify_bearer_jwt()
    if err:
        return err

    limit, cursor = parse_pagination(router.current_event.query_string_parameters or {})
    table = _get_skills_table()

    items = []
    scan_kwargs = {
        "FilterExpression": "visibility = :pub AND (attribute_not_exists(deleted) OR deleted = :f)",
        "ExpressionAttributeValues": {":pub": "public", ":f": False},
    }
    if cursor:
        import base64
        try:
            scan_kwargs["ExclusiveStartKey"] = json.loads(base64.b64decode(cursor).decode())
        except Exception:
            return bad_request("Invalid cursor")

    last_key = None
    while len(items) < limit:
        scan_kwargs["Limit"] = limit - len(items)
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            items.append({
                "skillId": item.get("skillId", ""),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "type": item.get("type", "prompt"),
                "tags": item.get("tags", []),
                "created_at": item.get("created_at", ""),
            })
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    next_cursor = None
    if last_key:
        import base64
        next_cursor = base64.b64encode(json.dumps(last_key).encode()).decode()

    result = paginated(items, next_cursor)
    try:
        result.headers["Cache-Control"] = "public, max-age=30"
    except Exception:
        pass
    return result


# ── Public tools listing ──


@router.get("/api/public/tools")
def list_public_tools():
    _, err = _verify_bearer_jwt()
    if err:
        return err

    limit, cursor = parse_pagination(router.current_event.query_string_parameters or {})
    table = _get_tools_table()

    items = []
    scan_kwargs = {
        "FilterExpression": (
            "visibility = :pub AND (attribute_not_exists(deleted) OR deleted = :f)"
        ),
        "ExpressionAttributeValues": {":pub": "public", ":f": False},
    }
    if cursor:
        import base64
        try:
            scan_kwargs["ExclusiveStartKey"] = json.loads(base64.b64decode(cursor).decode())
        except Exception:
            return bad_request("Invalid cursor")

    last_key = None
    while len(items) < limit:
        scan_kwargs["Limit"] = limit - len(items)
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            # Field whitelist: metadata only, never source code
            items.append({
                "toolId": item.get("toolId", ""),
                "name": item.get("name", ""),
                "description": item.get("description", ""),
                "category": item.get("category", ""),
                "created_at": item.get("created_at", ""),
            })
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        scan_kwargs["ExclusiveStartKey"] = last_key

    next_cursor = None
    if last_key:
        import base64
        next_cursor = base64.b64encode(json.dumps(last_key).encode()).decode()

    result = paginated(items, next_cursor)
    try:
        result.headers["Cache-Control"] = "public, max-age=30"
    except Exception:
        pass
    return result


# ── Public clone endpoints ──
#
# Clone copies a published resource into the caller's workspace as a private copy.
# Target workspace is passed via `x-workspace-id` header; caller must be editor+ there.
# Source resource must have visibility=public at the moment of clone.


@router.post("/api/public/agents/<agentId>/clone")
def clone_public_agent(agentId: str):
    user_id, err = _verify_bearer_jwt()
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    dst_ws_id, ws_err = _resolve_caller_workspace(user_id)
    if ws_err:
        return ws_err

    table = _get_agents_table()
    src = table.get_item(Key={"agentId": agentId}, ConsistentRead=True).get("Item")
    if not src or src.get("visibility") != "public" or src.get("status") == "archived":
        return not_found()

    new_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    # Derive a new alphanumeric-only name ≤ 36 chars; append short suffix to avoid collisions
    base_name = "".join(c for c in (src.get("name") or "agent") if c.isalnum())[:28] or "agent"
    suffix = new_id.replace("-", "")[:7]
    new_name = f"{base_name}{suffix}"[:36]

    body = router.current_event.json_body or {}
    override_name = (body.get("name") or "").strip()
    if override_name:
        cleaned = "".join(c for c in override_name if c.isalnum())[:36]
        if cleaned:
            new_name = cleaned

    # Field whitelist: keep configuration, drop deployment/runtime state.
    item = {
        "agentId": new_id,
        "workspace_id": dst_ws_id,
        "name": new_name,
        "display_name": src.get("display_name", "") or src.get("displayName", "") or src.get("name", ""),
        "displayName": src.get("displayName", "") or src.get("display_name", "") or src.get("name", ""),
        "description": src.get("description", ""),
        "model_id": src.get("model_id", ""),
        "default_model_id": src.get("default_model_id", ""),
        "template_id": src.get("template_id", ""),
        "supports_images": src.get("supports_images", False),
        "welcome_message": src.get("welcome_message", ""),
        "suggestions": src.get("suggestions", []),
        "tool_names": src.get("tool_names", []),
        "skills": src.get("skills", []),
        "skill_ids": [s.get("id", "") for s in src.get("skills", []) if isinstance(s, dict)] if src.get("skills") else [],
        "mcp_targets": src.get("mcp_targets", []),
        "status": "active",
        "visibility": "private",
        "cloned_from": agentId,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }
    table.put_item(Item=item)

    # Copy S3 artifacts (metadata, prompt, tool code, skills) so the clone
    # can be deployed without re-configuring from scratch via Meta-Agent.
    s3 = _get_s3()
    src_prefix = f"agents/{agentId}/"
    dst_prefix = f"agents/{new_id}/"
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=ASSETS_BUCKET, Prefix=src_prefix):
            for obj in page.get("Contents", []):
                src_key = obj["Key"]
                # Skip deployment.zip — clone must be (re-)deployed via Meta-Agent.
                # Skip assistant-history and staging — session-specific.
                rel = src_key[len(src_prefix):]
                if rel.startswith("deployment.zip") or rel.startswith("assistant-history") or rel.startswith("staging"):
                    continue
                dst_key = dst_prefix + rel
                s3.copy_object(
                    Bucket=ASSETS_BUCKET,
                    CopySource={"Bucket": ASSETS_BUCKET, "Key": src_key},
                    Key=dst_key,
                )
    except Exception as e:
        logger.warning("S3 artifact copy failed during agent clone (agent record still created)",
                       extra={"src": agentId, "dst": new_id, "error": str(e)})

    return success({"agentId": new_id, "name": new_name, "workspace_id": dst_ws_id}, status_code=201)


@router.post("/api/public/skills/<skillId>/clone")
def clone_public_skill(skillId: str):
    user_id, err = _verify_bearer_jwt()
    if err:
        return err

    id_err = validate_id(skillId, "skillId")
    if id_err:
        return bad_request(id_err)

    dst_ws_id, ws_err = _resolve_caller_workspace(user_id)
    if ws_err:
        return ws_err

    table = _get_skills_table()
    src = table.get_item(Key={"skillId": skillId}, ConsistentRead=True).get("Item")
    if not src or src.get("visibility") != "public" or src.get("deleted"):
        return not_found()

    new_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    item = {
        "skillId": new_id,
        "workspace_id": dst_ws_id,
        "name": src.get("name", ""),
        "description": src.get("description", ""),
        "type": src.get("type", "prompt"),
        # Script skills re-enter approval workflow in the destination workspace.
        "approved": src.get("type") != "script",
        "visibility": "private",
        "tags": src.get("tags", []),
        "deleted": False,
        "cloned_from": skillId,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }
    table.put_item(Item=item)

    # Copy S3 files (SKILL.md + scripts/*) via server-side CopyObject.
    s3 = _get_s3()
    src_prefix = f"skills/{skillId}/"
    dst_prefix = f"skills/{new_id}/"
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=ASSETS_BUCKET, Prefix=src_prefix):
            for obj in page.get("Contents", []):
                src_key = obj["Key"]
                rel = src_key[len(src_prefix):]
                if not rel:
                    continue
                s3.copy_object(
                    Bucket=ASSETS_BUCKET,
                    CopySource={"Bucket": ASSETS_BUCKET, "Key": src_key},
                    Key=f"{dst_prefix}{rel}",
                )
    except Exception:
        logger.exception("Failed to copy skill files during clone")
        # DDB record is already written; return success — user can retry file copy if needed.

    return success({"skillId": new_id, "workspace_id": dst_ws_id}, status_code=201)


@router.post("/api/public/tools/<toolId>/clone")
def clone_public_tool(toolId: str):
    user_id, err = _verify_bearer_jwt()
    if err:
        return err

    id_err = validate_id(toolId, "toolId")
    if id_err:
        return bad_request(id_err)

    dst_ws_id, ws_err = _resolve_caller_workspace(user_id)
    if ws_err:
        return ws_err

    table = _get_tools_table()
    src = table.get_item(Key={"toolId": toolId}, ConsistentRead=True).get("Item")
    if not src or src.get("visibility") != "public" or src.get("deleted"):
        return not_found()

    new_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    item = {
        "toolId": new_id,
        "workspace_id": dst_ws_id,
        "name": src.get("name", ""),
        "description": src.get("description", ""),
        "category": src.get("category", ""),
        "code": src.get("code", ""),   # source code exposed here (post-clone only)
        "builtin": False,
        "visibility": "private",
        "deleted": False,
        "cloned_from": toolId,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }
    table.put_item(Item=item)
    return success({"toolId": new_id, "workspace_id": dst_ws_id}, status_code=201)
