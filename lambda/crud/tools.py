"""Tool CRUD endpoints."""
import json
import uuid
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key

from shared.auth import verify_jwt, get_membership, check_permission
from shared.config import TOOLS_TABLE, REGION
from shared.middleware import auth_check
from shared.response import success, paginated, forbidden, not_found, bad_request, version_conflict
from shared.validators import validate_id, parse_pagination

router = Router()
logger = Logger(child=True)

_table = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(TOOLS_TABLE)
    return _table


def _tool_response(item: dict) -> dict:
    return {
        "toolId": item.get("toolId", ""),
        "workspace_id": item.get("workspace_id", ""),
        "name": item.get("name", ""),
        "description": item.get("description", ""),
        "category": item.get("category", ""),
        "code": item.get("code", ""),
        "builtin": item.get("builtin", False),
        "owner": item.get("created_by", ""),
        "visibility": item.get("visibility", "private"),
        "created_by": item.get("created_by", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
        "deleted": item.get("deleted", False),
        "deleted_at": item.get("deleted_at", ""),
    }


@router.get("/api/workspaces/<wsId>/tools")
def list_tools(wsId: str):
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
    items = [_tool_response(i) for i in resp.get("Items", [])]

    last_key = resp.get("LastEvaluatedKey")
    while len(items) < limit and last_key:
        query_kwargs["ExclusiveStartKey"] = last_key
        query_kwargs["Limit"] = limit - len(items)
        resp = table.query(**query_kwargs)
        items.extend([_tool_response(i) for i in resp.get("Items", [])])
        last_key = resp.get("LastEvaluatedKey")

    # Also fetch builtin tools and merge
    builtin_items = []
    builtin_kwargs = {
        "FilterExpression": "builtin = :bt AND (attribute_not_exists(deleted) OR deleted = :f)",
        "ExpressionAttributeValues": {":bt": True, ":f": False},
    }
    builtin_resp = table.scan(**builtin_kwargs)
    builtin_items.extend([_tool_response(i) for i in builtin_resp.get("Items", [])])
    while builtin_resp.get("LastEvaluatedKey"):
        builtin_kwargs["ExclusiveStartKey"] = builtin_resp["LastEvaluatedKey"]
        builtin_resp = table.scan(**builtin_kwargs)
        builtin_items.extend([_tool_response(i) for i in builtin_resp.get("Items", [])])

    seen_ids = {i["toolId"] for i in items}
    for bt in builtin_items:
        if bt["toolId"] not in seen_ids:
            items.insert(0, bt)
            seen_ids.add(bt["toolId"])

    next_cursor = None
    if last_key:
        import base64
        next_cursor = base64.b64encode(json.dumps(last_key).encode()).decode()

    return paginated(items, next_cursor)


@router.get("/api/workspaces/<wsId>/tools/<toolId>")
def get_tool(wsId: str, toolId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    id_err = validate_id(toolId, "toolId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    resp = table.get_item(Key={"toolId": toolId}, ConsistentRead=True)
    item = resp.get("Item")
    if not item or item.get("workspace_id") != ws_id:
        if not item or not item.get("builtin") == True:
            return forbidden()
    if item.get("deleted"):
        return not_found()

    return success(_tool_response(item))


@router.post("/api/workspaces/<wsId>/tools")
def create_tool(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    name = body.get("name", "").strip()
    if not name:
        return bad_request("name is required")
    if len(name) > 200:
        return bad_request("name must be 200 characters or less")

    code = body.get("code", "")
    if len(code.encode("utf-8")) > 350 * 1024:
        return bad_request("code must be less than 350KB")

    tool_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    item = {
        "toolId": tool_id,
        "workspace_id": ws_id,
        "name": name,
        "description": body.get("description", ""),
        "category": body.get("category", ""),
        "code": code,
        "builtin": False,
        "visibility": "private",
        "deleted": False,
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }

    table = _get_table()
    table.put_item(Item=item)

    return success(_tool_response(item), status_code=201)


@router.put("/api/workspaces/<wsId>/tools/<toolId>")
def update_tool(wsId: str, toolId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(toolId, "toolId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    existing = table.get_item(Key={"toolId": toolId}, ConsistentRead=True).get("Item")
    if not existing or existing.get("workspace_id") != ws_id:
        return forbidden()
    if existing.get("deleted"):
        return not_found()

    body = router.current_event.json_body or {}
    now = datetime.utcnow().isoformat() + "Z"
    expected_updated_at = body.get("expected_updated_at")
    if not expected_updated_at:
        return bad_request("expected_updated_at is required for optimistic concurrency control")

    update_parts = ["updated_at = :now"]
    expr_names = {}
    expr_values = {":now": now, ":ws": ws_id, ":expected": expected_updated_at}

    updatable_fields = {"name", "description", "category", "code"}
    for field in updatable_fields:
        if field in body:
            safe_name = f"#f_{field}"
            safe_val = f":v_{field}"
            update_parts.append(f"{safe_name} = {safe_val}")
            expr_names[safe_name] = field
            if field == "code" and len(body[field].encode("utf-8")) > 350 * 1024:
                return bad_request("code must be less than 350KB")
            expr_values[safe_val] = body[field]

    update_expr = "SET " + ", ".join(update_parts)
    condition = "attribute_exists(toolId) AND workspace_id = :ws AND updated_at = :expected"

    try:
        resp = table.update_item(
            Key={"toolId": toolId},
            UpdateExpression=update_expr,
            **({'ExpressionAttributeNames': expr_names} if expr_names else {}),
            ExpressionAttributeValues=expr_values,
            ConditionExpression=condition,
            ReturnValues="ALL_NEW",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return version_conflict("Tool was modified by another request")

    return success(_tool_response(resp.get("Attributes", {})))


@router.delete("/api/workspaces/<wsId>/tools/<toolId>")
def delete_tool(wsId: str, toolId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(toolId, "toolId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    try:
        table.update_item(
            Key={"toolId": toolId},
            UpdateExpression="SET deleted = :t, updated_at = :now, deleted_at = :now",
            ExpressionAttributeValues={
                ":t": True,
                ":now": datetime.utcnow().isoformat() + "Z",
                ":ws": ws_id,
            },
            ConditionExpression="attribute_exists(toolId) AND workspace_id = :ws",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return forbidden()

    return success({"deleted": True})


@router.post("/api/workspaces/<wsId>/tools/<toolId>/publish")
def publish_tool(wsId: str, toolId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(toolId, "toolId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    now = datetime.utcnow().isoformat() + "Z"
    try:
        table.update_item(
            Key={"toolId": toolId},
            UpdateExpression="SET visibility = :pub, updated_at = :now",
            ExpressionAttributeValues={":pub": "public", ":now": now, ":ws": ws_id, ":f": False},
            ConditionExpression="attribute_exists(toolId) AND workspace_id = :ws AND (attribute_not_exists(deleted) OR deleted = :f)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return forbidden()

    return success({"toolId": toolId, "visibility": "public"})


@router.post("/api/workspaces/<wsId>/tools/<toolId>/unpublish")
def unpublish_tool(wsId: str, toolId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(toolId, "toolId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    now = datetime.utcnow().isoformat() + "Z"
    try:
        table.update_item(
            Key={"toolId": toolId},
            UpdateExpression="SET visibility = :priv, updated_at = :now",
            ExpressionAttributeValues={":priv": "private", ":now": now, ":ws": ws_id, ":f": False},
            ConditionExpression="attribute_exists(toolId) AND workspace_id = :ws AND (attribute_not_exists(deleted) OR deleted = :f)",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return forbidden()

    return success({"toolId": toolId, "visibility": "private"})


@router.post("/api/workspaces/<wsId>/tools/<toolId>/restore")
def restore_tool(wsId: str, toolId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(toolId, "toolId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    now = datetime.utcnow().isoformat() + "Z"
    try:
        table.update_item(
            Key={"toolId": toolId},
            UpdateExpression="SET deleted = :f, updated_at = :now REMOVE deleted_at",
            ExpressionAttributeValues={":f": False, ":now": now, ":ws": ws_id},
            ConditionExpression="attribute_exists(toolId) AND workspace_id = :ws",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return forbidden()

    return success({"restored": True})
