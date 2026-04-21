"""Runs — scheduled/manual execution history endpoints."""
import os
import re
import time
from datetime import datetime, timezone

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

RUNS_TABLE = os.environ.get("RUNS_TABLE", "")
S3_BUCKET = os.environ.get("S3_BUCKET", "")

_runs_table = None
_agents_table = None
_s3 = None

_TIMEOUT_THRESHOLD_S = 600  # 10 minutes
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _get_runs_table():
    global _runs_table
    if _runs_table is None:
        _runs_table = boto3.resource("dynamodb", region_name=REGION).Table(RUNS_TABLE)
    return _runs_table


def _get_agents_table():
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _agents_table


def _get_agent_item(agent_id: str) -> dict | None:
    resp = _get_agents_table().get_item(Key={"agentId": agent_id}, ConsistentRead=True)
    return resp.get("Item")


def _get_s3():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3", region_name=REGION)
    return _s3


def _generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    return _get_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": S3_BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )


def _is_stale_running(item: dict) -> bool:
    if item.get("status") != "running":
        return False
    started = item.get("startedAt", "")
    if not started:
        return True
    try:
        dt = datetime.fromisoformat(started.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).total_seconds() > _TIMEOUT_THRESHOLD_S
    except (ValueError, TypeError):
        return True


def _format_list_item(item: dict) -> dict:
    status = item.get("status", "running")
    if status == "running" and _is_stale_running(item):
        status = "timeout"
    refs = item.get("artifactRefs") or []
    return {
        "runId": item.get("runId"),
        "trigger": item.get("trigger"),
        "scheduleId": item.get("scheduleId"),
        "status": status,
        "input": item.get("input", ""),
        "model": item.get("model"),
        "totalTokens": item.get("totalTokens"),
        "durationMs": item.get("durationMs"),
        "artifactCount": len(refs),
        "startedAt": item.get("startedAt"),
        "completedAt": item.get("completedAt"),
    }


def _extract_filename(key: str) -> str:
    name = key.rsplit("/", 1)[-1]
    # Strip the uuid prefix: "abc123_report.pdf" -> "report.pdf"
    parts = name.split("_", 1)
    return parts[1] if len(parts) > 1 else name


@router.get("/api/workspaces/<wsId>/agents/<agentId>/runs")
def list_runs(wsId: str, agentId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    qs = router.current_event.query_string_parameters or {}
    limit = min(int(qs.get("limit", "50")), 100)
    next_token = qs.get("nextToken")

    kwargs = {
        "KeyConditionExpression": "agentId = :aid",
        "ExpressionAttributeValues": {":aid": agentId},
        "ScanIndexForward": False,
        "Limit": limit,
    }
    if next_token:
        import json as _json
        try:
            kwargs["ExclusiveStartKey"] = _json.loads(next_token)
        except (ValueError, TypeError):
            return bad_request("invalid nextToken")

    try:
        resp = _get_runs_table().query(**kwargs)
    except ClientError as e:
        logger.exception("runs query failed")
        return internal_error()

    runs = [_format_list_item(r) for r in resp.get("Items", [])]
    result = {"runs": runs}
    if resp.get("LastEvaluatedKey"):
        import json as _json
        result["nextToken"] = _json.dumps(resp["LastEvaluatedKey"])

    return success(result)


@router.get("/api/workspaces/<wsId>/agents/<agentId>/runs/<runId>")
def get_run(wsId: str, agentId: str, runId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    if not runId or not _RUN_ID_RE.match(runId):
        return bad_request("invalid runId")

    agent = _get_agent_item(agentId)
    if not agent or agent.get("workspace_id") != ws_id:
        return forbidden()

    try:
        resp = _get_runs_table().get_item(
            Key={"agentId": agentId, "runId": runId},
            ConsistentRead=True,
        )
    except ClientError:
        logger.exception("get run failed")
        return internal_error()

    item = resp.get("Item")
    if not item:
        return not_found()
    item_ws = item.get("workspaceId") or ""
    if item_ws and item_ws != ws_id:
        return forbidden()

    status = item.get("status", "running")
    if status == "running" and _is_stale_running(item):
        status = "timeout"

    output_ref = item.get("outputRef", "")
    output_url = None
    if output_ref:
        try:
            output_url = _generate_presigned_url(output_ref)
        except ClientError:
            logger.warning("presign failed", extra={"key": output_ref})

    refs = item.get("artifactRefs") or []
    artifact_list = [{"key": k, "filename": _extract_filename(k)} for k in refs]

    return success({
        "runId": item.get("runId"),
        "trigger": item.get("trigger"),
        "scheduleId": item.get("scheduleId"),
        "sessionId": item.get("sessionId"),
        "status": status,
        "input": item.get("input", ""),
        "outputUrl": output_url,
        "artifactRefs": artifact_list,
        "usage": {
            "promptTokens": item.get("promptTokens"),
            "completionTokens": item.get("completionTokens"),
            "totalTokens": item.get("totalTokens"),
        },
        "durationMs": item.get("durationMs"),
        "model": item.get("model"),
        "error": item.get("error"),
        "startedAt": item.get("startedAt"),
        "completedAt": item.get("completedAt"),
    })
