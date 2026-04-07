"""Workspace CRUD endpoints."""
import json
import uuid
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key

from shared.auth import verify_jwt, get_membership, check_permission, ROLE_LEVEL
from shared.config import WORKSPACES_TABLE, REGION
from shared.middleware import auth_check
from shared.response import success, paginated, forbidden, not_found, bad_request, version_conflict, internal_error
from shared.validators import validate_id, parse_pagination

router = Router()
logger = Logger(child=True)

_table = None


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _table


# ─── GET /api/workspaces ───
@router.get("/api/workspaces")
def list_workspaces():
    user_id, _, _, err = auth_check(router.current_event, require_ws=False)
    if err:
        return err

    table = _get_table()
    resp = table.query(
        IndexName="user-index",
        KeyConditionExpression=Key("userId").eq(user_id),
    )
    member_records = resp.get("Items", [])

    workspaces = []
    for rec in member_records:
        ws_id = rec["workspaceId"]
        meta_resp = table.get_item(Key={"workspaceId": ws_id, "sk": "META"}, ConsistentRead=True)
        meta = meta_resp.get("Item")
        if meta:
            workspaces.append({
                "workspaceId": meta["workspaceId"],
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "role": rec.get("role", "viewer"),
                "created_at": meta.get("created_at", ""),
            })

    return success({"items": workspaces})


# ─── POST /api/workspaces ───
@router.post("/api/workspaces")
def create_workspace():
    user_id, _, _, err = auth_check(router.current_event, require_ws=False)
    if err:
        return err

    body = router.current_event.json_body or {}
    name = body.get("name", "").strip()
    if not name:
        return bad_request("name is required")
    if len(name) > 100:
        return bad_request("name must be 100 characters or less")

    ws_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"
    table = _get_table()

    table.meta.client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": table.name,
                    "Item": {
                        "workspaceId": {"S": ws_id},
                        "sk": {"S": "META"},
                        "name": {"S": name},
                        "description": {"S": body.get("description", "")},
                        "owner_id": {"S": user_id},
                        "created_at": {"S": now},
                        "updated_at": {"S": now},
                    },
                }
            },
            {
                "Put": {
                    "TableName": table.name,
                    "Item": {
                        "workspaceId": {"S": ws_id},
                        "sk": {"S": f"MEMBER#{user_id}"},
                        "userId": {"S": user_id},
                        "role": {"S": "owner"},
                        "joined_at": {"S": now},
                    },
                }
            },
        ]
    )

    return success({
        "workspaceId": ws_id,
        "name": name,
        "description": body.get("description", ""),
        "role": "owner",
        "created_at": now,
    }, status_code=201)


# ─── POST /api/onboarding ───
@router.post("/api/onboarding")
def onboarding():
    user_id, _, _, err = auth_check(router.current_event, require_ws=False)
    if err:
        return err

    table = _get_table()
    ws_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    try:
        table.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": {
                            "workspaceId": {"S": ws_id},
                            "sk": {"S": "META"},
                            "name": {"S": "My Workspace"},
                            "description": {"S": "Default workspace"},
                            "owner_id": {"S": user_id},
                            "created_at": {"S": now},
                            "updated_at": {"S": now},
                        },
                    }
                },
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": {
                            "workspaceId": {"S": ws_id},
                            "sk": {"S": f"MEMBER#{user_id}"},
                            "userId": {"S": user_id},
                            "role": {"S": "owner"},
                            "joined_at": {"S": now},
                        },
                        "ConditionExpression": "attribute_not_exists(sk)",
                    }
                },
            ]
        )
    except table.meta.client.exceptions.TransactionCanceledException:
        resp = table.query(
            IndexName="user-index",
            KeyConditionExpression=Key("userId").eq(user_id),
            Limit=1,
        )
        if resp.get("Items"):
            existing_ws_id = resp["Items"][0]["workspaceId"]
            meta = table.get_item(
                Key={"workspaceId": existing_ws_id, "sk": "META"},
                ConsistentRead=True,
            ).get("Item", {})
            return success({
                "workspaceId": existing_ws_id,
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "role": "owner",
                "created_at": meta.get("created_at", ""),
                "onboarding": False,
            })
        return bad_request("User already has a workspace")

    return success({
        "workspaceId": ws_id,
        "name": "My Workspace",
        "description": "Default workspace",
        "role": "owner",
        "created_at": now,
        "onboarding": True,
    }, status_code=201)


# ─── GET /api/workspaces/{wsId} ───
@router.get("/api/workspaces/<wsId>")
def get_workspace(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="viewer", ws_id=wsId)
    if err:
        return err

    table = _get_table()
    meta_resp = table.get_item(Key={"workspaceId": ws_id, "sk": "META"}, ConsistentRead=True)
    meta = meta_resp.get("Item")
    if not meta:
        return forbidden()

    members_resp = table.query(
        KeyConditionExpression=Key("workspaceId").eq(ws_id) & Key("sk").begins_with("MEMBER#"),
    )
    members = []
    for m in members_resp.get("Items", []):
        members.append({
            "userId": m.get("userId", ""),
            "role": m.get("role", "viewer"),
            "joined_at": m.get("joined_at", ""),
            "display_name": m.get("display_name", ""),
        })

    return success({
        "workspaceId": meta["workspaceId"],
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "owner_id": meta.get("owner_id", ""),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
        "members": members,
    })


# ─── PUT /api/workspaces/{wsId} ───
@router.put("/api/workspaces/<wsId>")
def update_workspace(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    name = body.get("name", "").strip()
    if not name:
        return bad_request("name is required")
    if len(name) > 100:
        return bad_request("name must be 100 characters or less")

    now = datetime.utcnow().isoformat() + "Z"
    expected_updated_at = body.get("expected_updated_at")
    if not expected_updated_at:
        return bad_request("expected_updated_at is required for optimistic concurrency control")

    table = _get_table()
    update_expr = "SET #n = :name, description = :desc, updated_at = :now"
    expr_values = {
        ":name": name,
        ":desc": body.get("description", ""),
        ":now": now,
        ":expected": expected_updated_at,
    }
    condition = "attribute_exists(workspaceId) AND updated_at = :expected"

    try:
        table.update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression=update_expr,
            ExpressionAttributeNames={"#n": "name"},
            ExpressionAttributeValues=expr_values,
            ConditionExpression=condition,
        )
    except table.meta.client.exceptions.ConditionalCheckFailedException:
        return version_conflict("Workspace was modified by another request")

    return success({
        "workspaceId": ws_id,
        "name": name,
        "description": body.get("description", ""),
        "updated_at": now,
    })


# ─── DELETE /api/workspaces/{wsId} ───
@router.delete("/api/workspaces/<wsId>")
def delete_workspace(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="owner", ws_id=wsId)
    if err:
        return err

    table = _get_table()
    last_key = None
    while True:
        query_kwargs = {
            "KeyConditionExpression": Key("workspaceId").eq(ws_id),
            "ProjectionExpression": "workspaceId, sk",
        }
        if last_key:
            query_kwargs["ExclusiveStartKey"] = last_key
        resp = table.query(**query_kwargs)
        with table.batch_writer() as batch:
            for item in resp.get("Items", []):
                batch.delete_item(Key={"workspaceId": item["workspaceId"], "sk": item["sk"]})
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break

    return success({"deleted": True})


# ─── POST /api/workspaces/{wsId}/members — 邀请成员 ───
@router.post("/api/workspaces/<wsId>/members")
def invite_member(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    import secrets
    body = router.current_event.json_body or {}
    email = body.get("email", "").strip()
    role = body.get("role", "viewer")
    if not email:
        return bad_request("email is required")
    if role not in ("viewer", "editor", "admin"):
        return bad_request("role must be viewer, editor, or admin")

    # Admin can only invite as viewer or editor. Only Owner can invite as admin.
    if role == "admin" and member.get("role") != "owner":
        return forbidden()

    token = secrets.token_urlsafe(32)
    now = datetime.utcnow().isoformat() + "Z"
    import time
    ttl = int(time.time()) + 7 * 24 * 3600

    table = _get_table()
    table.put_item(
        Item={
            "workspaceId": ws_id,
            "sk": f"INVITE#{token}",
            "email": email,
            "role": role,
            "invited_by": user_id,
            "created_at": now,
            "expires_at": ttl,
            "token": token,
        }
    )

    return success({
        "token": token,
        "email": email,
        "role": role,
        "created_at": now,
    }, status_code=201)


# ─── PUT /api/workspaces/{wsId}/members/{userId} — 修改角色 ───
@router.put("/api/workspaces/<wsId>/members/<memberId>")
def update_member_role(wsId: str, memberId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(memberId, "userId")
    if id_err:
        return bad_request(id_err)

    body = router.current_event.json_body or {}
    new_role = body.get("role", "")
    if new_role not in ("viewer", "editor", "admin"):
        return bad_request("role must be viewer, editor, or admin")

    table = _get_table()
    target = table.get_item(
        Key={"workspaceId": ws_id, "sk": f"MEMBER#{memberId}"},
        ConsistentRead=True,
    ).get("Item")
    if not target:
        return forbidden()
    if target.get("role") == "owner":
        return forbidden()

    if new_role == "admin" and member.get("role") != "owner":
        return forbidden()

    table.update_item(
        Key={"workspaceId": ws_id, "sk": f"MEMBER#{memberId}"},
        UpdateExpression="SET #r = :role",
        ExpressionAttributeNames={"#r": "role"},
        ExpressionAttributeValues={":role": new_role},
        ConditionExpression="attribute_exists(workspaceId)",
    )

    return success({"userId": memberId, "role": new_role})


# ─── DELETE /api/workspaces/{wsId}/members/{userId} — 移除成员 ───
@router.delete("/api/workspaces/<wsId>/members/<memberId>")
def remove_member(wsId: str, memberId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    id_err = validate_id(memberId, "userId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    target = table.get_item(
        Key={"workspaceId": ws_id, "sk": f"MEMBER#{memberId}"},
        ConsistentRead=True,
    ).get("Item")
    if not target:
        return forbidden()
    if target.get("role") == "owner":
        return bad_request("Cannot remove workspace owner")

    table.delete_item(Key={"workspaceId": ws_id, "sk": f"MEMBER#{memberId}"})
    return success({"removed": True})


# ─── POST /api/workspaces/{wsId}/leave — 自行退出 ───
@router.post("/api/workspaces/<wsId>/leave")
def leave_workspace(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="viewer", ws_id=wsId)
    if err:
        return err

    if member.get("role") == "owner":
        return bad_request("Owner cannot leave. Transfer ownership first.")

    table = _get_table()
    table.delete_item(Key={"workspaceId": ws_id, "sk": f"MEMBER#{user_id}"})
    return success({"left": True})


# ─── POST /api/workspaces/{wsId}/transfer-ownership — 转让 Owner ───
@router.post("/api/workspaces/<wsId>/transfer-ownership")
def transfer_ownership(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="owner", ws_id=wsId)
    if err:
        return err

    body = router.current_event.json_body or {}
    new_owner_id = body.get("targetUserId", "").strip()
    if not new_owner_id:
        return bad_request("targetUserId is required")
    id_err = validate_id(new_owner_id, "targetUserId")
    if id_err:
        return bad_request(id_err)

    table = _get_table()
    new_owner = table.get_item(
        Key={"workspaceId": ws_id, "sk": f"MEMBER#{new_owner_id}"},
        ConsistentRead=True,
    ).get("Item")
    if not new_owner:
        return forbidden()

    now = datetime.utcnow().isoformat() + "Z"

    table.meta.client.transact_write_items(
        TransactItems=[
            {
                "Update": {
                    "TableName": table.name,
                    "Key": {
                        "workspaceId": {"S": ws_id},
                        "sk": {"S": f"MEMBER#{new_owner_id}"},
                    },
                    "UpdateExpression": "SET #r = :role",
                    "ExpressionAttributeNames": {"#r": "role"},
                    "ExpressionAttributeValues": {":role": {"S": "owner"}},
                }
            },
            {
                "Update": {
                    "TableName": table.name,
                    "Key": {
                        "workspaceId": {"S": ws_id},
                        "sk": {"S": f"MEMBER#{user_id}"},
                    },
                    "UpdateExpression": "SET #r = :role",
                    "ExpressionAttributeNames": {"#r": "role"},
                    "ExpressionAttributeValues": {":role": {"S": "admin"}},
                }
            },
            {
                "Update": {
                    "TableName": table.name,
                    "Key": {
                        "workspaceId": {"S": ws_id},
                        "sk": {"S": "META"},
                    },
                    "UpdateExpression": "SET owner_id = :oid, updated_at = :now",
                    "ExpressionAttributeValues": {
                        ":oid": {"S": new_owner_id},
                        ":now": {"S": now},
                    },
                }
            },
        ]
    )

    return success({"newOwnerId": new_owner_id, "previousOwnerId": user_id})


# ─── GET /api/workspaces/{wsId}/invitations — 待处理邀请列表 ───
@router.get("/api/workspaces/<wsId>/invitations")
def list_invitations(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    table = _get_table()
    resp = table.query(
        KeyConditionExpression=Key("workspaceId").eq(ws_id) & Key("sk").begins_with("INVITE#"),
    )
    invitations = []
    for item in resp.get("Items", []):
        invitations.append({
            "token": item.get("token", ""),
            "email": item.get("email", ""),
            "role": item.get("role", "viewer"),
            "invited_by": item.get("invited_by", ""),
            "created_at": item.get("created_at", ""),
        })

    return success({"items": invitations})


# ─── DELETE /api/workspaces/{wsId}/invitations/{token} — 撤销邀请 ───
@router.delete("/api/workspaces/<wsId>/invitations/<token>")
def revoke_invitation(wsId: str, token: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="admin", ws_id=wsId)
    if err:
        return err

    table = _get_table()
    resp = table.get_item(Key={"workspaceId": ws_id, "sk": f"INVITE#{token}"}, ConsistentRead=True)
    if not resp.get("Item"):
        return forbidden()

    table.delete_item(Key={"workspaceId": ws_id, "sk": f"INVITE#{token}"})
    return success({"revoked": True})


# ─── GET /api/invitations/{token} — 验证邀请链接 (未认证) ───
@router.get("/api/invitations/<token>")
def verify_invitation(token: str):
    table = _get_table()
    resp = table.scan(
        FilterExpression="sk = :sk",
        ExpressionAttributeValues={":sk": f"INVITE#{token}"},
    )
    items = resp.get("Items", [])
    while not items and resp.get("LastEvaluatedKey"):
        resp = table.scan(
            FilterExpression="sk = :sk",
            ExpressionAttributeValues={":sk": f"INVITE#{token}"},
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items = resp.get("Items", [])
    if not items:
        return not_found()

    invite = items[0]
    ws_id = invite["workspaceId"]
    meta = table.get_item(Key={"workspaceId": ws_id, "sk": "META"}, ConsistentRead=True).get("Item", {})

    return success({
        "workspaceName": meta.get("name", ""),
    })


# ─── POST /api/invitations/{token}/accept — 接受邀请 ───
@router.post("/api/invitations/<token>/accept")
def accept_invitation(token: str):
    user_id, _, _, err = auth_check(router.current_event, require_ws=False)
    if err:
        return err

    table = _get_table()
    resp = table.scan(
        FilterExpression="sk = :sk",
        ExpressionAttributeValues={":sk": f"INVITE#{token}"},
    )
    items = resp.get("Items", [])
    while not items and resp.get("LastEvaluatedKey"):
        resp = table.scan(
            FilterExpression="sk = :sk",
            ExpressionAttributeValues={":sk": f"INVITE#{token}"},
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items = resp.get("Items", [])
    if not items:
        return not_found()

    invite = items[0]
    ws_id = invite["workspaceId"]
    role = invite.get("role", "viewer")
    now = datetime.utcnow().isoformat() + "Z"

    existing = table.get_item(
        Key={"workspaceId": ws_id, "sk": f"MEMBER#{user_id}"},
        ConsistentRead=True,
    ).get("Item")
    if existing:
        return bad_request("Already a member of this workspace")

    table.meta.client.transact_write_items(
        TransactItems=[
            {
                "Put": {
                    "TableName": table.name,
                    "Item": {
                        "workspaceId": {"S": ws_id},
                        "sk": {"S": f"MEMBER#{user_id}"},
                        "userId": {"S": user_id},
                        "role": {"S": role},
                        "joined_at": {"S": now},
                    },
                }
            },
            {
                "Delete": {
                    "TableName": table.name,
                    "Key": {
                        "workspaceId": {"S": ws_id},
                        "sk": {"S": f"INVITE#{token}"},
                    },
                }
            },
        ]
    )

    return success({
        "workspaceId": ws_id,
        "role": role,
        "joined_at": now,
    })
