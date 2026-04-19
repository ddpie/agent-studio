"""Trace / OTEL span reader endpoints.

Two endpoints:
- GET /agents/{id}/traces — list recent sessions (session summary rows)
- GET /agents/{id}/traces/{sessionId} — full span tree for one session

Data source: CloudWatch log group `aws/spans` (stream `default`).
OTEL spans land there via the account's XRay → CloudWatch Logs trace
segment destination (already ACTIVE on this account).

Span schema (observed from live probe, 2026-04-19):
  {
    "resource": {"attributes": {"cloud.resource_id": "arn:aws:bedrock-agentcore:...:runtime/<id>/..."}},
    "traceId": "...",
    "spanId": "...",
    "name": "AgentCore.Runtime.Invoke",
    "startTimeUnixNano": 1776...,
    "endTimeUnixNano":   1776...,
    "attributes": {"session.id": "...", "aws.agent.id": "<runtime-id>", ...},
    "status": {"code": "OK"}
  }

Logs Insights accesses dotted attributes with dot notation, e.g.
`attributes.session.id`. `parentSpanId` may be absent on root spans.
"""
import re
import time

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError

from shared.config import AGENTS_TABLE, REGION, SPANS_LOG_GROUP
from shared.middleware import auth_check
from shared.response import success, forbidden, not_found, bad_request, internal_error
from shared.validators import validate_id

router = Router()
logger = Logger(child=True)

_logs = None
_agents_table = None

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,128}$")


def _get_logs():
    global _logs
    if _logs is None:
        _logs = boto3.client("logs", region_name=REGION)
    return _logs


def _get_agent_item(agent_id: str) -> dict | None:
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    resp = _agents_table.get_item(Key={"agentId": agent_id}, ConsistentRead=True)
    return resp.get("Item")


def _run_query(query: str, hours: int = 24, timeout_s: int = 15) -> list:
    logs = _get_logs()
    now_s = int(time.time())
    q_id = logs.start_query(
        logGroupNames=[SPANS_LOG_GROUP],
        startTime=now_s - hours * 3600,
        endTime=now_s,
        queryString=query,
    )["queryId"]
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = logs.get_query_results(queryId=q_id)
        status = resp.get("status")
        if status == "Complete":
            return resp.get("results", [])
        if status in ("Failed", "Cancelled"):
            logger.warning("logs query non-complete",
                           extra={"query_status": status})
            return []
        time.sleep(0.3)
    try:
        logs.stop_query(queryId=q_id)
    except ClientError:
        pass
    return []


def _field(row: list, name: str):
    for kv in row:
        if kv.get("field") == name:
            return kv.get("value")
    return None


@router.get("/api/workspaces/<wsId>/agents/<agentId>/traces")
def list_traces(wsId: str, agentId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    q = f"""
fields @timestamp, attributes.session.id as sessionId, traceId, attributes.aws.agent.id as agentRuntimeId
| filter agentRuntimeId = "{agentId}" and ispresent(sessionId)
| stats min(@timestamp) as firstEvent, count(*) as spanCount by sessionId, traceId
| sort firstEvent desc
| limit 50
""".strip()

    try:
        rows = _run_query(q, hours=24)
    except ClientError as e:
        logger.exception("traces query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        return internal_error()

    sessions = []
    for row in rows:
        sid = _field(row, "sessionId")
        if not sid:
            continue
        try:
            count = int(_field(row, "spanCount") or "0")
        except ValueError:
            count = 0
        sessions.append({
            "sessionId": sid,
            "traceId": _field(row, "traceId"),
            "firstEvent": _field(row, "firstEvent"),
            "spanCount": count,
        })
    return success({"sessions": sessions})


@router.get("/api/workspaces/<wsId>/agents/<agentId>/traces/<sessionId>")
def get_session_trace(wsId: str, agentId: str, sessionId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    # Defensive: sessionId is interpolated into the Logs Insights query
    # string. We whitelist a safe charset — UUIDs and typical ids fit.
    if not sessionId or not _SESSION_ID_RE.match(sessionId):
        return bad_request("invalid sessionId")

    q = f"""
fields spanId, parentSpanId, name, startTimeUnixNano, endTimeUnixNano, status.code as status, attributes.session.id as sessionId
| filter sessionId = "{sessionId}"
| sort startTimeUnixNano asc
| limit 500
""".strip()

    try:
        rows = _run_query(q, hours=24)
    except ClientError as e:
        logger.exception("session trace query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        return internal_error()

    if not rows:
        return not_found()

    spans_by_id: dict[str, dict] = {}
    for row in rows:
        sid = _field(row, "spanId")
        if not sid:
            continue
        try:
            start_ns = int(_field(row, "startTimeUnixNano") or "0")
            end_ns = int(_field(row, "endTimeUnixNano") or start_ns)
        except ValueError:
            continue
        parent = _field(row, "parentSpanId") or None
        spans_by_id[sid] = {
            "spanId": sid,
            "parentSpanId": parent,
            "name": _field(row, "name") or "",
            "startMs": start_ns // 1_000_000,
            "durationMs": max(0, (end_ns - start_ns) // 1_000_000),
            "status": _field(row, "status") or "OK",
            "children": [],
        }

    root = None
    orphans = []
    for sp in spans_by_id.values():
        parent = sp["parentSpanId"]
        if parent and parent in spans_by_id:
            spans_by_id[parent]["children"].append(sp)
        else:
            if root is None:
                root = sp
            else:
                orphans.append(sp)

    if root is None:
        # No parent resolution. Return a synthetic root wrapping everything.
        children = sorted(spans_by_id.values(), key=lambda s: s["startMs"])
        root = {
            "spanId": "__synthetic__",
            "parentSpanId": None,
            "name": f"session:{sessionId}",
            "startMs": children[0]["startMs"] if children else 0,
            "durationMs": 0,
            "status": "OK",
            "children": children,
        }
    elif orphans:
        # Attach any orphaned spans that had an unresolved parent at the root
        # so they're not lost in the UI.
        root["children"].extend(sorted(orphans, key=lambda s: s["startMs"]))

    return success({"root": root, "totalSpans": len(spans_by_id)})
