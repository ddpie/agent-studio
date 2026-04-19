"""Evaluator (LLM-as-Judge) — workspace-scoped online config + read endpoints."""
import re
import time

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError

from shared.config import AGENTS_TABLE, EVALUATOR_ROLE_ARN, REGION, SPANS_LOG_GROUP
from shared.middleware import auth_check
from shared.response import success, forbidden, bad_request, internal_error
from shared.validators import validate_id

router = Router()
logger = Logger(child=True)

_control = None
_logs = None
_agents_table = None

EVAL_OUTPUT_LOG_GROUP_PREFIX = "/aws/bedrock-agentcore/evaluations/results/"

# Keep small + opinionated for MVP. Users don't pick which evaluators fire;
# we bake in a reasonable default. Sprint 3 can surface per-workspace
# customization.
DEFAULT_EVALUATORS = [
    {"evaluatorId": "Builtin.Correctness"},
    {"evaluatorId": "Builtin.Helpfulness"},
    {"evaluatorId": "Builtin.GoalSuccessRate"},
]


def _get_control():
    global _control
    if _control is None:
        _control = boto3.client("bedrock-agentcore-control", region_name=REGION)
    return _control


def _config_name_for(workspace_id: str) -> str:
    """Deterministic per-workspace config name.

    API regex: [a-zA-Z][a-zA-Z0-9_]{0,47}. Must start with a letter,
    only alphanumeric + underscore, max 48 chars. Workspace UUIDs use
    hyphens which are invalid — strip them.
    """
    safe = re.sub(r"[^A-Za-z0-9]", "", workspace_id)[:32]
    return f"agentstudio_ws_{safe}"


def create_eval_config_for_workspace(workspace_id: str) -> str:
    """Fire-and-forget: create OnlineEvaluationConfig for a workspace.

    Returns the config name (also the idempotency handle). Safe to call
    repeatedly — if the config already exists we swallow ConflictException
    and return the name.

    Synchronous to the API (returns in <1s) but AgentCore creates the
    resource asynchronously (status=CREATING ~minutes). Caller must not
    block on READY.
    """
    if not EVALUATOR_ROLE_ARN:
        logger.warning("EVALUATOR_ROLE_ARN not configured; skipping eval config creation")
        return ""

    name = _config_name_for(workspace_id)
    try:
        _get_control().create_online_evaluation_config(
            onlineEvaluationConfigName=name,
            description=f"Online evaluation for Agent Studio workspace {workspace_id}",
            rule={
                "samplingConfig": {"samplingPercentage": 100.0},
                "filters": [],
            },
            dataSourceConfig={
                "cloudWatchLogs": {
                    "logGroupNames": [SPANS_LOG_GROUP],
                    "serviceNames": ["bedrock-agentcore"],
                },
            },
            evaluators=DEFAULT_EVALUATORS,
            evaluationExecutionRoleArn=EVALUATOR_ROLE_ARN,
            enableOnCreate=True,
        )
        logger.info("created online eval config", extra={"config_name": name, "workspace_id": workspace_id})
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ConflictException":
            logger.info("eval config already exists", extra={"config_name": name})
        else:
            logger.exception("create_online_evaluation_config failed",
                             extra={"config_name": name, "error_code": code})
            raise
    return name


# ---------------------------------------------------------------------------
# Read endpoints
# ---------------------------------------------------------------------------


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


def _run_logs_query(
    log_group_names: list[str],
    query: str,
    start_epoch: int,
    end_epoch: int,
    timeout_s: int = 15,
) -> list:
    """Start a Logs Insights query + wait for completion. [] on failure."""
    if not log_group_names:
        return []
    logs = _get_logs()
    query_id = logs.start_query(
        logGroupNames=log_group_names[:20],  # Insights max 20 groups per query
        startTime=start_epoch,
        endTime=end_epoch,
        queryString=query,
    )["queryId"]

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        resp = logs.get_query_results(queryId=query_id)
        status = resp.get("status")
        if status == "Complete":
            return resp.get("results", [])
        if status in ("Failed", "Cancelled"):
            logger.warning("logs insights query non-complete",
                           extra={"queryId": query_id, "query_status": status})
            return []
        time.sleep(0.5)
    try:
        logs.stop_query(queryId=query_id)
    except ClientError:
        pass
    return []


def _field(row: list, name: str):
    for kv in row:
        if kv.get("field") == name:
            return kv.get("value")
    return None


@router.get("/api/workspaces/<wsId>/agents/<agentId>/evaluations")
def get_agent_evaluations(wsId: str, agentId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err

    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)

    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    # Discover eval result log groups. AgentCore creates one per
    # OnlineEvaluationConfig at /aws/bedrock-agentcore/evaluations/results/<id>.
    logs = _get_logs()
    try:
        groups = logs.describe_log_groups(
            logGroupNamePrefix=EVAL_OUTPUT_LOG_GROUP_PREFIX,
        )
    except ClientError as e:
        logger.exception("describe_log_groups failed",
                         extra={"error_code": e.response.get("Error", {}).get("Code")})
        return internal_error()

    group_names = [g["logGroupName"] for g in groups.get("logGroups", [])]
    if not group_names:
        return success({"evaluations": []})

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 7 * 24 * 3600 * 1000

    # Logs Insights query. Keep fields flexible — the real message shape
    # may wrap these under OTEL attributes; fallback handled in parsing.
    q = f"""
fields @timestamp, evaluatorId as evaluator, score, sessionId, traceId, reason
| filter agentRuntimeId = "{agentId}" or contains(@message, "{agentId}")
| sort @timestamp desc
| limit 100
""".strip()

    try:
        rows = _run_logs_query(
            log_group_names=group_names,
            query=q,
            start_epoch=start_ms // 1000,
            end_epoch=now_ms // 1000,
        )
    except ClientError as e:
        logger.exception("evaluations query failed",
                         extra={"agentId": agentId,
                                "error_code": e.response.get("Error", {}).get("Code")})
        return internal_error()

    out = []
    for row in rows:
        ts = _field(row, "@timestamp")
        ev = _field(row, "evaluator")
        sc_raw = _field(row, "score")
        try:
            sc = float(sc_raw) if sc_raw is not None else None
        except (TypeError, ValueError):
            sc = None
        if ev is None or sc is None:
            continue
        out.append({
            "timestamp": ts,
            "evaluator": ev,
            "score": sc,
            "sessionId": _field(row, "sessionId"),
            "traceId": _field(row, "traceId"),
            "reason": _field(row, "reason"),
        })

    return success({"evaluations": out})
