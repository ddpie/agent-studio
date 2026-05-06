"""Cost / usage aggregation endpoints.

Reads OTEL spans from the `aws/spans` log group via CloudWatch Logs Insights
and joins with the `agent-studio-agents` DynamoDB table to produce per-agent
and workspace-level totals.

Two endpoints:
- GET /api/workspaces/{ws}/costs                 — workspace roll-up + timeseries
- GET /api/workspaces/{ws}/agents/{id}/costs     — single agent roll-up

Important: in the current account, runtime-level spans (`AgentCore.Runtime.Invoke`)
do NOT carry `gen_ai.usage.input_tokens` / `gen_ai.usage.output_tokens` — only
session / agent id / duration. We still try to pull token attrs in case an
account has detailed genai telemetry enabled; if absent, tokens default to 0
and `costUsd` falls back to 0. The UI disclaims that costs are estimates.

Pricing table below is kept small + opinionated; `_unit_price()` returns
`(0, 0)` for any model we don't price, making cost 0 rather than wrong.

Insights cap: 10s timeout + 10k rows per query (spec).
"""
import time
from typing import Any

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.config import AGENTS_TABLE, REGION, SPANS_LOG_GROUP, WORKSPACES_TABLE
from shared.middleware import auth_check, check_platform_admin
from shared.response import success, forbidden, bad_request, internal_error
from shared.validators import validate_id

router = Router()
logger = Logger(child=True)

_logs = None
_agents_table = None


def _get_logs():
    global _logs
    if _logs is None:
        _logs = boto3.client("logs", region_name=REGION)
    return _logs


def _get_agents_table():
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _agents_table


# ── Pricing ────────────────────────────────────────────────────────────────
# USD per 1M tokens, as of 2026-04-19. Rates are rough estimates published by
# Anthropic / AWS; Bedrock pricing may differ slightly and is not included
# here for KB retrieval, compute, etc. The UI surfaces that caveat.
PRICING: dict[str, tuple[float, float]] = {
    # Claude 4.x family (Anthropic Opus/Sonnet/Haiku 4.x on Bedrock)
    "claude-opus-4-7":    (15.00, 75.00),
    "claude-sonnet-4-6":  ( 3.00, 15.00),
    "claude-haiku-4-5":   ( 0.80,  4.00),
    # Claude 4.0 base (sonnet-4-20250514, opus-4-20250514, etc.)
    "claude-opus-4":      (15.00, 75.00),
    "claude-sonnet-4":    ( 3.00, 15.00),
    "claude-haiku-4":     ( 0.80,  4.00),
    # Claude 3.5 family
    "claude-3-5-sonnet":  ( 3.00, 15.00),
    "claude-3-5-haiku":   ( 0.80,  4.00),
    # Claude 3 family (legacy)
    "claude-3-opus":      (15.00, 75.00),
    "claude-3-sonnet":    ( 3.00, 15.00),
    "claude-3-haiku":     ( 0.25,  1.25),
    # Amazon Nova
    "amazon-nova-pro":    ( 0.80,  3.20),
    "amazon-nova-lite":   ( 0.06,  0.24),
    "amazon-nova-micro":  ( 0.035, 0.14),
}


def _unit_price(model_id: str) -> tuple[float, float]:
    """Map a Bedrock model-id-ish string to (input_per_mtok, output_per_mtok).

    Accepts common variants: `anthropic.claude-sonnet-4-6-20260301-v1:0`,
    `us.anthropic.claude-haiku-4-5`, `amazon.nova-pro-v1:0`, plain short
    names, etc. Returns (0, 0) when unknown so unpriced rows don't fabricate
    a cost.
    """
    if not model_id:
        return (0.0, 0.0)
    m = model_id.lower()
    # direct hits first
    for key, rates in PRICING.items():
        if key in m:
            return rates
    # Nova common short forms (nova.pro, nova-pro)
    if "nova-pro" in m or "nova.pro" in m:
        return PRICING["amazon-nova-pro"]
    if "nova-lite" in m or "nova.lite" in m:
        return PRICING["amazon-nova-lite"]
    if "nova-micro" in m or "nova.micro" in m:
        return PRICING["amazon-nova-micro"]
    return (0.0, 0.0)


def _compute_cost(input_tokens: int, output_tokens: int, model_id: str) -> float:
    in_rate, out_rate = _unit_price(model_id)
    return (input_tokens / 1_000_000.0) * in_rate + (output_tokens / 1_000_000.0) * out_rate


# ── Logs Insights helpers ──────────────────────────────────────────────────

QUERY_TIMEOUT_S = 10
MAX_ROWS = 10_000


def _run_query(query: str, start_epoch: int, end_epoch: int,
               timeout_s: int = QUERY_TIMEOUT_S) -> list:
    """Start + wait for a Logs Insights query. Returns partial results on
    timeout and [] on hard failure (never raises for callers)."""
    logs = _get_logs()
    try:
        q_id = logs.start_query(
            logGroupNames=[SPANS_LOG_GROUP],
            startTime=start_epoch,
            endTime=end_epoch,
            queryString=query,
            limit=MAX_ROWS,
        )["queryId"]
    except ClientError as e:
        logger.warning("start_query failed",
                       extra={"error_code": e.response.get("Error", {}).get("Code")})
        return []

    deadline = time.time() + timeout_s
    last_results: list = []
    while time.time() < deadline:
        resp = logs.get_query_results(queryId=q_id)
        status = resp.get("status")
        last_results = resp.get("results", last_results)
        if status == "Complete":
            return last_results
        if status in ("Failed", "Cancelled"):
            logger.warning("insights query non-complete",
                           extra={"query_status": status})
            return last_results
        time.sleep(0.3)
    try:
        logs.stop_query(queryId=q_id)
    except ClientError:
        pass
    return last_results


def _field(row: list, name: str):
    for kv in row:
        if kv.get("field") == name:
            return kv.get("value")
    return None


def _to_int(v: Any) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def _to_float(v: Any) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


# ── Range helpers ──────────────────────────────────────────────────────────

def _parse_range(q: dict | None) -> tuple[int, int, str]:
    """Return (start_epoch, end_epoch, bucket) for ?range=24h|7d|30d.

    bucket is the Insights `bin()` unit used for timeseries aggregation.
    """
    now = int(time.time())
    r = (q or {}).get("range", "7d")
    if r == "24h" or r == "hourly":
        return now - 24 * 3600, now, "1h"
    if r == "30d":
        return now - 30 * 86400, now, "1d"
    # default: 7d
    return now - 7 * 86400, now, "1d"


# ── Span aggregation ───────────────────────────────────────────────────────

def _agent_ids_for_workspace(workspace_id: str) -> list[dict]:
    """Return [{agentId, name, model_id}] for every agent in the workspace."""
    table = _get_agents_table()
    out: list[dict] = []
    try:
        # Single Query when possible; fall back to scan if GSI missing. The
        # agents table has a `workspace_id` GSI (see database.ts).
        resp = table.query(
            IndexName="workspace-index",
            KeyConditionExpression=Key("workspace_id").eq(workspace_id),
        )
        for it in resp.get("Items", []):
            out.append({
                "agentId": it.get("agentId", ""),
                "name": it.get("display_name") or it.get("name") or it.get("agentId", ""),
                "model_id": it.get("model_id") or it.get("default_model_id") or "",
                "status": it.get("status") or "active",
            })
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ValidationException":
            # GSI name differs in this env; fall back to a filtered scan.
            logger.info("workspace_id-index missing, scanning", extra={"err": code})
            resp = table.scan(
                FilterExpression=Key("workspace_id").eq(workspace_id),
            )
            for it in resp.get("Items", []):
                out.append({
                    "agentId": it.get("agentId", ""),
                    "name": it.get("display_name") or it.get("name") or it.get("agentId", ""),
                    "model_id": it.get("model_id") or it.get("default_model_id") or "",
                    "status": it.get("status") or "active",
                })
        else:
            raise
    return [a for a in out if a["agentId"]]


def _per_agent_totals_query() -> str:
    """Per-agent call + token totals.

    Token totals come from gen_ai `chat` child spans (carry usage.* attrs).
    Call count uses a separate pass below since Insights doesn't aggregate
    boolean expressions reliably across fields.
    """
    return """
fields resource.attributes.service.name as agentRuntimeId,
       coalesce(attributes.gen_ai.usage.input_tokens, 0) as inTok,
       coalesce(attributes.gen_ai.usage.output_tokens, 0) as outTok
| filter ispresent(agentRuntimeId) and ispresent(attributes.gen_ai.usage.input_tokens)
| stats sum(inTok) as inputTokens,
        sum(outTok) as outputTokens
        by agentRuntimeId
""".strip()


def _per_agent_calls_query() -> str:
    """Invocation count per agent. Top-level agent span = one user-facing call."""
    return """
fields resource.attributes.service.name as agentRuntimeId
| filter name = "invoke_agent Strands Agents" and ispresent(agentRuntimeId)
| stats count(*) as calls by agentRuntimeId
""".strip()


def _timeseries_query(bucket: str) -> str:
    return f"""
fields resource.attributes.service.name as agentRuntimeId,
       coalesce(attributes.gen_ai.usage.input_tokens, 0) as inTok,
       coalesce(attributes.gen_ai.usage.output_tokens, 0) as outTok
| filter ispresent(agentRuntimeId) and ispresent(attributes.gen_ai.usage.input_tokens)
| stats sum(inTok) as inputTokens,
        sum(outTok) as outputTokens
        by bin({bucket}) as bucket, agentRuntimeId
| sort bucket asc
""".strip()


def _timeseries_calls_query(bucket: str) -> str:
    return f"""
fields resource.attributes.service.name as agentRuntimeId
| filter name = "invoke_agent Strands Agents" and ispresent(agentRuntimeId)
| stats count(*) as calls
        by bin({bucket}) as bucket, agentRuntimeId
| sort bucket asc
""".strip()


def _single_agent_query(agent_id: str) -> str:
    # agent_id is validated upstream via validate_id().
    return f"""
fields coalesce(attributes.gen_ai.usage.input_tokens, 0) as inTok,
       coalesce(attributes.gen_ai.usage.output_tokens, 0) as outTok
| filter resource.attributes.service.name = "{agent_id}" and ispresent(attributes.gen_ai.usage.input_tokens)
| stats sum(inTok) as inputTokens,
        sum(outTok) as outputTokens
""".strip()


def _single_agent_calls_query(agent_id: str) -> str:
    return f"""
fields @timestamp
| filter resource.attributes.service.name = "{agent_id}" and name = "invoke_agent Strands Agents"
| stats count(*) as calls
""".strip()


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/api/workspaces/<wsId>/costs")
def workspace_costs(wsId: str):
    _user_id, ws_id, _member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    qp = router.current_event.query_string_parameters or {}
    start, end, bucket = _parse_range(qp)

    # Build agent index for the workspace first; we'll join span rows to it.
    try:
        agents = _agent_ids_for_workspace(ws_id)
    except ClientError as e:
        logger.exception("agents list failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        return internal_error()

    by_id = {a["agentId"]: a for a in agents}
    if not by_id:
        return success({
            "agents": [],
            "workspace": {
                "totalCalls": 0,
                "totalCostUsd": 0.0,
                "totalInputTokens": 0,
                "totalOutputTokens": 0,
                "timeseries": [],
                "rangeStart": start,
                "rangeEnd": end,
                "bucket": bucket,
            },
        })

    # Per-agent rollup: tokens from gen_ai chat spans, calls from top-level
    # invoke_agent spans. Two independent queries because Insights can't
    # bucket both token-carrying (chat) and invoke-counting (agent) rows in
    # one pass without double-counting.
    try:
        rows = _run_query(_per_agent_totals_query(), start, end)
    except ClientError as e:
        logger.exception("per-agent token query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        rows = []

    try:
        call_rows = _run_query(_per_agent_calls_query(), start, end)
    except ClientError as e:
        logger.exception("per-agent calls query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        call_rows = []

    calls_by_id: dict[str, int] = {}
    for row in call_rows:
        rid = _field(row, "agentRuntimeId")
        if rid:
            calls_by_id[rid] = _to_int(_field(row, "calls"))

    per_agent: list[dict] = []
    total_calls = 0
    total_in = 0
    total_out = 0
    total_cost = 0.0

    for row in rows:
        rid = _field(row, "agentRuntimeId")
        if not rid or rid not in by_id:
            continue  # skip agents outside this workspace
        info = by_id[rid]
        in_tok = _to_int(_field(row, "inputTokens"))
        out_tok = _to_int(_field(row, "outputTokens"))
        calls = calls_by_id.get(rid, 0)
        cost = _compute_cost(in_tok, out_tok, info["model_id"])
        per_agent.append({
            "agentId": rid,
            "name": info["name"],
            "modelId": info["model_id"],
            "status": info.get("status", "active"),
            "calls": calls,
            "inputTokens": in_tok,
            "outputTokens": out_tok,
            "costUsd": round(cost, 6),
        })
        total_calls += calls
        total_in += in_tok
        total_out += out_tok
        total_cost += cost

    # Include zero-usage agents so the UI lists every agent in the workspace.
    seen = {a["agentId"] for a in per_agent}
    for rid, info in by_id.items():
        if rid in seen:
            continue
        per_agent.append({
            "agentId": rid,
            "name": info["name"],
            "modelId": info["model_id"],
            "status": info.get("status", "active"),
            "calls": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "costUsd": 0.0,
        })

    per_agent.sort(key=lambda a: a["costUsd"], reverse=True)

    # Timeseries: tokens + calls in separate queries
    try:
        ts_rows = _run_query(_timeseries_query(bucket), start, end)
    except ClientError:
        ts_rows = []
    try:
        ts_call_rows = _run_query(_timeseries_calls_query(bucket), start, end)
    except ClientError:
        ts_call_rows = []

    # bucket -> {calls, cost}
    buckets: dict[str, dict] = {}
    for row in ts_rows:
        rid = _field(row, "agentRuntimeId")
        if not rid or rid not in by_id:
            continue
        bkt = _field(row, "bucket") or ""
        if not bkt:
            continue
        in_tok = _to_int(_field(row, "inputTokens"))
        out_tok = _to_int(_field(row, "outputTokens"))
        cost = _compute_cost(in_tok, out_tok, by_id[rid]["model_id"])
        slot = buckets.setdefault(bkt, {"calls": 0, "cost": 0.0})
        slot["cost"] += cost

    for row in ts_call_rows:
        rid = _field(row, "agentRuntimeId")
        if not rid or rid not in by_id:
            continue
        bkt = _field(row, "bucket") or ""
        if not bkt:
            continue
        calls = _to_int(_field(row, "calls"))
        slot = buckets.setdefault(bkt, {"calls": 0, "cost": 0.0})
        slot["calls"] += calls

    timeseries = [
        {"bucket": b, "calls": v["calls"], "costUsd": round(v["cost"], 6)}
        for b, v in sorted(buckets.items())
    ]

    return success({
        "agents": per_agent,
        "workspace": {
            "totalCalls": total_calls,
            "totalCostUsd": round(total_cost, 6),
            "totalInputTokens": total_in,
            "totalOutputTokens": total_out,
            "timeseries": timeseries,
            "rangeStart": start,
            "rangeEnd": end,
            "bucket": bucket,
        },
    })


# ── Admin: cross-workspace rollup ──────────────────────────────────────────

_GLOBAL_AGENTS_CACHE = {"data": None, "expires": 0}
_GLOBAL_WORKSPACES_CACHE = {"data": None, "expires": 0}
_GLOBAL_CACHE_TTL = 60  # seconds — agent membership changes slowly


def _all_agents_by_runtime_id() -> dict:
    """Scan every agent row, index by agent runtime id (= DDB agentId).

    Cached 60s because /admin/costs is likely to be opened and refreshed
    by a human at a slower cadence than that. Attributes picked are the
    same set workspace_costs already surfaces per agent.
    """
    now = time.time()
    if _GLOBAL_AGENTS_CACHE["data"] and now < _GLOBAL_AGENTS_CACHE["expires"]:
        return _GLOBAL_AGENTS_CACHE["data"]
    table = _get_agents_table()
    out: dict = {}
    try:
        kwargs = {}
        while True:
            resp = table.scan(**kwargs)
            for it in resp.get("Items", []):
                aid = it.get("agentId")
                if not aid:
                    continue
                out[aid] = {
                    "agentId": aid,
                    "name": it.get("display_name") or it.get("name") or aid,
                    "model_id": it.get("model_id") or it.get("default_model_id") or "",
                    "workspace_id": it.get("workspace_id", ""),
                    "status": it.get("status") or "active",
                }
            last = resp.get("LastEvaluatedKey")
            if not last:
                break
            kwargs["ExclusiveStartKey"] = last
    except ClientError as e:
        logger.warning("scan(agents) failed", extra={"error_code": e.response.get("Error", {}).get("Code")})
    _GLOBAL_AGENTS_CACHE["data"] = out
    _GLOBAL_AGENTS_CACHE["expires"] = now + _GLOBAL_CACHE_TTL
    return out


def _all_workspaces_by_id() -> dict:
    """Scan every workspace META row, index by workspaceId -> {name}.

    Cached 60s like agent index. Only META items hold the display name;
    the rest of the workspace table is member rows.
    """
    now = time.time()
    if _GLOBAL_WORKSPACES_CACHE["data"] and now < _GLOBAL_WORKSPACES_CACHE["expires"]:
        return _GLOBAL_WORKSPACES_CACHE["data"]
    out: dict = {}
    try:
        ddb = boto3.resource("dynamodb", region_name=REGION)
        table = ddb.Table(WORKSPACES_TABLE)
        kwargs = {"FilterExpression": Key("sk").eq("META")}
        while True:
            resp = table.scan(**kwargs)
            for it in resp.get("Items", []):
                ws_id = it.get("workspaceId")
                if not ws_id:
                    continue
                out[ws_id] = {
                    "workspaceId": ws_id,
                    "name": it.get("name") or it.get("display_name") or ws_id,
                }
            last = resp.get("LastEvaluatedKey")
            if not last:
                break
            kwargs["ExclusiveStartKey"] = last
    except ClientError as e:
        logger.warning("scan(workspaces) failed", extra={"error_code": e.response.get("Error", {}).get("Code")})
    _GLOBAL_WORKSPACES_CACHE["data"] = out
    _GLOBAL_WORKSPACES_CACHE["expires"] = now + _GLOBAL_CACHE_TTL
    return out


@router.get("/api/admin/costs")
def admin_costs():
    """Cross-workspace cost rollup. Platform-admin only.

    One pass over the span log group (same two Insights queries as the
    per-workspace endpoint), then fan out by workspace on the application
    side via the agents-table index. Scan cost is the same as a single
    /workspaces/{ws}/costs call — Logs Insights charges by bytes scanned,
    which does not change with the groupby cardinality.
    """
    _user_id, is_admin, admin_err = check_platform_admin(router.current_event)
    if admin_err:
        return admin_err
    if not is_admin:
        return forbidden()

    qp = router.current_event.query_string_parameters or {}
    start, end, bucket = _parse_range(qp)

    agents_by_id = _all_agents_by_runtime_id()
    workspaces_by_id = _all_workspaces_by_id()

    # Same queries as workspace_costs but consumed without the
    # "filter by workspace" step — we want every agent so we can
    # attribute it server-side.
    try:
        token_rows = _run_query(_per_agent_totals_query(), start, end)
    except ClientError as e:
        logger.exception("admin per-agent token query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        token_rows = []
    try:
        call_rows = _run_query(_per_agent_calls_query(), start, end)
    except ClientError as e:
        logger.exception("admin per-agent calls query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        call_rows = []

    calls_by_id: dict[str, int] = {}
    for row in call_rows:
        rid = _field(row, "agentRuntimeId")
        if rid:
            calls_by_id[rid] = _to_int(_field(row, "calls"))

    # Build per-agent rows. Skip agents we have no DDB row for — those
    # are runtimes from deleted agents (or other products sharing the
    # span log group) and we can't reliably attribute them.
    per_agent: list[dict] = []
    grand_calls = 0
    grand_in = 0
    grand_out = 0
    grand_cost = 0.0

    for row in token_rows:
        rid = _field(row, "agentRuntimeId")
        if not rid:
            continue
        info = agents_by_id.get(rid)
        if not info:
            continue
        in_tok = _to_int(_field(row, "inputTokens"))
        out_tok = _to_int(_field(row, "outputTokens"))
        calls = calls_by_id.get(rid, 0)
        cost = _compute_cost(in_tok, out_tok, info["model_id"])
        per_agent.append({
            "agentId": rid,
            "name": info["name"],
            "modelId": info["model_id"],
            "workspaceId": info["workspace_id"],
            "status": info["status"],
            "calls": calls,
            "inputTokens": in_tok,
            "outputTokens": out_tok,
            "costUsd": round(cost, 6),
        })
        grand_calls += calls
        grand_in += in_tok
        grand_out += out_tok
        grand_cost += cost

    # Roll per-agent into per-workspace. Zero-usage workspaces included
    # so admin sees the full picture, including new/idle workspaces.
    workspaces: dict[str, dict] = {}
    for ws_id, info in workspaces_by_id.items():
        workspaces[ws_id] = {
            "workspaceId": ws_id,
            "name": info["name"],
            "agentCount": 0,
            "calls": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "costUsd": 0.0,
        }
    for info in agents_by_id.values():
        ws_id = info.get("workspace_id")
        if ws_id and ws_id in workspaces:
            workspaces[ws_id]["agentCount"] += 1
    for a in per_agent:
        ws_id = a.get("workspaceId")
        if not ws_id:
            continue
        ws_row = workspaces.get(ws_id)
        if not ws_row:
            # Agent references a workspace we don't know about (deleted
            # workspace META row with agents still alive) — surface it
            # under a synthetic entry so the numbers don't disappear.
            ws_row = {
                "workspaceId": ws_id,
                "name": f"(deleted: {ws_id[:12]})",
                "agentCount": 0,
                "calls": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "costUsd": 0.0,
            }
            workspaces[ws_id] = ws_row
        ws_row["calls"] += a["calls"]
        ws_row["inputTokens"] += a["inputTokens"]
        ws_row["outputTokens"] += a["outputTokens"]
        ws_row["costUsd"] += a["costUsd"]

    ws_list = sorted(workspaces.values(), key=lambda w: w["costUsd"], reverse=True)
    for w in ws_list:
        w["costUsd"] = round(w["costUsd"], 6)

    per_agent.sort(key=lambda a: a["costUsd"], reverse=True)

    return success({
        "workspaces": ws_list,
        "agents": per_agent,
        "totals": {
            "calls": grand_calls,
            "inputTokens": grand_in,
            "outputTokens": grand_out,
            "costUsd": round(grand_cost, 6),
        },
        "rangeStart": start,
        "rangeEnd": end,
        "bucket": bucket,
    })


@router.get("/api/workspaces/<wsId>/agents/<agentId>/costs")
def agent_costs(wsId: str, agentId: str):
    _user_id, ws_id, _member, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    table = _get_agents_table()
    item = table.get_item(Key={"agentId": agentId}, ConsistentRead=True).get("Item")
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    qp = router.current_event.query_string_parameters or {}
    start, end, _bucket = _parse_range(qp)

    model_id = item.get("model_id") or item.get("default_model_id") or ""

    try:
        rows = _run_query(_single_agent_query(agentId), start, end)
    except ClientError as e:
        logger.exception("single-agent token query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        rows = []
    try:
        call_rows = _run_query(_single_agent_calls_query(agentId), start, end)
    except ClientError:
        call_rows = []

    in_tok = out_tok = 0
    for row in rows:
        in_tok += _to_int(_field(row, "inputTokens"))
        out_tok += _to_int(_field(row, "outputTokens"))
    calls = 0
    for row in call_rows:
        calls += _to_int(_field(row, "calls"))

    cost = _compute_cost(in_tok, out_tok, model_id)

    return success({
        "agentId": agentId,
        "name": item.get("display_name") or item.get("name") or agentId,
        "modelId": model_id,
        "calls": calls,
        "inputTokens": in_tok,
        "outputTokens": out_tok,
        "costUsd": round(cost, 6),
        "rangeStart": start,
        "rangeEnd": end,
    })
