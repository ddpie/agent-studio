"""Agent secrets CRUD endpoints (AWS Secrets Manager)."""

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.config import AGENTS_TABLE, REGION
from shared.middleware import auth_check
from shared.response import bad_request, forbidden, not_found, success
from shared.validators import validate_id, validate_secret_key

router = Router()
logger = Logger(child=True)

_sm = None
_agents_table = None


def _get_sm():
    global _sm
    if _sm is None:
        _sm = boto3.client("secretsmanager", region_name=REGION)
    return _sm


def _get_agents_table():
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _agents_table


def _secret_path(workspace_id: str, agent_id: str, key: str = "") -> str:
    base = f"agent-studio/{workspace_id}/{agent_id}"
    return f"{base}/{key}" if key else base


def _verify_agent_ownership(agent_id: str, workspace_id: str) -> bool:
    table = _get_agents_table()
    item = table.get_item(Key={"agentId": agent_id}, ConsistentRead=True).get("Item")
    return item is not None and item.get("workspace_id") == workspace_id


@router.get("/api/workspaces/<wsId>/agents/<agentId>/secrets")
def list_secrets(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    if not _verify_agent_ownership(agentId, ws_id):
        return forbidden()

    sm = _get_sm()
    prefix = _secret_path(ws_id, agentId) + "/"
    keys = []
    try:
        paginator = sm.get_paginator("list_secrets")
        for page in paginator.paginate(
            Filters=[{"Key": "name", "Values": [prefix]}],
        ):
            for secret in page.get("SecretList", []):
                name = secret["Name"]
                key = name[len(prefix) :]
                if key:
                    keys.append(
                        {
                            "key": key,
                            "created_at": secret.get("CreatedDate", "").isoformat()
                            if hasattr(secret.get("CreatedDate", ""), "isoformat")
                            else "",
                            "updated_at": secret.get("LastChangedDate", "").isoformat()
                            if hasattr(secret.get("LastChangedDate", ""), "isoformat")
                            else "",
                        }
                    )
    except Exception:
        logger.exception("Failed to list secrets")
        keys = []

    return success({"items": keys})


@router.post("/api/workspaces/<wsId>/agents/<agentId>/secrets")
def set_secret(wsId: str, agentId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    if not _verify_agent_ownership(agentId, ws_id):
        return forbidden()

    body = router.current_event.json_body or {}
    key = body.get("key", "").strip()
    value = body.get("value", "")
    if not key:
        return bad_request("key is required")
    if not value:
        return bad_request("value is required")
    key_err = validate_secret_key(key, "key")
    if key_err:
        return bad_request(key_err)

    sm = _get_sm()
    secret_name = _secret_path(ws_id, agentId, key)

    try:
        sm.put_secret_value(SecretId=secret_name, SecretString=value)
    except sm.exceptions.ResourceNotFoundException:
        sm.create_secret(
            Name=secret_name,
            SecretString=value,
            Description=f"Agent secret for {agentId} in workspace {ws_id}",
        )

    return success({"key": key, "set": True}, status_code=201)


@router.delete("/api/workspaces/<wsId>/agents/<agentId>/secrets/<secretKey>")
def delete_secret(wsId: str, agentId: str, secretKey: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    key_err = validate_secret_key(secretKey, "secretKey")
    if key_err:
        return bad_request(key_err)

    if not _verify_agent_ownership(agentId, ws_id):
        return forbidden()

    sm = _get_sm()
    secret_name = _secret_path(ws_id, agentId, secretKey)

    try:
        sm.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
    except sm.exceptions.ResourceNotFoundException:
        return not_found()

    return success({"key": secretKey, "deleted": True})
