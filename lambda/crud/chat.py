"""Chat session persistence — S3-backed, per-user within a workspace.

S3 layout:
    chat/{ws_id}/{user_id}/{agent_key}/sessions/{session_id}.json

Each session is one JSON blob containing the full transcript. Listing is done
via ListObjectsV2 + read-each; there is intentionally no sidecar index file so
there is no read-modify-write to worry about.

Tenancy: user_id is taken from the JWT, never from the request path or body,
so a caller cannot address another user's chat folder. The workspace ID is
path-bound and validated by auth_check the same way every other route does it.
"""
import json

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router

from shared.config import REGION, ASSETS_BUCKET
from shared.middleware import auth_check
from shared.response import success, not_found, bad_request, internal_error
from shared.validators import validate_id

router = Router()
logger = Logger(child=True)

# Hard cap. A chat session with 50 messages + tool calls typically sits under
# 500 KB; 4 MB is a defence-in-depth ceiling, not an expected size.
MAX_SESSION_SIZE = 4 * 1024 * 1024

_s3 = None


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _session_key(ws_id: str, user_id: str, agent_key: str, session_id: str) -> str:
    return f"chat/{ws_id}/{user_id}/{agent_key}/sessions/{session_id}.json"


def _session_prefix(ws_id: str, user_id: str, agent_key: str) -> str:
    return f"chat/{ws_id}/{user_id}/{agent_key}/sessions/"


def _summary(session: dict) -> dict:
    """Trim a session down to the fields the list view needs."""
    messages = session.get("messages") or []
    preview = ""
    for m in messages:
        content = (m.get("content") or "").strip()
        if content:
            preview = content[:200]
            break
    return {
        "id": session.get("id", ""),
        "agentKey": session.get("agentKey", ""),
        "title": session.get("title", ""),
        "modelId": session.get("modelId"),
        "createdAt": session.get("createdAt", 0),
        "updatedAt": session.get("updatedAt", 0),
        "messageCount": len(messages),
        "preview": preview,
    }


@router.get("/api/workspaces/<wsId>/chat/agents/<agentKey>/sessions")
def list_sessions(wsId: str, agentKey: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    key_err = validate_id(agentKey, "agentKey")
    if key_err:
        return bad_request(key_err)

    s3 = _get_s3()
    prefix = _session_prefix(ws_id, user_id, agentKey)

    summaries: list[dict] = []
    try:
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=ASSETS_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                try:
                    resp = s3.get_object(Bucket=ASSETS_BUCKET, Key=obj["Key"])
                    body = resp["Body"].read().decode("utf-8")
                    data = json.loads(body)
                    summaries.append(_summary(data))
                except Exception:
                    logger.exception("Skipping unreadable session %s", obj["Key"])
    except Exception:
        logger.exception("Failed to list chat sessions")
        return internal_error()

    summaries.sort(key=lambda s: s.get("updatedAt", 0), reverse=True)
    return success({"items": summaries})


@router.get("/api/workspaces/<wsId>/chat/agents/<agentKey>/sessions/<sessionId>")
def get_session(wsId: str, agentKey: str, sessionId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    for val, name in ((agentKey, "agentKey"), (sessionId, "sessionId")):
        id_err = validate_id(val, name)
        if id_err:
            return bad_request(id_err)

    s3 = _get_s3()
    key = _session_key(ws_id, user_id, agentKey, sessionId)
    try:
        resp = s3.get_object(Bucket=ASSETS_BUCKET, Key=key)
        body = resp["Body"].read().decode("utf-8")
        data = json.loads(body)
    except s3.exceptions.NoSuchKey:
        return not_found()
    except Exception:
        logger.exception("Failed to read chat session %s", key)
        return internal_error()

    return success(data)


@router.put("/api/workspaces/<wsId>/chat/agents/<agentKey>/sessions/<sessionId>")
def put_session(wsId: str, agentKey: str, sessionId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    for val, name in ((agentKey, "agentKey"), (sessionId, "sessionId")):
        id_err = validate_id(val, name)
        if id_err:
            return bad_request(id_err)

    body = router.current_event.json_body or {}
    if not isinstance(body, dict):
        return bad_request("Body must be an object")
    if not isinstance(body.get("messages"), list):
        return bad_request("messages must be an array")

    # Normalise: force caller-supplied identity fields to match the URL so
    # a stale frontend can never corrupt another user's or agent's folder.
    body["id"] = sessionId
    body["agentKey"] = agentKey

    content = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if len(content) > MAX_SESSION_SIZE:
        return bad_request(f"Session too large (max {MAX_SESSION_SIZE // (1024 * 1024)} MB)")

    s3 = _get_s3()
    key = _session_key(ws_id, user_id, agentKey, sessionId)
    try:
        s3.put_object(
            Bucket=ASSETS_BUCKET,
            Key=key,
            Body=content,
            ContentType="application/json",
        )
    except Exception:
        logger.exception("Failed to write chat session %s", key)
        return internal_error()

    return success({"id": sessionId})


@router.delete("/api/workspaces/<wsId>/chat/agents/<agentKey>/sessions/<sessionId>")
def delete_session(wsId: str, agentKey: str, sessionId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err

    for val, name in ((agentKey, "agentKey"), (sessionId, "sessionId")):
        id_err = validate_id(val, name)
        if id_err:
            return bad_request(id_err)

    s3 = _get_s3()
    key = _session_key(ws_id, user_id, agentKey, sessionId)
    try:
        s3.delete_object(Bucket=ASSETS_BUCKET, Key=key)
    except Exception:
        logger.exception("Failed to delete chat session %s", key)
        return internal_error()

    return success({"deleted": True})
