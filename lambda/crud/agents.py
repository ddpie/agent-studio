"""Agent CRUD endpoints."""
import json
import uuid
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key

from shared.auth import check_permission, get_membership
from shared.config import AGENTS_TABLE, ASSETS_BUCKET, REGION, WORKSPACES_TABLE
from shared.middleware import auth_check
from shared.response import (
    bad_request,
    forbidden,
    internal_error,
    not_found,
    paginated,
    success,
    version_conflict,
)
from shared.validators import parse_pagination, validate_id, validate_path

router = Router()
logger = Logger(child=True)

_table = None
_ws_table = None
_s3 = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _table


def _get_ws_table():
    global _ws_table
    if _ws_table is None:
        _ws_table = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _ws_table


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _validate_memory_enable(*, memory_enabled: bool, workspace_memory_id: str | None) -> None:
    """Raise ValueError if memory is being enabled on a workspace without a Memory resource."""
    if memory_enabled and not workspace_memory_id:
        raise ValueError(
            "Cannot enable memory: workspace memory resource missing. "
            "Workspace owner must run repair."
        )


_control = None


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


def _sync_harness_memory(agent_id: str, memory_cfg: dict | None, workspace_memory_id: str | None) -> None:
    """Reflect the agent's memory toggle into the harness control plane.

    Zip agents discover their memory config inside the generated main.py
    at invoke time. Harness is declarative — we have to re-push the whole
    memory block via UpdateHarness whenever it changes.

    Args:
        agent_id: harnessId (same as our DDB agentId for harness agents).
        memory_cfg: dict from request body, e.g. {"enabled": True, "strategies": [...]}.
            None / missing key → no memory change; we skip the control-plane call.
        workspace_memory_id: workspace's Memory resource id; required for enable=True.

    Failures are logged but not re-raised — memory drift isn't worth
    failing the whole PUT, since the DDB flag is already the source of
    truth the frontend reads.
    """
    if not isinstance(memory_cfg, dict):
        return
    enabled = bool(memory_cfg.get("enabled"))
    # AWS asymmetry: UpdateHarness can switch memory ON (by passing the
    # wrapped optionalValue tagged union) but has no documented way to
    # switch it OFF — `optionalValue: {}` and `optionalValue: None` both
    # fail ParamValidation, and omitting `memory` leaves the existing
    # config untouched. So "disable" here is intentionally a DDB-only
    # change; the harness server keeps the memory arn until the agent
    # is destroyed. That's fine: without the DDB flag, no caller can
    # read the memory drawer, and no new writes are initiated by the
    # frontend. If we ever need hard-clear semantics, we'd have to
    # delete+recreate the harness.
    if not enabled:
        return
    if not workspace_memory_id:
        return
    try:
        mem_arn = (
            f"arn:aws:bedrock-agentcore:{REGION}:"
            f"{boto3.client('sts').get_caller_identity()['Account']}:"
            f"memory/{workspace_memory_id}"
        )
        _get_control().update_harness(
            harnessId=agent_id,
            memory={
                "optionalValue": {
                    "agentCoreMemoryConfiguration": {"arn": mem_arn},
                },
            },
        )
    except Exception as e:
        logger.warning(
            "harness memory sync failed",
            extra={"agentId": agent_id, "enabled": enabled, "err": str(e)},
        )


# Fields PUT /agents/{id} is allowed to mutate. runtime_type and harness_arn
# are deliberately excluded — they're set exactly once at create time and
# mutating them would break the Meta-Agent's harness lifecycle. Silently
# dropping them (not erroring) keeps the API forgiving for clients that
# send the full agent body unchanged.
ALLOWED_AGENT_FIELDS = {
    "name", "display_name", "description", "model_id", "default_model_id",
    "template_id", "supports_images", "welcome_message", "suggestions",
    "tool_names", "skill_ids", "skills", "mcp_targets", "memory",
}


def _build_agent_item(body: dict, ws_id: str, agent_id: str, user_id: str, now: str) -> dict:
    # Coerce unknown runtime_type values to "zip" (defense in depth — the
    # Meta-Agent's create_harness_agent tool is the canonical writer for
    # "harness" records; anything else through CRUD defaults to zip).
    requested_runtime = body.get("runtime_type", "zip")
    runtime_type = requested_runtime if requested_runtime in ("zip", "harness") else "zip"

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
        "runtime_type": runtime_type,
        "status": "active",
        "visibility": "private",
        "created_by": user_id,
        "created_at": now,
        "updated_at": now,
    }
    # harness_arn is only populated for harness runtimes; typically written by
    # the Meta-Agent's create_harness_agent tool rather than this CRUD path.
    if body.get("harness_arn"):
        item["harness_arn"] = body["harness_arn"]
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
        # Harness agents store system_prompt on the DDB item (no S3 staging
        # file exists for them). Zip agents leave this empty — the frontend
        # falls back to fetching system_prompt.txt from S3 for zip.
        "system_prompt": item.get("system_prompt", ""),
        "status": item.get("status", "active"),
        "visibility": item.get("visibility", "private"),
        "created_by": item.get("created_by", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
        "mcp_targets": item.get("mcp_targets", []),
        "memory": item.get("memory"),
        "runtime_type": item.get("runtime_type", "zip"),
        "harness_arn": item.get("harness_arn", ""),
        # A2A peer agents linked via Meta-Agent's link_agent tool. Written
        # back into DDB by link_agent / unlink_agent so the frontend
        "linked_agents": item.get("linked_agents", []),
        "knowledge_bases": list(item.get("knowledge_bases") or []),
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

    # Validate memory.enabled against workspace memory resource
    memory_cfg = body.get("memory")
    if isinstance(memory_cfg, dict) and memory_cfg.get("enabled"):
        ws_item = _get_ws_table().get_item(
            Key={"workspaceId": ws_id, "sk": "META"}, ConsistentRead=False,
        ).get("Item") or {}
        try:
            _validate_memory_enable(
                memory_enabled=True,
                workspace_memory_id=ws_item.get("memory_id"),
            )
        except ValueError as e:
            return bad_request(str(e))

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

    # Validate memory.enabled against workspace memory resource
    memory_cfg = body.get("memory")
    if isinstance(memory_cfg, dict) and memory_cfg.get("enabled"):
        ws_item = _get_ws_table().get_item(
            Key={"workspaceId": ws_id, "sk": "META"}, ConsistentRead=False,
        ).get("Item") or {}
        try:
            _validate_memory_enable(
                memory_enabled=True,
                workspace_memory_id=ws_item.get("memory_id"),
            )
        except ValueError as e:
            return bad_request(str(e))

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

    # Memory is the only DDB-mutation that also needs a control-plane sync
    # for harness agents. Zip agents pick up memory cfg at invoke time from
    # their generated main.py, so we skip the sync for them.
    updated_item = resp.get("Attributes", {})
    if (
        updated_item.get("runtime_type") == "harness"
        and isinstance(body.get("memory"), dict)
    ):
        ws_item = _get_ws_table().get_item(
            Key={"workspaceId": ws_id, "sk": "META"}, ConsistentRead=False,
        ).get("Item") or {}
        _sync_harness_memory(
            agent_id=agentId,
            memory_cfg=body["memory"],
            workspace_memory_id=ws_item.get("memory_id"),
        )

    return success(_agent_response(updated_item))


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

    # Destination agent must belong to the caller's workspace
    _, dst_err = _check_agent_ownership(agentId, ws_id)
    if dst_err:
        return dst_err

    # Source agent's workspace: caller must be at least viewer in it
    table = _get_table()
    src_agent = table.get_item(Key={"agentId": source_agent_id}, ConsistentRead=True).get("Item")
    if not src_agent:
        return forbidden()
    src_ws_id = src_agent.get("workspace_id")
    if not src_ws_id:
        return forbidden()
    if src_ws_id != ws_id:
        src_member = get_membership(src_ws_id, user_id)
        if not check_permission(src_member, "viewer"):
            logger.warning("copy_skill denied: user=%s src_ws=%s role=%s", user_id, src_ws_id, src_member.get("role") if src_member else None)
            return forbidden()

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
