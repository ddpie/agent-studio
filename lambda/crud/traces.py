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
from datetime import datetime, timezone

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

# Aggregate stats tuning
_STATS_QUERY_TIMEOUT_S = 10
# 24h → 1h buckets (24 points); 7d → 6h buckets (28 points)
_STATS_RANGES = {
    "24h": {"hours": 24, "bin": "1h", "bucket_s": 3600},
    "7d": {"hours": 24 * 7, "bin": "6h", "bucket_s": 6 * 3600},
}


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


def _as_utc_iso(ts: str | None) -> str | None:
    """CloudWatch Logs Insights emits `@timestamp` as "YYYY-MM-DD HH:MM:SS.fff"
    in UTC with no tz suffix. Browsers parsing a naive string treat it as
    local time, so we normalise to `YYYY-MM-DDTHH:MM:SS.fffZ`.
    """
    if not ts:
        return ts
    s = ts.strip()
    if not s:
        return s
    if s.endswith("Z") or "+" in s[10:] or s.endswith("UTC"):
        return s
    return s.replace(" ", "T", 1) + "Z"


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

    # Two passes — the stats engine only lets us aggregate one level at a
    # time. Pass 1: session totals (spans + firstEvent). Pass 2: look up
    # invoke_agent / POST /invocations per session for latency + tokens +
    # status. We pass the same 24h window to both.
    list_q = f"""
fields @timestamp, attributes.session.id as sessionId, traceId, resource.attributes.service.name as svc
| filter svc = "{agentId}" and ispresent(sessionId)
| stats min(@timestamp) as firstEvent, count(*) as spanCount by sessionId, traceId
| sort firstEvent desc
| limit 50
""".strip()

    meta_q = f"""
fields attributes.session.id as sid, resource.attributes.service.name as svc, name as spanName, status.code as statusCode,
       attributes.gen_ai.request.model as model,
       attributes.gen_ai.usage.input_tokens as inputTokens,
       attributes.gen_ai.usage.output_tokens as outputTokens,
       attributes.gen_ai.usage.total_tokens as totalTokens,
       (durationNano / 1000000) as durMs
| filter svc = "{agentId}" and ispresent(sid) and (spanName = "invoke_agent Strands Agents" or spanName = "POST /invocations")
| stats max(model) as model,
        max(inputTokens) as inputTokens,
        max(outputTokens) as outputTokens,
        max(totalTokens) as totalTokens,
        max(durMs) as durMs,
        max(statusCode) as statusCode by sid
| limit 100
""".strip()

    try:
        rows = _run_query(list_q, hours=24)
    except ClientError as e:
        logger.exception("traces query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        return internal_error()

    try:
        meta_rows = _run_query(meta_q, hours=24, timeout_s=_STATS_QUERY_TIMEOUT_S)
    except ClientError:
        meta_rows = []

    meta_by_sid: dict[str, dict] = {}
    for row in meta_rows:
        sid = _field(row, "sid")
        if not sid:
            continue
        meta_by_sid[sid] = {
            "model": _field(row, "model"),
            "inputTokens": _to_int(_field(row, "inputTokens")) or None,
            "outputTokens": _to_int(_field(row, "outputTokens")) or None,
            "totalTokens": _to_int(_field(row, "totalTokens")) or None,
            "durationMs": _round_ms(_field(row, "durMs")),
            "status": _field(row, "statusCode") or "OK",
        }

    sessions = []
    for row in rows:
        sid = _field(row, "sessionId")
        if not sid:
            continue
        try:
            count = int(_field(row, "spanCount") or "0")
        except ValueError:
            count = 0
        meta = meta_by_sid.get(sid, {})
        sessions.append({
            "sessionId": sid,
            "traceId": _field(row, "traceId"),
            "firstEvent": _as_utc_iso(_field(row, "firstEvent")),
            "spanCount": count,
            "model": meta.get("model"),
            "totalTokens": meta.get("totalTokens"),
            "durationMs": meta.get("durationMs"),
            "status": meta.get("status") or "OK",
        })
    return success({"sessions": sessions})


@router.get("/api/workspaces/<wsId>/agents/<agentId>/traces/<sessionId>")
def get_session_trace(wsId: str, agentId: str, sessionId: str):
    # Powertools matches routes in registration order; this path would
    # swallow `/traces/stats` otherwise. Short-circuit and dispatch to
    # the stats handler when the literal collides with a reserved word.
    if sessionId == "stats":
        return get_trace_stats(wsId, agentId)

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


# Filter out framework / OTEL noise from the runtime log stream so what
# remains is the sub-agent's stdout (= the final user-facing output).
_OUTPUT_SKIP_PATTERNS = (
    re.compile(r"^\d{4}-\d{2}-\d{2} .* (INFO|WARNING|ERROR|DEBUG) "),
    re.compile(r"^WARNING:"),
    re.compile(r"^INFO:"),
    re.compile(r"^ERROR:"),
    re.compile(r"Failed to export logs batch"),
    re.compile(r'^\{"timestamp":'),
)


def _looks_like_framework_noise(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    for pat in _OUTPUT_SKIP_PATTERNS:
        if pat.search(s):
            return True
    return False


@router.get("/api/workspaces/<wsId>/agents/<agentId>/traces/<sessionId>/output")
def get_session_output(wsId: str, agentId: str, sessionId: str):
    """Return the agent's user-facing output text + per-session metrics.

    We read stdout-like lines from the sub-agent's runtime log stream
    (`/aws/bedrock-agentcore/runtimes/{agentId}-DEFAULT`) and aggregate
    usage from the OTEL spans in aws/spans.

    Kept separate from the span-tree endpoint so the heavy runtime-log
    lookup only runs when the UI needs output.
    """
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()
    if not sessionId or not _SESSION_ID_RE.match(sessionId):
        return bad_request("invalid sessionId")

    logs = _get_logs()

    # 1) Metrics from aws/spans — invoke_agent gives tokens + model;
    #    POST /invocations gives wall clock.
    meta_q = f"""
fields resource.attributes.service.name as svc, attributes.session.id as sid, name as spanName, status.code as statusCode,
       attributes.gen_ai.request.model as model,
       attributes.gen_ai.usage.input_tokens as inputTokens,
       attributes.gen_ai.usage.output_tokens as outputTokens,
       attributes.gen_ai.usage.total_tokens as totalTokens,
       (durationNano / 1000000) as durMs
| filter svc = "{agentId}" and sid = "{sessionId}" and (spanName = "invoke_agent Strands Agents" or spanName = "POST /invocations")
| stats max(model) as model,
        max(inputTokens) as inputTokens,
        max(outputTokens) as outputTokens,
        max(totalTokens) as totalTokens,
        max(durMs) as durMs,
        max(statusCode) as statusCode
""".strip()
    try:
        meta_rows = _run_query(meta_q, hours=24, timeout_s=_STATS_QUERY_TIMEOUT_S)
    except ClientError:
        meta_rows = []

    meta: dict = {"model": None, "inputTokens": None, "outputTokens": None,
                  "totalTokens": None, "durationMs": None, "status": "OK"}
    if meta_rows:
        row = meta_rows[0]
        meta["model"] = _field(row, "model")
        meta["inputTokens"] = _to_int(_field(row, "inputTokens")) or None
        meta["outputTokens"] = _to_int(_field(row, "outputTokens")) or None
        meta["totalTokens"] = _to_int(_field(row, "totalTokens")) or None
        meta["durationMs"] = _round_ms(_field(row, "durMs"))
        meta["status"] = _field(row, "statusCode") or "OK"

    # 2) Free-text output from the runtime log stream. Stream name shape:
    #    `2026/04/20/[runtime-logs-<sessionId>]<stream-uuid>`
    runtime_log_group = f"/aws/bedrock-agentcore/runtimes/{agentId}-DEFAULT"
    stream_prefix_fragment = f"runtime-logs-{sessionId}"
    output_lines: list[str] = []
    matched_stream: str | None = None
    try:
        streams = logs.describe_log_streams(
            logGroupName=runtime_log_group,
            orderBy="LastEventTime",
            descending=True,
            limit=50,
        ).get("logStreams", [])
        for s in streams:
            sname = s.get("logStreamName", "")
            if stream_prefix_fragment in sname:
                matched_stream = sname
                break
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        # Runtime log group may not exist yet for a freshly-deployed agent;
        # surface an empty output with 200 rather than 500 so the UI can
        # still show the metrics strip.
        if code == "ResourceNotFoundException":
            matched_stream = None
        else:
            logger.exception("describe_log_streams failed",
                             extra={"agentId": agentId, "code": code})
            return internal_error()

    if matched_stream:
        try:
            ev_resp = logs.get_log_events(
                logGroupName=runtime_log_group,
                logStreamName=matched_stream,
                startFromHead=True,
                limit=200,
            )
            for e in ev_resp.get("events", []):
                msg = (e.get("message") or "").rstrip()
                if not _looks_like_framework_noise(msg):
                    output_lines.append(msg)
        except ClientError as e:
            logger.warning("get_log_events failed",
                           extra={"agentId": agentId, "stream": matched_stream,
                                  "code": e.response.get("Error", {}).get("Code", "")})

    return success({
        "sessionId": sessionId,
        "output": "\n".join(output_lines).strip(),
        "hasOutput": bool(output_lines),
        "metrics": meta,
    })


def _to_float(v) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    # Logs Insights returns "null" string for missing aggregates
    if f != f:  # NaN
        return None
    return f


def _to_int(v, default: int = 0) -> int:
    f = _to_float(v)
    return int(f) if f is not None else default


def _round_ms(v) -> int | None:
    f = _to_float(v)
    return int(round(f)) if f is not None else None


@router.get("/api/workspaces/<wsId>/agents/<agentId>/traces/stats")
def get_trace_stats(wsId: str, agentId: str):
    """Aggregate latency / error / count stats over a rolling window.

    Query params:
      - range: "24h" (default) or "7d"

    Returns a compact payload suitable for the TracesTab stats strip.
    On partial/query timeout, returns best-effort data (possibly empty
    timeseries). Attributes that don't exist in this account's spans
    degrade to `null` rather than raising.
    """
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
    range_key = (qs.get("range") or "24h").lower()
    if range_key not in _STATS_RANGES:
        return bad_request("range must be '24h' or '7d'")
    cfg = _STATS_RANGES[range_key]

    # Top-line aggregates. Filter to the top-level runtime span only.
    # attributes.latency_ms is ms; we also pull durationNano as a fallback
    # (divided by 1e6) but prefer latency_ms when present.
    # Error count: status.code = "ERROR". Other values (OK/UNSET) count as success.
    # Identify spans by resource.attributes.service.name (what AgentCore
    # actually emits — `attributes.aws.agent.id` doesn't exist). Filter
    # to the top-level request span `POST /invocations` (SERVER kind);
    # durationNano is always present, latency_ms isn't, so compute ms
    # from duration.
    summary_q = f"""
fields resource.attributes.service.name as svc, name as spanName, kind, status.code as statusCode, (durationNano / 1000000) as durMs
| filter svc = "{agentId}" and spanName = "POST /invocations"
| fields (statusCode = "ERROR") as isError
| stats count(*) as total,
        sum(isError) as errors,
        avg(durMs) as avgMs,
        pct(durMs, 50) as p50,
        pct(durMs, 90) as p90,
        pct(durMs, 95) as p95,
        pct(durMs, 99) as p99
""".strip()

    try:
        summary_rows = _run_query(summary_q, hours=cfg["hours"], timeout_s=_STATS_QUERY_TIMEOUT_S)
    except ClientError as e:
        logger.exception("trace stats summary query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        return internal_error()

    count = 0
    error_count = 0
    latency = {"p50": None, "p90": None, "p95": None, "p99": None, "avg": None}
    if summary_rows:
        row = summary_rows[0]
        count = _to_int(_field(row, "total"))
        error_count = _to_int(_field(row, "errors"))
        latency = {
            "p50": _round_ms(_field(row, "p50")),
            "p90": _round_ms(_field(row, "p90")),
            "p95": _round_ms(_field(row, "p95")),
            "p99": _round_ms(_field(row, "p99")),
            "avg": _round_ms(_field(row, "avgMs")),
        }

    error_rate = (error_count / count) if count > 0 else 0.0

    # Time series — bucketed. Use @timestamp for bucketing.
    series_q = f"""
fields @timestamp, resource.attributes.service.name as svc, name as spanName, status.code as statusCode, (durationNano / 1000000) as durMs
| filter svc = "{agentId}" and spanName = "POST /invocations"
| fields (statusCode = "ERROR") as isError
| stats count(*) as c,
        sum(isError) as e,
        pct(durMs, 95) as p95 by bin({cfg['bin']}) as bucket
| sort bucket asc
""".strip()

    try:
        series_rows = _run_query(series_q, hours=cfg["hours"], timeout_s=_STATS_QUERY_TIMEOUT_S)
    except ClientError:
        # Partial result: keep summary but drop the sparkline.
        series_rows = []

    timeseries = []
    for row in series_rows:
        bucket_raw = _field(row, "bucket")
        if not bucket_raw:
            continue
        # Insights returns bucket as "YYYY-MM-DD HH:MM:SS.sss" (UTC).
        iso = bucket_raw.replace(" ", "T")
        if "." in iso:
            iso = iso.split(".", 1)[0]
        if not iso.endswith("Z"):
            # Best-effort: treat as UTC
            try:
                dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                iso = dt.isoformat().replace("+00:00", "Z")
            except ValueError:
                iso = bucket_raw
        timeseries.append({
            "bucket": iso,
            "count": _to_int(_field(row, "c")),
            "errors": _to_int(_field(row, "e")),
            "p95Ms": _round_ms(_field(row, "p95")),
        })

    return success({
        "range": range_key,
        "count": count,
        "errorCount": error_count,
        "errorRate": round(error_rate, 4),
        "latencyMs": latency,
        "timeseries": timeseries,
        "bucketSeconds": cfg["bucket_s"],
    })
