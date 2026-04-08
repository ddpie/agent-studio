"""Upload presigned URL generation and public listing endpoints."""
import json
import uuid
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key

from shared.auth import verify_jwt, get_membership, check_permission
from shared.config import REGION, ASSETS_BUCKET, AGENTS_TABLE, SKILLS_TABLE
from shared.middleware import auth_check
from shared.response import success, paginated, forbidden, bad_request
from shared.validators import validate_id, validate_path, parse_pagination

router = Router()
logger = Logger(child=True)

_s3 = None
_agents_table = None
_skills_table = None


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
    if not re.match(r"^[a-zA-Z0-9._-]+$", filename):
        return bad_request("Invalid filename: must match [a-zA-Z0-9._-]+")

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
    if not re.match(r"^[a-zA-Z0-9-]+$", session_id):
        return bad_request("Invalid sessionId: must match [a-zA-Z0-9-]+")
    if not re.match(r"^[a-zA-Z0-9._-]+$", filename):
        return bad_request("Invalid filename: must match [a-zA-Z0-9._-]+")

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
    s3.put_object(Bucket=ASSETS_BUCKET, Key=s3_key, Body=content.encode("utf-8"), ContentType="application/json")
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
    s3.delete_object(Bucket=ASSETS_BUCKET, Key=s3_key)
    return success({"deleted": True})


@router.get("/api/public/agents")
def list_public_agents():
    auth_header = router.current_event.get_header_value("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return forbidden()
    try:
        verify_jwt(auth_header[7:])
    except Exception:
        return forbidden()

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
    result["headers"]["Cache-Control"] = "public, max-age=30"
    return result


@router.get("/api/public/skills")
def list_public_skills():
    auth_header = router.current_event.get_header_value("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        return forbidden()
    try:
        verify_jwt(auth_header[7:])
    except Exception:
        return forbidden()

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
    result["headers"]["Cache-Control"] = "public, max-age=30"
    return result
