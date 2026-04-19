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

from shared.config import AGENTS_TABLE, REGION, SPANS_LOG_GROUP
from shared.middleware import auth_check
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
    # Claude 3.5 family
    "claude-3-5-sonnet":  ( 3.00, 15.00),
    "claude-3-5-haiku":   ( 0.80,  4.00),
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
                })
        else:
            raise
    return [a for a in out if a["agentId"]]


def _per_agent_totals_query() -> str:
    """Return an Insights query that produces per-agent call + token totals.

    Token fields may be absent on some accounts — coalesce with 0 so the stats
    row is produced regardless. Filter to AgentCore.Runtime.Invoke spans so
    we count invocations (not every trace child).
    """
    return """
fields attributes.aws.agent.id as agentRuntimeId,
       coalesce(attributes.gen_ai.usage.input_tokens, 0) as inTok,
       coalesce(attributes.gen_ai.usage.output_tokens, 0) as outTok
| filter name = "AgentCore.Runtime.Invoke" and ispresent(agentRuntimeId)
| stats count(*) as calls,
        sum(inTok) as inputTokens,
        sum(outTok) as outputTokens
        by agentRuntimeId
""".strip()


def _timeseries_query(bucket: str) -> str:
    return f"""
fields attributes.aws.agent.id as agentRuntimeId,
       coalesce(attributes.gen_ai.usage.input_tokens, 0) as inTok,
       coalesce(attributes.gen_ai.usage.output_tokens, 0) as outTok
| filter name = "AgentCore.Runtime.Invoke" and ispresent(agentRuntimeId)
| stats count(*) as calls,
        sum(inTok) as inputTokens,
        sum(outTok) as outputTokens
        by bin({bucket}) as bucket, agentRuntimeId
| sort bucket asc
""".strip()


def _single_agent_query(agent_id: str) -> str:
    # agent_id is validated upstream via validate_id().
    return f"""
fields coalesce(attributes.gen_ai.usage.input_tokens, 0) as inTok,
       coalesce(attributes.gen_ai.usage.output_tokens, 0) as outTok
| filter name = "AgentCore.Runtime.Invoke" and attributes.aws.agent.id = "{agent_id}"
| stats count(*) as calls,
        sum(inTok) as inputTokens,
        sum(outTok) as outputTokens
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

    # Per-agent rollup
    try:
        rows = _run_query(_per_agent_totals_query(), start, end)
    except ClientError as e:
        logger.exception("per-agent cost query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        rows = []

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
        calls = _to_int(_field(row, "calls"))
        in_tok = _to_int(_field(row, "inputTokens"))
        out_tok = _to_int(_field(row, "outputTokens"))
        cost = _compute_cost(in_tok, out_tok, info["model_id"])
        per_agent.append({
            "agentId": rid,
            "name": info["name"],
            "modelId": info["model_id"],
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
            "calls": 0,
            "inputTokens": 0,
            "outputTokens": 0,
            "costUsd": 0.0,
        })

    per_agent.sort(key=lambda a: a["costUsd"], reverse=True)

    # Timeseries
    try:
        ts_rows = _run_query(_timeseries_query(bucket), start, end)
    except ClientError:
        ts_rows = []

    # bucket -> {calls, cost}
    buckets: dict[str, dict] = {}
    for row in ts_rows:
        rid = _field(row, "agentRuntimeId")
        if not rid or rid not in by_id:
            continue
        bkt = _field(row, "bucket") or ""
        if not bkt:
            continue
        calls = _to_int(_field(row, "calls"))
        in_tok = _to_int(_field(row, "inputTokens"))
        out_tok = _to_int(_field(row, "outputTokens"))
        cost = _compute_cost(in_tok, out_tok, by_id[rid]["model_id"])
        slot = buckets.setdefault(bkt, {"calls": 0, "cost": 0.0})
        slot["calls"] += calls
        slot["cost"] += cost

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
        logger.exception("single-agent cost query failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        rows = []

    calls = in_tok = out_tok = 0
    for row in rows:
        calls += _to_int(_field(row, "calls"))
        in_tok += _to_int(_field(row, "inputTokens"))
        out_tok += _to_int(_field(row, "outputTokens"))

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
