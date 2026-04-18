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


def _strip_sensitive(d: dict) -> dict:
    return {k: v for k, v in d.items() if k not in _SENSITIVE_RUNTIME_FIELDS}


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
