"""Channel Integration CRUD endpoints.

Manages channel configurations (Feishu, DingTalk, Slack) that connect
deployed Agents to external messaging platforms. Each channel stores
platform credentials in Secrets Manager and configuration in DynamoDB.
"""
import json
import time
from uuid import uuid4

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError

from shared.config import AGENTS_TABLE, CHANNEL_HISTORY_TABLE, CHANNELS_TABLE, REGION
from shared.middleware import auth_check
from shared.response import bad_request, internal_error, not_found, success
from shared.validators import validate_id

router = Router()
logger = Logger(child=True)

_channels_table = None
_history_table = None
_agents_table = None
_secrets = None

VALID_CHANNEL_TYPES = ("feishu", "dingtalk", "slack")
VALID_TRIGGER_MODES = ("mention", "all", "keyword")
MAX_CHANNEL_NAME_LEN = 64
MAX_HISTORY_TURNS = 50
DEFAULT_HISTORY_TURNS = 10


def _get_channels_table():
    global _channels_table
    if _channels_table is None:
        _channels_table = boto3.resource("dynamodb", region_name=REGION).Table(CHANNELS_TABLE)
    return _channels_table


def _get_history_table():
    global _history_table
    if _history_table is None:
        _history_table = boto3.resource("dynamodb", region_name=REGION).Table(CHANNEL_HISTORY_TABLE)
    return _history_table


def _get_agents_table():
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _agents_table


def _get_secrets():
    global _secrets
    if _secrets is None:
        _secrets = boto3.client("secretsmanager", region_name=REGION)
    return _secrets


def _generate_channel_id() -> str:
    """Generate a channel ID: ch_ + 20 hex chars from uuid4."""
    return "ch_" + uuid4().hex[:20]


def _secret_name(channel_id: str) -> str:
    return f"agent-studio/channels/{channel_id}"


def _validate_agent_in_workspace(agent_id: str, workspace_id: str) -> bool:
    """Verify agent exists and belongs to the workspace."""
    try:
        resp = _get_agents_table().get_item(
            Key={"agentId": agent_id},
            ProjectionExpression="agentId, workspace_id",
        )
        item = resp.get("Item")
        return item is not None and item.get("workspace_id") == workspace_id
    except ClientError:
        return False


def _channel_response(item: dict) -> dict:
    """Shape a DDB channel item for API response (strip secrets)."""
    return {
        "channelId": item.get("sk", ""),
        "workspaceId": item.get("workspaceId", ""),
        "channelType": item.get("channelType", ""),
        "channelName": item.get("channelName", ""),
        "defaultAgentId": item.get("defaultAgentId", ""),
        "routingMode": item.get("routingMode", "single"),
        "routingRules": item.get("routingRules", []),
        "platformConfig": item.get("platformConfig", {}),
        "triggerMode": item.get("triggerMode", "mention"),
        "maxHistoryTurns": item.get("maxHistoryTurns", DEFAULT_HISTORY_TURNS),
        "language": item.get("language", "zh"),
        "status": item.get("status", "provisioning"),
        "configVersion": item.get("configVersion", 1),
        "lastMessageAt": item.get("lastMessageAt"),
        "messageCount": item.get("messageCount", 0),
        "lastError": item.get("lastError"),
        "errorCount": item.get("errorCount", 0),
        "createdAt": item.get("createdAt", ""),
        "updatedAt": item.get("updatedAt", ""),
        "createdBy": item.get("createdBy", ""),
    }


# ---------------------------------------------------------------------------
# POST /api/workspaces/<wsId>/channels — create channel
# ---------------------------------------------------------------------------


@router.post("/api/workspaces/<wsId>/channels")
def create_channel(wsId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}

    # Required fields
    channel_type = (body.get("channelType") or "").strip()
    channel_name = (body.get("channelName") or "").strip()
    default_agent_id = (body.get("defaultAgentId") or "").strip()
    platform_config = body.get("platformConfig") or {}
    app_secret = (body.get("appSecret") or "").strip()

    # Optional fields
    trigger_mode = (body.get("triggerMode") or "at_bot").strip()
    max_history_turns = body.get("maxHistoryTurns", DEFAULT_HISTORY_TURNS)
    language = (body.get("language") or "zh").strip()

    # --- Validation ---
    if not channel_type:
        return bad_request("channelType is required")
    if channel_type not in VALID_CHANNEL_TYPES:
        return bad_request(f"channelType must be one of: {', '.join(VALID_CHANNEL_TYPES)}")

    if not channel_name:
        return bad_request("channelName is required")
    if len(channel_name) > MAX_CHANNEL_NAME_LEN:
        return bad_request(f"channelName must be {MAX_CHANNEL_NAME_LEN} characters or less")

    if not default_agent_id:
        return bad_request("defaultAgentId is required")
    id_err = validate_id(default_agent_id, "defaultAgentId")
    if id_err:
        return bad_request(id_err)
    if not _validate_agent_in_workspace(default_agent_id, ws_id):
        return bad_request("defaultAgentId does not exist in this workspace")

    if not isinstance(platform_config, dict):
        return bad_request("platformConfig must be an object")
    app_id = (platform_config.get("appId") or "").strip()
    if not app_id:
        return bad_request("platformConfig.appId is required")

    if not app_secret:
        return bad_request("appSecret is required")

    if trigger_mode not in VALID_TRIGGER_MODES:
        return bad_request(f"triggerMode must be one of: {', '.join(VALID_TRIGGER_MODES)}")

    if not isinstance(max_history_turns, int) or max_history_turns < 0 or max_history_turns > MAX_HISTORY_TURNS:
        return bad_request(f"maxHistoryTurns must be an integer between 0 and {MAX_HISTORY_TURNS}")

    if language not in ("zh", "en"):
        return bad_request("language must be 'zh' or 'en'")

    # --- Generate ID + store secret ---
    channel_id = _generate_channel_id()
    now = int(time.time())

    try:
        _get_secrets().create_secret(
            Name=_secret_name(channel_id),
            SecretString=json.dumps({"appSecret": app_secret}),
            Description=f"Channel credentials for {channel_id} in workspace {ws_id}",
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("create channel secret failed", extra={"channelId": channel_id, "code": code})
        return internal_error("Failed to store channel credentials")

    # --- Write DDB record ---
    item = {
        "workspaceId": ws_id,
        "sk": channel_id,
        "channelType": channel_type,
        "channelName": channel_name,
        "defaultAgentId": default_agent_id,
        "platformConfig": platform_config,
        "triggerMode": trigger_mode,
        "maxHistoryTurns": max_history_turns,
        "language": language,
        "status": "provisioning",
        "configVersion": 1,
        "createdAt": now,
        "updatedAt": now,
        "createdBy": user_id,
    }

    try:
        _get_channels_table().put_item(Item=item)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("create channel DDB failed", extra={"channelId": channel_id, "code": code})
        # Best-effort cleanup: delete the secret we just created
        try:
            _get_secrets().delete_secret(
                SecretId=_secret_name(channel_id),
                ForceDeleteWithoutRecovery=True,
            )
        except ClientError:
            pass
        return internal_error("Failed to create channel record")

    return success(_channel_response(item), status_code=201)


# ---------------------------------------------------------------------------
# GET /api/workspaces/<wsId>/channels — list channels
# ---------------------------------------------------------------------------


@router.get("/api/workspaces/<wsId>/channels")
def list_channels(wsId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    try:
        resp = _get_channels_table().query(
            KeyConditionExpression="workspaceId = :wsId AND begins_with(sk, :prefix)",
            ExpressionAttributeValues={
                ":wsId": ws_id,
                ":prefix": "ch_",
            },
        )
        items = resp.get("Items", [])
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("list channels failed", extra={"wsId": ws_id, "code": code})
        return internal_error()

    # Filter out group metadata records (SK contains "#group#")
    channels = [
        _channel_response(item) for item in items
        if "#group#" not in item.get("sk", "")
    ]

    return success({"channels": channels})


# ---------------------------------------------------------------------------
# PUT /api/workspaces/<wsId>/channels/<chId> — update channel
# ---------------------------------------------------------------------------


@router.put("/api/workspaces/<wsId>/channels/<chId>")
def update_channel(wsId: str, chId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(chId, "channelId")
    if id_err:
        return bad_request(id_err)

    body = router.current_event.json_body or {}
    if not body:
        return bad_request("request body is required")

    # Fetch existing record
    try:
        resp = _get_channels_table().get_item(
            Key={"workspaceId": ws_id, "sk": chId},
        )
        existing = resp.get("Item")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("update channel get failed", extra={"channelId": chId, "code": code})
        return internal_error()

    if not existing:
        return not_found("Channel not found")

    # Build update expression
    update_expr_parts = []
    expr_attr_values = {}
    expr_attr_names = {}

    # channelName
    if "channelName" in body:
        name = (body["channelName"] or "").strip()
        if not name:
            return bad_request("channelName cannot be empty")
        if len(name) > MAX_CHANNEL_NAME_LEN:
            return bad_request(f"channelName must be {MAX_CHANNEL_NAME_LEN} characters or less")
        update_expr_parts.append("#cn = :cn")
        expr_attr_names["#cn"] = "channelName"
        expr_attr_values[":cn"] = name

    # defaultAgentId
    if "defaultAgentId" in body:
        agent_id = (body["defaultAgentId"] or "").strip()
        if not agent_id:
            return bad_request("defaultAgentId cannot be empty")
        aid_err = validate_id(agent_id, "defaultAgentId")
        if aid_err:
            return bad_request(aid_err)
        if not _validate_agent_in_workspace(agent_id, ws_id):
            return bad_request("defaultAgentId does not exist in this workspace")
        update_expr_parts.append("defaultAgentId = :aid")
        expr_attr_values[":aid"] = agent_id

    # platformConfig
    if "platformConfig" in body:
        pc = body["platformConfig"]
        if not isinstance(pc, dict):
            return bad_request("platformConfig must be an object")
        update_expr_parts.append("platformConfig = :pc")
        expr_attr_values[":pc"] = pc

    # triggerMode
    if "triggerMode" in body:
        tm = (body["triggerMode"] or "").strip()
        if tm not in VALID_TRIGGER_MODES:
            return bad_request(f"triggerMode must be one of: {', '.join(VALID_TRIGGER_MODES)}")
        update_expr_parts.append("triggerMode = :tm")
        expr_attr_values[":tm"] = tm

    # maxHistoryTurns
    if "maxHistoryTurns" in body:
        mht = body["maxHistoryTurns"]
        if not isinstance(mht, int) or mht < 0 or mht > MAX_HISTORY_TURNS:
            return bad_request(f"maxHistoryTurns must be an integer between 0 and {MAX_HISTORY_TURNS}")
        update_expr_parts.append("maxHistoryTurns = :mht")
        expr_attr_values[":mht"] = mht

    # routingRules
    if "routingRules" in body:
        rules = body["routingRules"]
        if not isinstance(rules, list):
            return bad_request("routingRules must be a list")
        update_expr_parts.append("routingRules = :rr")
        expr_attr_values[":rr"] = rules

    # routingMode
    if "routingMode" in body:
        rm = (body["routingMode"] or "").strip()
        if rm not in ("single", "per-group", "command"):
            return bad_request("routingMode must be one of: single, per-group, command")
        update_expr_parts.append("routingMode = :rm")
        expr_attr_values[":rm"] = rm

    # language
    if "language" in body:
        lang = (body["language"] or "").strip()
        if lang not in ("zh", "en"):
            return bad_request("language must be 'zh' or 'en'")
        update_expr_parts.append("#lang = :lang")
        expr_attr_names["#lang"] = "language"
        expr_attr_values[":lang"] = lang

    # status
    if "status" in body:
        status = (body["status"] or "").strip()
        if status not in ("provisioning", "active", "paused", "error"):
            return bad_request("status must be one of: provisioning, active, paused, error")
        update_expr_parts.append("#st = :st")
        expr_attr_names["#st"] = "status"
        expr_attr_values[":st"] = status

    # appSecret — update in Secrets Manager, not DDB
    if "appSecret" in body:
        app_secret = (body["appSecret"] or "").strip()
        if not app_secret:
            return bad_request("appSecret cannot be empty")
        try:
            _get_secrets().put_secret_value(
                SecretId=_secret_name(chId),
                SecretString=json.dumps({"appSecret": app_secret}),
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code == "ResourceNotFoundException":
                # Secret was deleted; recreate
                try:
                    _get_secrets().create_secret(
                        Name=_secret_name(chId),
                        SecretString=json.dumps({"appSecret": app_secret}),
                        Description=f"Channel credentials for {chId} in workspace {ws_id}",
                    )
                except ClientError:
                    logger.exception("recreate channel secret failed", extra={"channelId": chId})
                    return internal_error("Failed to update channel credentials")
            else:
                logger.exception("update channel secret failed", extra={"channelId": chId, "code": code})
                return internal_error("Failed to update channel credentials")

    if not update_expr_parts:
        # Only appSecret was updated (or nothing)
        if "appSecret" not in body:
            return bad_request("no updateable fields provided")
        # Return current record
        return success(_channel_response(existing))

    # Always increment configVersion and updatedAt
    update_expr_parts.append("configVersion = configVersion + :one")
    expr_attr_values[":one"] = 1
    update_expr_parts.append("updatedAt = :ua")
    expr_attr_values[":ua"] = int(time.time())

    update_expr = "SET " + ", ".join(update_expr_parts)

    try:
        update_kwargs = {
            "Key": {"workspaceId": ws_id, "sk": chId},
            "UpdateExpression": update_expr,
            "ExpressionAttributeValues": expr_attr_values,
            "ReturnValues": "ALL_NEW",
        }
        if expr_attr_names:
            update_kwargs["ExpressionAttributeNames"] = expr_attr_names
        resp = _get_channels_table().update_item(**update_kwargs)
        updated = resp.get("Attributes", {})
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("update channel DDB failed", extra={"channelId": chId, "code": code})
        return internal_error()

    return success(_channel_response(updated))


# ---------------------------------------------------------------------------
# DELETE /api/workspaces/<wsId>/channels/<chId> — delete channel
# ---------------------------------------------------------------------------


@router.delete("/api/workspaces/<wsId>/channels/<chId>")
def delete_channel(wsId: str, chId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(chId, "channelId")
    if id_err:
        return bad_request(id_err)

    # Verify record exists
    try:
        resp = _get_channels_table().get_item(
            Key={"workspaceId": ws_id, "sk": chId},
        )
        existing = resp.get("Item")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("delete channel get failed", extra={"channelId": chId, "code": code})
        return internal_error()

    if not existing:
        return not_found("Channel not found")

    # Delete Secrets Manager secret
    try:
        _get_secrets().delete_secret(
            SecretId=_secret_name(chId),
            ForceDeleteWithoutRecovery=True,
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code != "ResourceNotFoundException":
            logger.exception("delete channel secret failed", extra={"channelId": chId, "code": code})
            return internal_error("Failed to delete channel credentials")

    # Delete DDB record
    try:
        _get_channels_table().delete_item(
            Key={"workspaceId": ws_id, "sk": chId},
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("delete channel DDB failed", extra={"channelId": chId, "code": code})
        return internal_error()

    # TODO: Stop ECS relay task for this channel (placeholder)
    logger.info("channel deleted — ECS task stop not yet implemented", extra={"channelId": chId})

    return success({"deleted": chId})


# ---------------------------------------------------------------------------
# POST /api/workspaces/<wsId>/channels/<chId>/test — test connection
# ---------------------------------------------------------------------------


@router.post("/api/workspaces/<wsId>/channels/<chId>/test")
def test_channel(wsId: str, chId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(chId, "channelId")
    if id_err:
        return bad_request(id_err)

    # Placeholder for V1 — actual connection test not yet implemented
    return success({"status": "ok", "message": "Connection test not yet implemented"})


# ---------------------------------------------------------------------------
# GET /api/workspaces/<wsId>/channels/<chId>/messages — recent messages
# ---------------------------------------------------------------------------


@router.get("/api/workspaces/<wsId>/channels/<chId>/messages")
def list_channel_messages(wsId: str, chId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    id_err = validate_id(chId, "channelId")
    if id_err:
        return bad_request(id_err)

    # Verify channel belongs to workspace
    try:
        resp = _get_channels_table().get_item(
            Key={"workspaceId": ws_id, "sk": chId},
            ProjectionExpression="channelId",
        )
        if not resp.get("Item"):
            return not_found("Channel not found")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("messages channel check failed", extra={"channelId": chId, "code": code})
        return internal_error()

    # Query history table: limited scan with filter for this channel's messages.
    # History table PK starts with channelId, so we filter by begins_with(pk, channelId).
    try:
        from boto3.dynamodb.conditions import Attr
        resp = _get_history_table().scan(
            FilterExpression=Attr("pk").begins_with(chId),
            Limit=50,
            ProjectionExpression="pk, sk, #r, content, userName, #ts",
            ExpressionAttributeNames={"#r": "role", "#ts": "timestamp"},
        )
        items = resp.get("Items", [])
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        logger.exception("list channel messages failed", extra={"channelId": chId, "code": code})
        return internal_error()

    # Sort by timestamp descending (sk is typically a timestamp-based sort key)
    items.sort(key=lambda x: x.get("sk", ""), reverse=True)

    messages = [
        {
            "role": item.get("role", ""),
            "content": item.get("content", ""),
            "userName": item.get("userName", ""),
            "timestamp": item.get("timestamp", item.get("sk", "")),
        }
        for item in items[:50]
    ]

    return success({"messages": messages})
