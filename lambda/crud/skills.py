"""Skill CRUD endpoints."""
import json
import uuid
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key

from shared.auth import verify_jwt, get_membership, check_permission
from shared.config import SKILLS_TABLE, REGION, ASSETS_BUCKET
from shared.middleware import auth_check
from shared.response import success, paginated, forbidden, not_found, bad_request, version_conflict, internal_error
from shared.validators import validate_id, parse_pagination

router = Router()
logger = Logger(child=True)

_table = None
_s3 = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(SKILLS_TABLE)
    return _table


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _skill_response(item: dict) -> dict:
    return {
        "skillId": item.get("skillId", ""),
        "workspace_id": item.get("workspace_id", ""),
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "type": item.get("type", "prompt"),
        "approved": item.get("approved", False),
        "visibility": item.get("visibility", "private"),
        "tags": item.get("tags", []),
        "created_by": item.get("created_by", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


@router.get("/api/workspaces/<wsId>/skills")
def list_skills(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    limit, cursor = parse_pagination(router.current_event.query_string_parameters or {})
    table = _get_table()

    query_kwargs = {
        "IndexName": "workspace-index",
        "KeyConditionExpression": Key("workspace_id").eq(ws_id),
        "ScanIndexForward": False,
        "Limit": limit,
        "FilterExpression": "attribute_not_exists(deleted) OR deleted = :f",
        "ExpressionAttributeValues": {":f": False},
    }
    if cursor:
        import base64
        try:
            query_kwargs["ExclusiveStartKey"] = json.loads(base64.b64decode(cursor).decode())
        except Exception:
            return bad_request("Invalid cursor")

    resp = table.query(**query_kwargs)
    items = [_skill_response(i) for i in resp.get("Items", [])]

    last_key = resp.get("LastEvaluatedKey")
    while len(items) < limit and last_key:
        query_kwargs["ExclusiveStartKey"] = last_key
        query_kwargs["Limit"] = limit - len(items)
        resp = table.query(**query_kwargs)
        items.extend([_skill_response(i) for i in resp.get("Items", [])])
        last_key = resp.get("LastEvaluatedKey")

    next_cursor = None
    if last_key:
        import base64
        next_cursor = base64.b64encode(json.dumps(last_key).encode()).decode()

    return paginated(items, next_cursor)


@router.get("/api/workspaces/<wsId>/skills/<skillId>")
def get_skill(wsId: str, skillId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    id_err = validate_id(skillId, "skillId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    resp = table.get_item(Key={"skillId": skillId}, ConsistentRead=True)
    item = resp.get("Item")
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()
    if item.get("deleted"):
        return forbidden()
    result = _skill_response(item)
    try:
        s3 = _get_s3()
        obj = s3.get_object(Bucket=ASSETS_BUCKET, Key=f"skills/{skillId}/SKILL.md")
        result["content"] = obj["Body"].read().decode("utf-8")
    except Exception:
        result["content"] = ""

    return success(result)


@router.post("/api/workspaces/<wsId>/skills")
def create_skill(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    name = body.get("name", "").strip()
    if not name:
        return bad_request("name is required")
    if len(name) > 200:
        return bad_request("name must be 200 characters or less")

    skill_type = body.get("type", "prompt")
    if skill_type not in ("prompt", "script"):
        return bad_request("type must be 'prompt' or 'script'")

    skill_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    item = {
        "skillId": skill_id,
        "workspace_id": ws_id,
        "name": name,
        "description": body.get("description", ""),
        "type": skill_type,
        "approved": skill_type != "script",
        "visibility": "private",
        "tags": body.get("tags", []),
        "deleted": False,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }

    table = _get_table()
    table.put_item(Item=item)

    content = body.get("content", "")
    if content:
        s3 = _get_s3()
        s3.put_object(
            Bucket=ASSETS_BUCKET,
            Key=f"skills/{skill_id}/SKILL.md",
            Body=content.encode("utf-8"),
            ContentType="text/markdown",
        )

    return success(_skill_response(item), status_code=201)


@router.put("/api/workspaces/<wsId>/skills/<skillId>")
def update_skill(wsId: str, skillId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(skillId, "skillId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    existing = table.get_item(Key={"skillId": skillId}, ConsistentRead=True).get("Item")
    if not existing or existing.get("workspace_id") != ws_id:
        return forbidden()
    if existing.get("deleted"):
        return forbidden()

    body = router.current_event.json_body or {}
    now = datetime.utcnow().isoformat() + "Z"
    expected_updated_at = body.get("expected_updated_at")
    if not expected_updated_at:
        return bad_request("expected_updated_at is required for optimistic concurrency control")

    update_parts = ["updated_at = :now"]
    expr_names = {}
    expr_values = {":now": now, ":ws": ws_id, ":expected": expected_updated_at}

    updatable_fields = {"name", "description", "tags"}
    for field in updatable_fields:
        if field in body:
            safe_name = f"#f_{field}"
            safe_val = f":v_{field}"
            update_parts.append(f"{safe_name} = {safe_val}")
            expr_names[safe_name] = field
            expr_values[safe_val] = body[field]

    if existing.get("type") == "script" and body.get("content"):
        update_parts.append("approved = :false")
        expr_values[":false"] = False

    update_expr = "SET " + ", ".join(update_parts)
    condition = "attribute_exists(skillId) AND workspace_id = :ws AND updated_at = :expected"

    try:
        resp = table.update_item(
            Key={"skillId": skillId},
            UpdateExpression=update_expr,
            **({'ExpressionAttributeNames': expr_names} if expr_names else {}),
            ExpressionAttributeValues=expr_values,
            ConditionExpression=condition,
            ReturnValues="ALL_NEW",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return version_conflict("Skill was modified by another request")

    content = body.get("content")
    if content:
        s3 = _get_s3()
        s3.put_object(
            Bucket=ASSETS_BUCKET,
            Key=f"skills/{skillId}/SKILL.md",
            Body=content.encode("utf-8"),
            ContentType="text/markdown",
        )

    return success(_skill_response(resp.get("Attributes", {})))


@router.delete("/api/workspaces/<wsId>/skills/<skillId>")
def delete_skill(wsId: str, skillId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(skillId, "skillId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    try:
        table.update_item(
            Key={"skillId": skillId},
            UpdateExpression="SET deleted = :t, updated_at = :now, deleted_at = :now",
            ExpressionAttributeValues={
                ":t": True,
                ":now": datetime.utcnow().isoformat() + "Z",
                ":ws": ws_id,
            },
            ConditionExpression="attribute_exists(skillId) AND workspace_id = :ws",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return forbidden()

    return success({"deleted": True})


@router.post("/api/workspaces/<wsId>/skills/import")
def import_skill(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    content = body.get("content", "").strip()
    if not content:
        return bad_request("content (SKILL.md) is required")

    name = body.get("name", "")
    description = body.get("description", "")

    if not name and content.startswith("---"):
        try:
            import re
            fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
            if fm_match:
                fm_text = fm_match.group(1)
                for line in fm_text.split("\n"):
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip("\"'")
                    elif line.startswith("description:"):
                        description = line.split(":", 1)[1].strip().strip("\"'")
        except Exception:
            pass

    if not name:
        return bad_request("name is required (in body or SKILL.md frontmatter)")

    skill_type = body.get("type", "prompt")
    if skill_type not in ("prompt", "script"):
        return bad_request("type must be 'prompt' or 'script'")

    skill_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    item = {
        "skillId": skill_id,
        "workspace_id": ws_id,
        "name": name,
        "description": description,
        "type": skill_type,
        "approved": skill_type != "script",
        "visibility": "private",
        "tags": body.get("tags", []),
        "deleted": False,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }

    # Validate all script names BEFORE any writes
    scripts = body.get("scripts", {})
    import re as _re
    for script_name in scripts:
        if not _re.match(r"^[a-zA-Z0-9._-]+$", script_name):
            return bad_request(f"Invalid script name: must match [a-zA-Z0-9._-]+")

    # Now safe to write
    table = _get_table()
    table.put_item(Item=item)

    s3 = _get_s3()
    s3.put_object(
        Bucket=ASSETS_BUCKET,
        Key=f"skills/{skill_id}/SKILL.md",
        Body=content.encode("utf-8"),
        ContentType="text/markdown",
    )

    for script_name, script_content in scripts.items():
        s3.put_object(
            Bucket=ASSETS_BUCKET,
            Key=f"skills/{skill_id}/scripts/{script_name}",
            Body=script_content.encode("utf-8"),
            ContentType="text/plain",
        )

    return success(_skill_response(item), status_code=201)


@router.post("/api/workspaces/<wsId>/skills/<skillId>/approve")
def approve_skill(wsId: str, skillId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(skillId, "skillId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    existing = table.get_item(Key={"skillId": skillId}, ConsistentRead=True).get("Item")
    if not existing or existing.get("workspace_id") != ws_id:
        return forbidden()
    if existing.get("deleted"):
        return forbidden()
    if existing.get("type") != "script":
        return bad_request("Only script skills require approval")
    if existing.get("approved"):
        return bad_request("Skill is already approved")

    now = datetime.utcnow().isoformat() + "Z"
    table.update_item(
        Key={"skillId": skillId},
        UpdateExpression="SET approved = :t, approved_by = :uid, approved_at = :now, updated_at = :now",
        ExpressionAttributeValues={
            ":t": True,
            ":uid": user_id,
            ":now": now,
            ":ws": ws_id,
        },
        ConditionExpression="attribute_exists(skillId) AND workspace_id = :ws",
    )

    return success({
        "skillId": skillId,
        "approved": True,
        "approved_by": user_id,
        "approved_at": now,
    })
