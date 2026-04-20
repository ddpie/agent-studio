"""AgentCore Control Plane passthrough endpoints.

Separated from agents.py because these read-only handlers talk to
bedrock-agentcore-control, not DynamoDB. Keeps agents.py focused on
DDB CRUD.
"""
import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError

from shared.config import AGENTS_TABLE, REGION
from shared.middleware import auth_check
from shared.response import success, forbidden, not_found, bad_request, internal_error
from shared.validators import validate_id

router = Router()
logger = Logger(child=True)

_control = None
_agents_table = None


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


def _get_agents_table():
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _agents_table


def _get_agent_item(agent_id: str) -> dict | None:
    """Fetch agent row for ownership check. Returns None if missing."""
    resp = _get_agents_table().get_item(Key={"agentId": agent_id}, ConsistentRead=True)
    return resp.get("Item")


_SENSITIVE_RUNTIME_FIELDS = {
    "agentRuntimeArn",
    "executionRoleArn",
    "agentRuntimeArtifact",
    "networkConfiguration",
    "protocolConfiguration",
    "filesystemConfigurations",
    "roleArn",
    "ResponseMetadata",
}


def _to_json_safe(value):
    """Recursively convert datetime/bytes to JSON-serialisable primitives.

    Naive datetimes are treated as UTC (AWS APIs return aware UTC, but
    tests and some call sites pass naive). Emitting a tz suffix ensures
    JS `new Date()` on the other side interprets as UTC, not local.
    """
    import datetime as _dt
    if isinstance(value, _dt.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
        return value.isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {k: _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [_to_json_safe(v) for v in value]
    return value


def _strip_sensitive(d: dict) -> dict:
    return {
        k: _to_json_safe(v)
        for k, v in d.items()
        if k not in _SENSITIVE_RUNTIME_FIELDS
    }


@router.get("/api/workspaces/<wsId>/agents/<agentId>/runtime")
def get_runtime(wsId: str, agentId: str):
    """Passthrough get_agent_runtime. Caller must be workspace member."""
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    try:
        resp = _get_control().get_agent_runtime(agentRuntimeId=agentId)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            return not_found()
        logger.exception("get_agent_runtime failed", extra={"agentId": agentId, "code": code})
        return internal_error()

    return success(_strip_sensitive(resp))


@router.get("/api/workspaces/<wsId>/agents/<agentId>/versions")
def list_versions(wsId: str, agentId: str):
    """Passthrough list_agent_runtime_versions, sorted newest first."""
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    try:
        resp = _get_control().list_agent_runtime_versions(agentRuntimeId=agentId)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            return not_found()
        logger.exception("list_agent_runtime_versions failed", extra={"agentId": agentId, "code": code})
        return internal_error()

    raw = resp.get("agentRuntimes", [])
    cleaned = [_strip_sensitive(v) for v in raw]

    def _key(v):
        try:
            return int(v.get("agentRuntimeVersion", "0"))
        except (TypeError, ValueError):
            return 0

    cleaned.sort(key=_key, reverse=True)
    return success({"versions": cleaned})


@router.get("/api/workspaces/<wsId>/agents/<agentId>/endpoints")
def list_endpoints(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()
    try:
        resp = _get_control().list_agent_runtime_endpoints(agentRuntimeId=agentId)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            return not_found()
        logger.exception("list_agent_runtime_endpoints failed", extra={"agentId": agentId})
        return internal_error()
    return success({"endpoints": [_strip_sensitive(e) for e in resp.get("runtimeEndpoints", [])]})


@router.post("/api/workspaces/<wsId>/agents/<agentId>/endpoints")
def create_endpoint(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()
    body = router.current_event.json_body or {}
    name = (body.get("name") or "").strip()
    version = (body.get("version") or "").strip()
    if not name or not version:
        return bad_request("name and version are required")
    if name.upper() == "DEFAULT":
        return bad_request("DEFAULT endpoint is managed automatically")
    try:
        resp = _get_control().create_agent_runtime_endpoint(
            agentRuntimeId=agentId,
            name=name,
            description=f"Created from Agent Studio by {user_id}",
            agentRuntimeVersion=version,
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code in ("ValidationException", "ConflictException"):
            return bad_request(e.response.get("Error", {}).get("Message", code))
        if code == "ResourceNotFoundException":
            return not_found()
        logger.exception("create_agent_runtime_endpoint failed", extra={"agentId": agentId, "name": name})
        return internal_error()
    return success(_strip_sensitive(resp), status_code=202)


@router.put("/api/workspaces/<wsId>/agents/<agentId>/endpoints/<endpointName>")
def update_endpoint(wsId: str, agentId: str, endpointName: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()
    body = router.current_event.json_body or {}
    version = (body.get("version") or "").strip()
    if not version:
        return bad_request("version is required")
    try:
        resp = _get_control().update_agent_runtime_endpoint(
            agentRuntimeId=agentId,
            endpointName=endpointName,
            agentRuntimeVersion=version,
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            return not_found()
        if code in ("ValidationException", "ConflictException"):
            return bad_request(e.response.get("Error", {}).get("Message", code))
        logger.exception("update_agent_runtime_endpoint failed", extra={"agentId": agentId, "endpointName": endpointName})
        return internal_error()
    return success(_strip_sensitive(resp), status_code=202)


@router.delete("/api/workspaces/<wsId>/agents/<agentId>/endpoints/<endpointName>")
def delete_endpoint(wsId: str, agentId: str, endpointName: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    if endpointName.upper() == "DEFAULT":
        return bad_request("DEFAULT endpoint cannot be deleted")
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()
    try:
        _get_control().delete_agent_runtime_endpoint(agentRuntimeId=agentId, endpointName=endpointName)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ResourceNotFoundException":
            return not_found()
        logger.exception("delete_agent_runtime_endpoint failed", extra={"agentId": agentId, "endpointName": endpointName})
        return internal_error()
    return success({"deleted": endpointName})
