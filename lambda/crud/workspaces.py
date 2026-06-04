"""Workspace CRUD endpoints."""
import json
import uuid
from datetime import datetime

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key

from shared.config import COGNITO_USER_POOL_ID, REGION, WORKSPACES_TABLE
from shared.memory_strategies import DEFAULT_MEMORY_STRATEGIES
from shared.middleware import auth_check, check_platform_admin
from shared.response import (
    bad_request,
    error,
    forbidden,
    internal_error,
    not_found,
    success,
    version_conflict,
)
from shared.validators import validate_id

_iam_client = None

router = Router()
logger = Logger(child=True)

_table = None
_cognito = None
_control = None
# Per-userId identity cache. Keyed by Cognito sub (== userId) so it can
# safely span workspaces — the sub->attributes mapping is global to the
# user pool. Lambda container reuse provides the bulk of the speedup.
_identity_cache: dict = {}


def _get_table():
    global _table
    if _table is None:
        _table = boto3.resource("dynamodb", region_name=REGION).Table(WORKSPACES_TABLE)
    return _table


def _get_cognito():
    global _cognito
    if _cognito is None:
        _cognito = boto3.client("cognito-idp", region_name=REGION)
    return _cognito


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


def _delete_workspace_memory(memory_id: str) -> None:
    """Best-effort delete of AgentCore Memory resource. Logs on failure."""
    if not memory_id:
        return
    try:
        _get_control().delete_memory(memoryId=memory_id)
    except Exception as e:
        logger.warning("delete_memory failed for %s: %s", memory_id, e)


def _workspace_name_exists(table, name: str, exclude_ws_id: str = "") -> bool:
    """Check if any workspace globally has this name (case-insensitive)."""
    name_lower = name.strip().lower()
    scan_kwargs = {
        "FilterExpression": "sk = :meta",
        "ExpressionAttributeValues": {":meta": "META"},
        "ProjectionExpression": "workspaceId, #n",
        "ExpressionAttributeNames": {"#n": "name"},
    }
    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            if item.get("name", "").strip().lower() == name_lower:
                if exclude_ws_id and item.get("workspaceId") == exclude_ws_id:
                    continue
                return True
        if not resp.get("LastEvaluatedKey"):
            break
        scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return False


def _create_workspace_memory(workspace_id: str) -> str | None:
    """Best-effort create an AgentCore Memory for the workspace.

    Returns the memory ID on success, or None if the call fails.
    Failure must never block workspace creation.  If a memory with the
    same name already exists (e.g. DDB reference was lost but the
    AgentCore resource survived), we recover by listing memories and
    returning the existing ID.
    """
    safe_name = f"agentstudio_ws_{workspace_id[:12].replace('-', '_')}"
    try:
        resp = _get_control().create_memory(
            name=safe_name,
            description=f"Agent Studio workspace {workspace_id}",
            memoryStrategies=DEFAULT_MEMORY_STRATEGIES,
            eventExpiryDuration=90,
        )
        return resp["memory"]["id"]
    except Exception as e:
        if "already exists" in str(e):
            found = _find_memory_by_name(safe_name)
            if found:
                return found
        logger.warning("create_memory failed for workspace %s: %s", workspace_id, e)
        return None


def _find_memory_by_name(name: str) -> str | None:
    """Look up an existing memory by name prefix. Returns the ID or None."""
    try:
        control = _get_control()
        resp = control.list_memories()
        for m in resp.get("memories", []):
            if m.get("id", "").startswith(name):
                return m["id"]
        while resp.get("nextToken"):
            resp = control.list_memories(nextToken=resp["nextToken"])
            for m in resp.get("memories", []):
                if m.get("id", "").startswith(name):
                    return m["id"]
    except Exception as e:
        logger.warning("list_memories fallback failed: %s", e)
    return None


def _hydrate_member_identities(members: list) -> list:
    """Fill in display_name/email on member dicts using Cognito AdminGetUser.

    Members whose display_name is already set pass through untouched (the
    DDB MEMBER item has authoritative data — avoid an extra Cognito call).
    Per-user lookup failures (UserNotFoundException, AccessDenied, etc.)
    are logged at warning level and leave the entry unchanged so one bad
    user doesn't break the whole roster.
    """
    if not COGNITO_USER_POOL_ID:
        return members

    client = _get_cognito()
    hydrated = []
    for m in members:
        if m.get("display_name"):
            hydrated.append(m)
            continue
        user_id = m.get("userId", "")
        if not user_id:
            hydrated.append(m)
            continue

        cached = _identity_cache.get(user_id)
        if cached is None:
            try:
                resp = client.admin_get_user(
                    UserPoolId=COGNITO_USER_POOL_ID,
                    Username=user_id,
                )
                attrs = {a["Name"]: a["Value"] for a in resp.get("UserAttributes", [])}
                cached = {
                    "display_name": attrs.get("name") or "",
                    "email": attrs.get("email", ""),
                }
                _identity_cache[user_id] = cached
            except Exception as e:
                logger.warning(
                    "cognito admin_get_user failed",
                    extra={"userId": user_id, "error": str(e)},
                )
                hydrated.append(m)
                continue

        merged = dict(m)
        if cached.get("display_name") and not merged.get("display_name"):
            merged["display_name"] = cached["display_name"]
        if cached.get("email") and not merged.get("email"):
            merged["email"] = cached["email"]
        hydrated.append(merged)

    return hydrated


def _hydrate_owner_identities(workspaces: list) -> None:
    """Annotate each workspace dict with `owner_name` / `owner_email` in-place.

    Per-user failures fall back to a truncated user id; they must never
    break the whole list.
    """
    owner_ids = {w["owner_id"] for w in workspaces if w.get("owner_id")}
    if not owner_ids:
        return
    owner_map: dict = {}
    for uid in owner_ids:
        cached = _identity_cache.get(uid)
        if cached:
            owner_map[uid] = cached
        elif COGNITO_USER_POOL_ID:
            try:
                resp = _get_cognito().admin_get_user(UserPoolId=COGNITO_USER_POOL_ID, Username=uid)
                attrs = {a["Name"]: a["Value"] for a in resp.get("UserAttributes", [])}
                info = {"display_name": attrs.get("name", attrs.get("email", uid[:8])), "email": attrs.get("email", "")}
                _identity_cache[uid] = info
                owner_map[uid] = info
            except Exception:
                owner_map[uid] = {"display_name": uid[:8], "email": ""}
    for w in workspaces:
        info = owner_map.get(w.get("owner_id", ""))
        if info:
            w["owner_name"] = info["display_name"]
            w["owner_email"] = info["email"]


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
                "owner_id": meta.get("owner_id", ""),
            })

    _hydrate_owner_identities(workspaces)
    return success({"items": workspaces})


# ─── GET /api/admin/workspaces ───
@router.get("/api/admin/workspaces")
def list_all_workspaces_as_admin():
    """Platform-admin-only: list every workspace in the system.

    Scans the workspaces table for `sk = "META"` rows. Pagination follows
    DDB's native LastEvaluatedKey: callers pass `?next=<base64>` to resume.
    `role` is omitted because the admin may not be a member — `AdminWorkspaceIamPanel`
    doesn't use it.
    """
    _, is_admin, admin_err = check_platform_admin(router.current_event)
    if admin_err:
        return admin_err
    if not is_admin:
        return forbidden()

    qp = router.current_event.query_string_parameters or {}
    next_token = qp.get("next")

    import base64
    start_key = None
    if next_token:
        try:
            start_key = json.loads(base64.urlsafe_b64decode(next_token.encode()).decode())
        except Exception:
            return bad_request("invalid next token")

    table = _get_table()
    scan_kwargs = {
        "FilterExpression": "sk = :sk",
        "ExpressionAttributeValues": {":sk": "META"},
    }
    if start_key:
        scan_kwargs["ExclusiveStartKey"] = start_key
    resp = table.scan(**scan_kwargs)

    workspaces = []
    for meta in resp.get("Items", []):
        workspaces.append({
            "workspaceId": meta["workspaceId"],
            "name": meta.get("name", ""),
            "description": meta.get("description", ""),
            "created_at": meta.get("created_at", ""),
            "owner_id": meta.get("owner_id", ""),
        })

    _hydrate_owner_identities(workspaces)

    result = {"items": workspaces}
    last_key = resp.get("LastEvaluatedKey")
    if last_key:
        result["next"] = base64.urlsafe_b64encode(json.dumps(last_key).encode()).decode()
    return success(result)


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

    table = _get_table()

    # Global duplicate name check (case-insensitive): paginated scan
    if _workspace_name_exists(table, name):
        return error("workspace_name_duplicate", "WORKSPACE_NAME_DUPLICATE", 409)

    ws_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    meta_item = {
        "workspaceId": ws_id,
        "sk": "META",
        "name": name,
        "owner_id": user_id,
        "created_at": now,
        "updated_at": now,
    }
    desc = (body.get("description") or "").strip()
    if desc:
        meta_item["description"] = desc
    # Two separate put_item calls instead of transact_write_items:
    # boto3 table.put_item accepts native Python types (auto-serialized),
    # whereas transact_write_items on the low-level client requires typed
    # AttributeValue dicts. Using the Table resource keeps the code path
    # consistent with the rest of this module.
    table.put_item(Item=meta_item)
    table.put_item(Item={
        "workspaceId": ws_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": "owner",
        "joined_at": now,
    })

    memory_id = _create_workspace_memory(ws_id)
    if memory_id:
        table.update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET memory_id = :m",
            ExpressionAttributeValues={":m": memory_id},
        )

    return success({
        "workspaceId": ws_id,
        "name": name,
        "description": body.get("description", ""),
        "role": "owner",
        "created_at": now,
        "memory_id": memory_id,
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

    # Generate a unique default workspace name from user's email prefix
    default_name = "My Workspace"
    email_prefix = ""
    email_domain = ""
    try:
        if COGNITO_USER_POOL_ID:
            cogn_resp = _get_cognito().admin_get_user(UserPoolId=COGNITO_USER_POOL_ID, Username=user_id)
            attrs = {a["Name"]: a["Value"] for a in cogn_resp.get("UserAttributes", [])}
            email = attrs.get("email", "")
            if "@" in email:
                email_prefix = email.split("@")[0]
                email_domain = email.split("@")[1].split(".")[0]
                default_name = email_prefix
    except Exception:
        pass
    # Dedup: first try with domain disambiguation, then numeric suffix
    if _workspace_name_exists(table, default_name) and email_domain:
        default_name = f"{email_prefix}({email_domain})"
    candidate = default_name
    suffix = 1
    while _workspace_name_exists(table, candidate):
        suffix += 1
        candidate = f"{default_name} {suffix}"
    default_name = candidate

    try:
        table.meta.client.transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": table.name,
                        "Item": {
                            "workspaceId": {"S": ws_id},
                            "sk": {"S": "META"},
                            "name": {"S": default_name},
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

    memory_id = _create_workspace_memory(ws_id)
    if memory_id:
        table.update_item(
            Key={"workspaceId": ws_id, "sk": "META"},
            UpdateExpression="SET memory_id = :m",
            ExpressionAttributeValues={":m": memory_id},
        )

    return success({
        "workspaceId": ws_id,
        "name": default_name,
        "description": "Default workspace",
        "role": "owner",
        "created_at": now,
        "onboarding": True,
        "memory_id": memory_id,
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
            "email": m.get("email", ""),
        })
    members = _hydrate_member_identities(members)

    return success({
        "workspaceId": meta["workspaceId"],
        "name": meta.get("name", ""),
        "description": meta.get("description", ""),
        "owner_id": meta.get("owner_id", ""),
        "created_at": meta.get("created_at", ""),
        "updated_at": meta.get("updated_at", ""),
        "memory_id": meta.get("memory_id", ""),
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

    # Duplicate name check on rename (exclude self)
    if _workspace_name_exists(table, name, exclude_ws_id=ws_id):
        return error("workspace_name_duplicate", "WORKSPACE_NAME_DUPLICATE", 409)
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


# ─── Workspace IAM role cleanup ───

def _get_iam_client():
    global _iam_client
    if _iam_client is None:
        _iam_client = boto3.client("iam", region_name=REGION)
    return _iam_client


def _delete_workspace_iam_role(workspace_meta: dict) -> None:
    """Clean up IAM role when a workspace is deleted.

    Best-effort: failures are logged but do not block workspace deletion.
    Follows the required IAM deletion order:
      1. Delete all inline policies
      2. Detach all managed policies
      3. Delete the role
    """
    role_name = workspace_meta.get("roleName")
    if not role_name:
        return

    iam = _get_iam_client()
    try:
        # 1. Delete all inline policies
        policies = iam.list_role_policies(RoleName=role_name)
        for policy_name in policies.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)

        # 2. Detach all managed policies
        attached = iam.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])

        # 3. Delete the role
        iam.delete_role(RoleName=role_name)
        logger.info("Deleted workspace IAM role", extra={"roleName": role_name})
    except iam.exceptions.NoSuchEntityException:
        pass  # Role already deleted
    except Exception as e:
        logger.warning(
            "Failed to delete workspace IAM role",
            extra={"roleName": role_name, "error": str(e)},
        )


# ─── DELETE /api/workspaces/{wsId} ───
@router.delete("/api/workspaces/<wsId>")
def delete_workspace(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="owner", ws_id=wsId)
    if err:
        return err

    table = _get_table()

    # Fetch workspace META for cleanup before purging DDB records.
    meta_resp = table.get_item(Key={"workspaceId": ws_id, "sk": "META"}, ConsistentRead=True)
    workspace_meta = meta_resp.get("Item") or {}

    # Best-effort cleanup: Memory + IAM role (before DDB purge).
    _delete_workspace_memory(workspace_meta.get("memory_id", ""))
    _delete_workspace_iam_role(workspace_meta)

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


# ─── POST /api/workspaces/{wsId}/memory/repair ───
@router.post("/api/workspaces/<wsId>/memory/repair")
def repair_workspace_memory(wsId: str):
    user_id, ws_id, member, err = auth_check(router.current_event, min_role="owner", ws_id=wsId)
    if err:
        return err

    table = _get_table()
    resp = table.get_item(Key={"workspaceId": wsId, "sk": "META"}, ConsistentRead=True)
    if "Item" not in resp:
        return not_found("workspace not found")

    existing = resp["Item"].get("memory_id")
    if existing:
        return success({"memory_id": existing})

    new_id = _create_workspace_memory(wsId)
    if not new_id:
        return internal_error("memory creation still failing")

    table.update_item(
        Key={"workspaceId": wsId, "sk": "META"},
        UpdateExpression="SET memory_id = :m",
        ExpressionAttributeValues={":m": new_id},
    )
    return success({"memory_id": new_id})


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

    # Three independent updates instead of transact_write_items (see
    # accept_invitation / create_workspace for the reserialization issue).
    # Order matters: promote new owner first, then demote self, then update
    # META. If we crash mid-sequence the workspace ends up with two owners
    # temporarily — still admin-recoverable and preferable to zero owners.
    table.update_item(
        Key={"workspaceId": ws_id, "sk": f"MEMBER#{new_owner_id}"},
        UpdateExpression="SET #r = :role",
        ExpressionAttributeNames={"#r": "role"},
        ExpressionAttributeValues={":role": "owner"},
    )
    table.update_item(
        Key={"workspaceId": ws_id, "sk": f"MEMBER#{user_id}"},
        UpdateExpression="SET #r = :role",
        ExpressionAttributeNames={"#r": "role"},
        ExpressionAttributeValues={":role": "admin"},
    )
    table.update_item(
        Key={"workspaceId": ws_id, "sk": "META"},
        UpdateExpression="SET owner_id = :oid, updated_at = :now",
        ExpressionAttributeValues={":oid": new_owner_id, ":now": now},
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

    # Two writes instead of transact_write_items: botocore re-serializes
    # typed AttributeValue dicts through the resource-layer client, producing
    # nested {"M": {"S": ...}} and a ValidationError. Use native Python values
    # via table.put_item / delete_item. If we crash between the two the user
    # can re-accept — idempotent because of the member-exists check above.
    table.put_item(Item={
        "workspaceId": ws_id,
        "sk": f"MEMBER#{user_id}",
        "userId": user_id,
        "role": role,
        "joined_at": now,
    })
    table.delete_item(Key={"workspaceId": ws_id, "sk": f"INVITE#{token}"})

    return success({
        "workspaceId": ws_id,
        "role": role,
        "joined_at": now,
    })
