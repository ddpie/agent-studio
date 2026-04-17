"""Agent CRUD endpoints."""
import json
import uuid
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key

from shared.auth import verify_jwt, get_membership, check_permission
from shared.config import AGENTS_TABLE, REGION, ASSETS_BUCKET
from shared.middleware import auth_check
from shared.response import success, paginated, forbidden, not_found, bad_request, version_conflict, internal_error
from shared.validators import validate_id, validate_path, parse_pagination

router = Router()
logger = Logger(child=True)

_table = None
_s3 = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _table


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


ALLOWED_AGENT_FIELDS = {
    "name", "display_name", "description", "model_id", "default_model_id",
    "template_id", "supports_images", "welcome_message", "suggestions",
    "tool_names", "skill_ids", "skills", "mcp_targets",
}


def _build_agent_item(body: dict, ws_id: str, agent_id: str, user_id: str, now: str) -> dict:
    item = {
        "agentId": agent_id,
        "workspace_id": ws_id,
        "name": body.get("name", ""),
        "display_name": body.get("display_name", ""),
        "description": body.get("description", ""),
        "model_id": body.get("model_id", ""),
        "default_model_id": body.get("default_model_id", ""),
        "template_id": body.get("template_id", ""),
        "supports_images": body.get("supports_images", False),
        "welcome_message": body.get("welcome_message", ""),
        "suggestions": body.get("suggestions", []),
        "tool_names": body.get("tool_names", []),
        "skill_ids": body.get("skill_ids", []),
        "skills": body.get("skills", []),
        "mcp_targets": body.get("mcp_targets", []),
        "status": "active",
        "visibility": "private",
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }
    return item


def _agent_response(item: dict) -> dict:
    return {
        "agentId": item.get("agentId", ""),
        "workspace_id": item.get("workspace_id", ""),
        "name": item.get("name", ""),
        "display_name": item.get("display_name", ""),
        "description": item.get("description", ""),
        "model_id": item.get("model_id", ""),
        "default_model_id": item.get("default_model_id", ""),
        "template_id": item.get("template_id", ""),
        "supports_images": item.get("supports_images", False),
        "welcome_message": item.get("welcome_message", ""),
        "suggestions": item.get("suggestions", []),
        "tool_names": item.get("tool_names", []),
        "skill_ids": item.get("skill_ids", []),
        "skills": item.get("skills", []),
        "status": item.get("status", "active"),
        "visibility": item.get("visibility", "private"),
        "created_by": item.get("created_by", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
        "mcp_targets": item.get("mcp_targets", []),
    }


@router.get("/api/workspaces/<wsId>/agents")
def list_agents(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    limit, cursor = parse_pagination(router.current_event.query_string_parameters or {})
    table = _get_table()

    # Return all statuses (active + archived). Frontend splits them into two
    # sections; archived agents need to be visible for restore/purge actions.
    query_kwargs = {
        "IndexName": "workspace-index",
        "KeyConditionExpression": Key("workspace_id").eq(ws_id),
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if cursor:
        try:
            query_kwargs["ExclusiveStartKey"] = json.loads(
                __import__("base64").b64decode(cursor).decode()
            )
        except Exception:
            return bad_request("Invalid cursor")

    resp = table.query(**query_kwargs)
    items = [_agent_response(i) for i in resp.get("Items", [])]

    last_key = resp.get("LastEvaluatedKey")
    while len(items) < limit and last_key:
        query_kwargs["ExclusiveStartKey"] = last_key
        query_kwargs["Limit"] = limit - len(items)
        resp = table.query(**query_kwargs)
        items.extend([_agent_response(i) for i in resp.get("Items", [])])
        last_key = resp.get("LastEvaluatedKey")

    next_cursor = None
    if last_key:
        import base64
        next_cursor = base64.b64encode(json.dumps(last_key).encode()).decode()

    return paginated(items, next_cursor)


@router.get("/api/workspaces/<wsId>/agents/<agentId>")
def get_agent(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    resp = table.get_item(Key={"agentId": agentId}, ConsistentRead=True)
    item = resp.get("Item")
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()
    if item.get("status") == "archived":
        return forbidden()

    return success(_agent_response(item))


@router.post("/api/workspaces/<wsId>/agents")
def create_agent(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    name = body.get("name", "").strip()
    if not name:
        return bad_request("name is required")
    if len(name) > 200:
        return bad_request("name must be 200 characters or less")

    agent_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    item = _build_agent_item(body, ws_id, agent_id, user_id, now)

    table = _get_table()
    table.put_item(Item=item)

    return success(_agent_response(item), status_code=201)


@router.put("/api/workspaces/<wsId>/agents/<agentId>")
def update_agent(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    existing = table.get_item(Key={"agentId": agentId}, ConsistentRead=True).get("Item")
    if not existing or existing.get("workspace_id") != ws_id:
        return forbidden()
    if existing.get("status") == "archived":
        return forbidden()

    body = router.current_event.json_body or {}
    now = datetime.utcnow().isoformat() + "Z"
    expected_updated_at = body.get("expected_updated_at")

    update_parts = []
    expr_names = {}
    expr_values = {":now": now, ":ws": ws_id}
    if expected_updated_at:
        expr_values[":expected"] = expected_updated_at

    for field in ALLOWED_AGENT_FIELDS:
        if field in body:
            safe_name = f"#f_{field}"
            safe_val = f":v_{field}"
            update_parts.append(f"{safe_name} = {safe_val}")
            expr_names[safe_name] = field
            expr_values[safe_val] = body[field]

    update_parts.append("updated_at = :now")
    update_expr = "SET " + ", ".join(update_parts)

    condition = "attribute_exists(agentId) AND workspace_id = :ws"
    if expected_updated_at:
        condition += " AND updated_at = :expected"

    try:
        resp = table.update_item(
            Key={"agentId": agentId},
            UpdateExpression=update_expr,
            **({'ExpressionAttributeNames': expr_names} if expr_names else {}),
            ExpressionAttributeValues=expr_values,
            ConditionExpression=condition,
            ReturnValues="ALL_NEW",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return version_conflict("Agent was modified by another request")

    return success(_agent_response(resp.get("Attributes", {})))


@router.delete("/api/workspaces/<wsId>/agents/<agentId>")
def delete_agent(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    try:
        table.update_item(
            Key={"agentId": agentId},
            UpdateExpression="SET #st = :archived, updated_at = :now",
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":archived": "archived",
                ":now": datetime.utcnow().isoformat() + "Z",
                ":ws": ws_id,
            },
            ConditionExpression="attribute_exists(agentId) AND workspace_id = :ws",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return forbidden()

    return success({"deleted": True})


@router.post("/api/workspaces/<wsId>/agents/<agentId>/deploy")
def deploy_agent(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    existing = table.get_item(Key={"agentId": agentId}, ConsistentRead=True).get("Item")
    if not existing or existing.get("workspace_id") != ws_id:
        return forbidden()

    skill_ids = existing.get("skill_ids", [])
    if skill_ids:
        from shared.config import SKILLS_TABLE
        skills_table = boto3.resource("dynamodb", region_name=REGION).Table(SKILLS_TABLE)
        unapproved = []
        for sid in skill_ids:
            skill_item = skills_table.get_item(Key={"skillId": sid}, ConsistentRead=True).get("Item")
            if not skill_item or skill_item.get("workspace_id") != ws_id:
                continue  # skip skills not in this workspace
            if not skill_item.get("approved", False):
                unapproved.append(sid)
        if unapproved:
            return bad_request("Cannot deploy: some referenced skills are not approved")

    now = datetime.utcnow().isoformat() + "Z"
    table.update_item(
        Key={"agentId": agentId},
        UpdateExpression="SET updated_at = :now",
        ExpressionAttributeValues={":now": now, ":ws": ws_id},
        ConditionExpression="attribute_exists(agentId) AND workspace_id = :ws",
    )

    logger.info("Deploy triggered", extra={"agentId": agentId, "workspace_id": ws_id})

    return success({"agentId": agentId, "status": "deploying"}, status_code=202)


@router.post("/api/workspaces/<wsId>/agents/<agentId>/publish")
def publish_agent(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    now = datetime.utcnow().isoformat() + "Z"
    try:
        table.update_item(
            Key={"agentId": agentId},
            UpdateExpression="SET visibility = :pub, updated_at = :now",
            ExpressionAttributeValues={":pub": "public", ":now": now, ":ws": ws_id},
            ConditionExpression="attribute_exists(agentId) AND workspace_id = :ws",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return forbidden()

    return success({"agentId": agentId, "visibility": "public"})


@router.post("/api/workspaces/<wsId>/agents/<agentId>/unpublish")
def unpublish_agent(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    now = datetime.utcnow().isoformat() + "Z"
    try:
        table.update_item(
            Key={"agentId": agentId},
            UpdateExpression="SET visibility = :priv, updated_at = :now",
            ExpressionAttributeValues={":priv": "private", ":now": now, ":ws": ws_id},
            ConditionExpression="attribute_exists(agentId) AND workspace_id = :ws",
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return forbidden()

    return success({"agentId": agentId, "visibility": "private"})


@router.get("/api/workspaces/<wsId>/agents/<agentId>/files")
def get_agent_file(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    path = (router.current_event.query_string_parameters or {}).get("path", "")
    allowed_paths = {"system_prompt.txt", "tool_definitions.py", "assistant-history.json", "draft.json", "staging.json"}
    if path not in allowed_paths:
        return bad_request("Invalid file path")

    table = _get_table()
    existing = table.get_item(Key={"agentId": agentId}, ConsistentRead=True).get("Item")
    if not existing or existing.get("workspace_id") != ws_id:
        return forbidden()

    s3_key = f"agents/{agentId}/{path}"
    s3 = _get_s3()
    try:
        obj = s3.get_object(Bucket=ASSETS_BUCKET, Key=s3_key)
        content = obj["Body"].read().decode("utf-8")
    except s3.exceptions.NoSuchKey:
        return not_found()
    except Exception:
        logger.exception("Failed to read S3 file")
        return not_found()

    return success({"path": path, "content": content})


@router.put("/api/workspaces/<wsId>/agents/<agentId>/files")
def put_agent_file(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    path = (router.current_event.query_string_parameters or {}).get("path", "")
    allowed_paths = {"system_prompt.txt", "tool_definitions.py", "assistant-history.json", "draft.json", "staging.json"}
    if path not in allowed_paths:
        return bad_request("Invalid file path")

    table = _get_table()
    existing = table.get_item(Key={"agentId": agentId}, ConsistentRead=True).get("Item")
    if not existing or existing.get("workspace_id") != ws_id:
        return forbidden()

    body = router.current_event.json_body or {}
    content = body.get("content", "")
    if not content:
        return bad_request("content is required")

    s3_key = f"agents/{agentId}/{path}"
    s3 = _get_s3()
    s3.put_object(
        Bucket=ASSETS_BUCKET,
        Key=s3_key,
        Body=content.encode("utf-8"),
        ContentType="text/plain" if path.endswith(".txt") else "application/json" if path.endswith(".json") else "text/x-python",
    )

    now = datetime.utcnow().isoformat() + "Z"
    table.update_item(
        Key={"agentId": agentId},
        UpdateExpression="SET updated_at = :now",
        ExpressionAttributeValues={":now": now, ":ws": ws_id},
        ConditionExpression="attribute_exists(agentId) AND workspace_id = :ws",
    )

    return success({"path": path, "updated_at": now})


# ── Agent Skill Files ──


def _check_agent_ownership(agent_id: str, ws_id: str):
    """Return agent item if valid, or (None, error_response)."""
    table = _get_table()
    agent = table.get_item(Key={"agentId": agent_id}, ConsistentRead=True).get("Item")
    if not agent or agent.get("workspace_id") != ws_id:
        return None, forbidden()
    if agent.get("status") == "archived":
        return None, forbidden()
    return agent, None


@router.get("/api/workspaces/<wsId>/agents/<agentId>/skills/<skillId>/files")
def get_agent_skill_file(wsId: str, agentId: str, skillId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    for name, val in [("agentId", agentId), ("skillId", skillId)]:
        id_err = validate_id(val, name)
        if id_err:
            return bad_request(id_err)

    agent, agent_err = _check_agent_ownership(agentId, ws_id)
    if agent_err:
        return agent_err

    path = (router.current_event.query_string_parameters or {}).get("path", "")
    prefix = f"agents/{agentId}/skills/{skillId}/"

    if not path:
        # List files, or bulk read all contents with ?bulk=true
        s3 = _get_s3()
        bulk = (router.current_event.query_string_parameters or {}).get("bulk", "")
        keys = []
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=ASSETS_BUCKET, Prefix=prefix, MaxKeys=1000):
                for obj in page.get("Contents", []):
                    rel = obj["Key"][len(prefix):]
                    if rel and not rel.startswith("."):
                        keys.append(rel)
        except Exception:
            logger.exception("Failed to list agent skill files")

        if bulk:
            # Return all file contents in one response
            files = {}
            for key in keys:
                try:
                    obj = s3.get_object(Bucket=ASSETS_BUCKET, Key=f"{prefix}{key}")
                    files[key] = obj["Body"].read().decode("utf-8")
                except Exception:
                    pass  # skip unreadable files
            return success({"files": files})

        return success({"files": keys})

    path_err = validate_path(path)
    if path_err:
        return bad_request(path_err)

    s3 = _get_s3()
    try:
        obj = s3.get_object(Bucket=ASSETS_BUCKET, Key=f"{prefix}{path}")
        content = obj["Body"].read().decode("utf-8")
    except Exception:
        logger.exception("Failed to read agent skill file")
        return not_found()
    return success({"path": path, "content": content})


@router.post("/api/workspaces/<wsId>/agents/<agentId>/skills/<skillId>/copy-from")
def copy_skill_files_from(wsId: str, agentId: str, skillId: str):
    """Copy all skill files from a source agent's skill to this agent's skill via S3 CopyObject."""
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    for name, val in [("agentId", agentId), ("skillId", skillId)]:
        id_err = validate_id(val, name)
        if id_err:
            return bad_request(id_err)

    body = router.current_event.json_body or {}
    source_agent_id = body.get("sourceAgentId", "")
    source_skill_id = body.get("sourceSkillId", "")
    if not source_agent_id or not source_skill_id:
        return bad_request("sourceAgentId and sourceSkillId are required")
    for name, val in [("sourceAgentId", source_agent_id), ("sourceSkillId", source_skill_id)]:
        id_err = validate_id(val, name)
        if id_err:
            return bad_request(id_err)

    s3 = _get_s3()
    src_prefix = f"agents/{source_agent_id}/skills/{source_skill_id}/"
    dst_prefix = f"agents/{agentId}/skills/{skillId}/"
    copied = 0
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
                copied += 1
    except Exception:
        logger.exception("S3 copy failed for skill files")
        return internal_error()

    return success({"copied": copied})


@router.put("/api/workspaces/<wsId>/agents/<agentId>/skills/<skillId>/files")
def put_agent_skill_file(wsId: str, agentId: str, skillId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    for name, val in [("agentId", agentId), ("skillId", skillId)]:
        id_err = validate_id(val, name)
        if id_err:
            return bad_request(id_err)

    agent, agent_err = _check_agent_ownership(agentId, ws_id)
    if agent_err:
        return agent_err

    path = (router.current_event.query_string_parameters or {}).get("path", "")
    path_err = validate_path(path)
    if path_err:
        return bad_request(path_err)

    body = router.current_event.json_body or {}
    content = body.get("content", "")

    ct = "text/markdown" if path.endswith(".md") else "text/x-python" if path.endswith(".py") else "text/plain"
    s3 = _get_s3()
    try:
        s3.put_object(
            Bucket=ASSETS_BUCKET,
            Key=f"agents/{agentId}/skills/{skillId}/{path}",
            Body=content.encode("utf-8"),
            ContentType=ct,
        )
    except Exception:
        logger.exception("Failed to write agent skill file")
        return internal_error("Failed to write file")
    return success({"path": path})


@router.delete("/api/workspaces/<wsId>/agents/<agentId>/skills/<skillId>/files")
def delete_agent_skill_files(wsId: str, agentId: str, skillId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    for name, val in [("agentId", agentId), ("skillId", skillId)]:
        id_err = validate_id(val, name)
        if id_err:
            return bad_request(id_err)

    agent, agent_err = _check_agent_ownership(agentId, ws_id)
    if agent_err:
        return agent_err

    path = (router.current_event.query_string_parameters or {}).get("path", "")
    s3 = _get_s3()
    prefix = f"agents/{agentId}/skills/{skillId}/"

    if path:
        path_err = validate_path(path)
        if path_err:
            return bad_request(path_err)
        try:
            s3.delete_object(Bucket=ASSETS_BUCKET, Key=f"{prefix}{path}")
        except Exception:
            logger.exception("Failed to delete agent skill file")
            return internal_error("Failed to delete file")
    else:
        # Delete all files for this skill
        try:
            paginator = s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=ASSETS_BUCKET, Prefix=prefix):
                for obj in page.get("Contents", []):
                    s3.delete_object(Bucket=ASSETS_BUCKET, Key=obj["Key"])
        except Exception:
            logger.exception("Failed to delete agent skill files")
            return internal_error("Failed to delete files")
    return success({"deleted": True})
