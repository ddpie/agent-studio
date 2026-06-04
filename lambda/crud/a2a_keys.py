"""Per-user-per-agent A2A API keys.

Keys are returned ONCE in plaintext at creation. We store only the
SHA-256 hash in DynamoDB. The hash is the primary key, which also means
the proxy lambda can look up a key by hashing the incoming Bearer
token without a scan — O(1).

Key format: as_<32-chars base62>. The 'as_' prefix lets tooling
recognise it as an Agent Studio key at a glance. 'keyPrefix' (first 8
chars) is stored as plaintext to help users identify which key is which.
"""

import hashlib
import os
import uuid
from datetime import datetime, timezone

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key

from shared.config import A2A_KEYS_TABLE, AGENTS_TABLE, REGION
from shared.middleware import auth_check
from shared.response import bad_request, forbidden, not_found, success
from shared.validators import validate_id

router = Router()
logger = Logger(child=True)

_table = None
_agents_table = None

_KEY_CHARSET = "ABCDEFGHJKMNPQRSTVWXYZabcdefghijkmnpqrstuvwxyz23456789"


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(A2A_KEYS_TABLE)
    return _table


def _get_agent_item(agent_id: str) -> dict | None:
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    resp = _agents_table.get_item(Key={"agentId": agent_id}, ConsistentRead=True)
    return resp.get("Item")


def _generate_key() -> tuple[str, str, str]:
    """Generate a new key. Returns (plaintext, sha256_hex, prefix).

    Uses os.urandom rather than stdlib `secrets` because the CRUD lambda
    also ships a local module named secrets.py (crud/secrets.py), which
    shadows the standard-library secrets module on sys.path.
    """
    raw = os.urandom(32)
    n = len(_KEY_CHARSET)
    random_part = "".join(_KEY_CHARSET[b % n] for b in raw)
    plaintext = f"as_{random_part}"
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    return plaintext, h, plaintext[:8]


@router.post("/api/workspaces/<wsId>/agents/<agentId>/a2a-keys")
def create_key(wsId: str, agentId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    plaintext, key_hash, prefix = _generate_key()
    key_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    _get_table().put_item(
        Item={
            "apiKeyHash": key_hash,
            "keyId": key_id,
            "userAgentKey": f"{user_id}#{agentId}",
            "keyPrefix": prefix,
            "userId": user_id,
            "agentId": agentId,
            "workspaceId": ws_id,
            "createdAt": now,
            "revoked": False,
        }
    )
    return success(
        {
            "keyId": key_id,
            "apiKey": plaintext,
            "keyPrefix": prefix,
            "createdAt": now,
        },
        status_code=201,
    )


@router.get("/api/workspaces/<wsId>/agents/<agentId>/a2a-keys")
def list_keys(wsId: str, agentId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    resp = _get_table().query(
        IndexName="user-agent-index",
        KeyConditionExpression=Key("userAgentKey").eq(f"{user_id}#{agentId}"),
        ScanIndexForward=False,
    )
    keys = []
    for row in resp.get("Items", []):
        keys.append(
            {
                "keyId": row.get("keyId"),
                "keyPrefix": row.get("keyPrefix"),
                "createdAt": row.get("createdAt"),
                "lastUsedAt": row.get("lastUsedAt"),
                "revoked": bool(row.get("revoked", False)),
            }
        )
    return success({"keys": keys})


@router.delete("/api/workspaces/<wsId>/agents/<agentId>/a2a-keys/<keyId>")
def revoke_key(wsId: str, agentId: str, keyId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    resp = _get_table().query(
        IndexName="user-agent-index",
        KeyConditionExpression=Key("userAgentKey").eq(f"{user_id}#{agentId}"),
    )
    row = next((r for r in resp.get("Items", []) if r.get("keyId") == keyId), None)
    if not row:
        return not_found()
    if row.get("userId") != user_id:
        return forbidden()

    _get_table().update_item(
        Key={"apiKeyHash": row["apiKeyHash"]},
        UpdateExpression="SET revoked = :r",
        ExpressionAttributeValues={":r": True},
    )
    return success({"revoked": True, "keyId": keyId})


# ── Meta-Agent variants (same logic, agentId fixed to sentinel) ──

_META_SENTINEL = "meta-agent"


@router.post("/api/workspaces/<wsId>/meta-agent/a2a-keys")
def create_meta_key(wsId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    plaintext, key_hash, prefix = _generate_key()
    key_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    _get_table().put_item(
        Item={
            "apiKeyHash": key_hash,
            "keyId": key_id,
            "userAgentKey": f"{user_id}#{_META_SENTINEL}",
            "keyPrefix": prefix,
            "userId": user_id,
            "agentId": _META_SENTINEL,
            "workspaceId": ws_id,
            "createdAt": now,
            "revoked": False,
        }
    )
    return success(
        {"keyId": key_id, "apiKey": plaintext, "keyPrefix": prefix, "createdAt": now},
        status_code=201,
    )


@router.get("/api/workspaces/<wsId>/meta-agent/a2a-keys")
def list_meta_keys(wsId: str):
    user_id, _, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    resp = _get_table().query(
        IndexName="user-agent-index",
        KeyConditionExpression=Key("userAgentKey").eq(f"{user_id}#{_META_SENTINEL}"),
        ScanIndexForward=False,
    )
    keys = [
        {
            "keyId": r.get("keyId"),
            "keyPrefix": r.get("keyPrefix"),
            "createdAt": r.get("createdAt"),
            "lastUsedAt": r.get("lastUsedAt"),
            "revoked": bool(r.get("revoked", False)),
        }
        for r in resp.get("Items", [])
    ]
    return success({"keys": keys})


@router.delete("/api/workspaces/<wsId>/meta-agent/a2a-keys/<keyId>")
def revoke_meta_key(wsId: str, keyId: str):
    user_id, _, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    resp = _get_table().query(
        IndexName="user-agent-index",
        KeyConditionExpression=Key("userAgentKey").eq(f"{user_id}#{_META_SENTINEL}"),
    )
    row = next((r for r in resp.get("Items", []) if r.get("keyId") == keyId), None)
    if not row:
        return not_found()
    if row.get("userId") != user_id:
        return forbidden()
    _get_table().update_item(
        Key={"apiKeyHash": row["apiKeyHash"]},
        UpdateExpression="SET revoked = :r",
        ExpressionAttributeValues={":r": True},
    )
    return success({"revoked": True, "keyId": keyId})
