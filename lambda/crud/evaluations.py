"""Evaluator (LLM-as-Judge) — per-agent online config + read endpoints.

AgentCore enforces `serviceNames` in the CloudWatch data-source config to
be a list of exactly 1. Since `service.name` in spans is the agent
runtime id, one eval config maps to one agent. We key configs by
`{ws-prefix}_{agent-id}` and keep them in sync with agent CRUD.
"""

import hashlib
import re
import time

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

from shared.config import AGENTS_TABLE, EVALUATOR_ROLE_ARN, REGION, SPANS_LOG_GROUP
from shared.middleware import auth_check
from shared.response import bad_request, forbidden, internal_error, success
from shared.validators import validate_id

router = Router()
logger = Logger(child=True)

_control = None
_logs = None
_agents_table = None

EVAL_OUTPUT_LOG_GROUP_PREFIX = "/aws/bedrock-agentcore/evaluations/results/"

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


def _get_agent_item(agent_id: str) -> dict | None:
    resp = _get_agents_table().get_item(Key={"agentId": agent_id}, ConsistentRead=True)
    return resp.get("Item")


def _eval_config_name_for_agent(workspace_id: str, agent_id: str) -> str:
    """Deterministic per-agent config name.

    API regex: [a-zA-Z][a-zA-Z0-9_]{0,47} (max 48 chars). Strip
    non-alphanumeric, concatenate with a short hash of the full agent_id
    so two agents whose names share the first N chars after stripping
    (e.g. `CustomerServiceBotV1-x` vs `CustomerServiceBotV2-y`) can't
    collide into the same config name.

    Layout: `agentstudio_{ws16}_{agent14}_{hash6}` = 12+16+1+14+1+6 = 50,
    so agent is truncated to 13 chars to fit 48.
    """
    safe_ws = re.sub(r"[^A-Za-z0-9]", "", workspace_id)[:16]
    safe_agent = re.sub(r"[^A-Za-z0-9]", "", agent_id)[:13]
    agent_hash = hashlib.sha1(agent_id.encode("utf-8")).hexdigest()[:6]
    return f"agentstudio_{safe_ws}_{safe_agent}_{agent_hash}"[:48]


def _find_config_by_name(name: str) -> dict | None:
    """Return the matching config summary, or None if missing. Paginates."""
    client = _get_control()
    next_token: str | None = None
    while True:
        kwargs: dict = {"maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        resp = client.list_online_evaluation_configs(**kwargs)
        items = (
            resp.get("onlineEvaluationConfigs")
            or resp.get("onlineEvaluationConfigSummaries")
            or resp.get("items")
            or []
        )
        for item in items:
            item_name = item.get("onlineEvaluationConfigName") or item.get("name")
            if item_name == name:
                return item
        next_token = resp.get("nextToken")
        if not next_token:
            return None


def _runtime_log_group_for_agent(agent_id: str) -> str:
    """Agent's runtime log group — where ADOT writes gen_ai input/output
    messages as OTLP log records (agentic observability mode). Evaluators
    need both aws/spans (for spans) and this log group (for message events)
    to compute scores; omitting it yields LogEventMissingException and no
    output."""
    return f"/aws/bedrock-agentcore/runtimes/{agent_id}-DEFAULT"


def create_eval_config_for_agent(workspace_id: str, agent_id: str) -> str:
    """Idempotently create a per-agent OnlineEvaluationConfig.

    Returns the config name. Swallows ConflictException so callers can
    invoke from agent-create hooks without extra checks.
    """
    if not EVALUATOR_ROLE_ARN:
        logger.warning("EVALUATOR_ROLE_ARN not configured; skipping eval config creation")
        return ""

    name = _eval_config_name_for_agent(workspace_id, agent_id)
    try:
        _get_control().create_online_evaluation_config(
            onlineEvaluationConfigName=name,
            description=f"Online evaluation for agent {agent_id} (workspace {workspace_id})",
            rule={
                "samplingConfig": {"samplingPercentage": 100.0},
                "filters": [],
            },
            dataSourceConfig={
                "cloudWatchLogs": {
                    "logGroupNames": [
                        SPANS_LOG_GROUP,
                        _runtime_log_group_for_agent(agent_id),
                    ],
                    # AgentCore accepts exactly 1 service name. Use the agent
                    # id — that's what `resource.attributes.service.name`
                    # holds on spans.
                    "serviceNames": [agent_id],
                },
            },
            evaluators=DEFAULT_EVALUATORS,
            evaluationExecutionRoleArn=EVALUATOR_ROLE_ARN,
            enableOnCreate=True,
        )
        logger.info(
            "created online eval config",
            extra={"config_name": name, "agent_id": agent_id, "workspace_id": workspace_id},
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ConflictException":
            logger.info("eval config already exists", extra={"config_name": name})
            _ensure_runtime_log_group(name, agent_id)
        else:
            logger.exception(
                "create_online_evaluation_config failed", extra={"config_name": name, "error_code": code}
            )
            raise
    return name


def _ensure_runtime_log_group(config_name: str, agent_id: str) -> None:
    """Backfill: configs created before the runtime log group was added to
    the data source need to be updated so evaluators can read message events.
    Called from the idempotent create path; no-op when already present."""
    try:
        existing = _find_config_by_name(config_name)
    except ClientError:
        return
    if not existing:
        return
    cfg_id = existing.get("onlineEvaluationConfigId") or existing.get("id")
    if not cfg_id:
        return
    try:
        full = _get_control().get_online_evaluation_config(onlineEvaluationConfigId=cfg_id)
    except ClientError:
        return
    lgs = ((full.get("dataSourceConfig") or {}).get("cloudWatchLogs") or {}).get("logGroupNames") or []
    runtime_lg = _runtime_log_group_for_agent(agent_id)
    if runtime_lg in lgs:
        return
    try:
        _get_control().update_online_evaluation_config(
            onlineEvaluationConfigId=cfg_id,
            dataSourceConfig={
                "cloudWatchLogs": {
                    "logGroupNames": [SPANS_LOG_GROUP, runtime_lg],
                    "serviceNames": [agent_id],
                },
            },
        )
        logger.info(
            "backfilled runtime log group on eval config",
            extra={"config_name": config_name, "agent_id": agent_id},
        )
    except ClientError as e:
        logger.warning(
            "backfill runtime log group failed",
            extra={"config_name": config_name, "error_code": e.response.get("Error", {}).get("Code")},
        )


def delete_eval_config_for_agent(workspace_id: str, agent_id: str) -> None:
    """Tear down the agent's eval config. Safe when already missing."""
    if not EVALUATOR_ROLE_ARN:
        return
    name = _eval_config_name_for_agent(workspace_id, agent_id)
    existing = _find_config_by_name(name)
    if not existing:
        return
    cfg_id = existing.get("onlineEvaluationConfigId") or existing.get("id")
    try:
        _get_control().delete_online_evaluation_config(
            onlineEvaluationConfigId=cfg_id,
        )
        logger.info("deleted online eval config", extra={"config_name": name, "agent_id": agent_id})
    except ClientError as e:
        logger.warning(
            "delete_online_evaluation_config failed",
            extra={"config_name": name, "error_code": e.response.get("Error", {}).get("Code")},
        )


def sync_eval_config_for_agent(workspace_id: str, agent_id: str) -> None:
    """Create-if-missing hook for agent create. Kept as a separate
    function for symmetry with a future update path (e.g. disable on
    archive)."""
    create_eval_config_for_agent(workspace_id, agent_id)


# Backward-compat shim — earlier code called per-workspace; new callers
# should use the per-agent functions above. Fans out to all live agents
# in the workspace. Safe to call repeatedly.
def create_eval_config_for_workspace(workspace_id: str) -> str:
    table = _get_agents_table()
    try:
        resp = table.query(
            IndexName="workspace-index",
            KeyConditionExpression=Key("workspace_id").eq(workspace_id),
            ProjectionExpression="agentId,#s",
            ExpressionAttributeNames={"#s": "status"},
        )
    except ClientError as e:
        logger.warning(
            "workspace agent scan failed",
            extra={"workspace_id": workspace_id, "error_code": e.response.get("Error", {}).get("Code")},
        )
        return ""
    names: list[str] = []
    for item in resp.get("Items", []):
        aid = item.get("agentId")
        if not aid or item.get("status") == "archived":
            continue
        names.append(create_eval_config_for_agent(workspace_id, aid))
    return ",".join(n for n in names if n)


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
        logGroupNames=log_group_names[:20],
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
            logger.warning(
                "logs insights query non-complete", extra={"queryId": query_id, "query_status": status}
            )
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

    # This agent's dedicated eval config output log group.
    cfg_name = _eval_config_name_for_agent(ws_id, agentId)
    cfg = _find_config_by_name(cfg_name)
    if cfg is None:
        return success({"evaluations": []})

    cfg_id = cfg.get("onlineEvaluationConfigId") or cfg.get("id") or ""
    if not cfg_id:
        return success({"evaluations": []})

    target_lg = f"{EVAL_OUTPUT_LOG_GROUP_PREFIX}{cfg_id}"
    try:
        _get_logs().describe_log_groups(logGroupNamePrefix=target_lg, limit=1)
    except ClientError as e:
        logger.warning(
            "describe_log_groups failed for eval output",
            extra={"error_code": e.response.get("Error", {}).get("Code")},
        )

    now_ms = int(time.time() * 1000)
    start_ms = now_ms - 7 * 24 * 3600 * 1000

    # The AgentCore evaluator pipeline writes sessionId here using whatever
    # `attributes.session.id` it read off aws/spans — which is stale on
    # warm-container reuse. We can't swap this for agent_studio.session_id
    # at the evaluator-output layer (the evaluator emits the log, not us);
    # scores still attribute to the wrong session id until AgentCore fixes
    # its managed session-id injection. Tracked in the span-tagging fix.
    q = """
fields @timestamp,
       attributes.gen_ai.evaluation.name as evaluator,
       attributes.gen_ai.evaluation.score as score,
       attributes.session.id as sessionId,
       traceId,
       attributes.gen_ai.evaluation.reason as reason,
       attributes.error.type as errorType
| sort @timestamp desc
| limit 200
""".strip()

    try:
        rows = _run_logs_query(
            log_group_names=[target_lg],
            query=q,
            start_epoch=start_ms // 1000,
            end_epoch=now_ms // 1000,
        )
    except ClientError as e:
        logger.exception(
            "evaluations query failed",
            extra={"agentId": agentId, "error_code": e.response.get("Error", {}).get("Code")},
        )
        return internal_error()

    out = []
    errors = 0
    for row in rows:
        ts = _field(row, "@timestamp")
        ev = _field(row, "evaluator")
        sc_raw = _field(row, "score")
        error_type = _field(row, "errorType")
        if error_type:
            errors += 1
            continue
        try:
            sc = float(sc_raw) if sc_raw is not None else None
        except (TypeError, ValueError):
            sc = None
        if ev is None or sc is None:
            continue
        out.append(
            {
                "timestamp": ts,
                "evaluator": ev,
                "score": sc,
                "sessionId": _field(row, "sessionId"),
                "traceId": _field(row, "traceId"),
                "reason": _field(row, "reason"),
            }
        )

    resp_body: dict = {"evaluations": out}
    if errors and not out:
        resp_body["diagnostics"] = {
            "allFailed": True,
            "errorCount": errors,
            "hint": "AgentSpanMappingException — evaluators could not parse "
            "user_query from spans. This is a known Strands SDK / "
            "AgentCore compatibility issue being tracked upstream.",
        }
    return success(resp_body)


# ---------------------------------------------------------------------------
# Per-agent enable + status endpoints (Evaluations tab "Enable" button,
# plus the richer empty-state on the UI).
# ---------------------------------------------------------------------------


@router.post("/api/workspaces/<wsId>/agents/<agentId>/evaluations/enable")
def enable_agent_evaluations(wsId: str, agentId: str):
    _user_id, ws_id, _member, err = auth_check(
        router.current_event,
        min_role="editor",
        ws_id=wsId,
    )
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    if not EVALUATOR_ROLE_ARN:
        return internal_error("EVALUATOR_ROLE_ARN not configured")

    name = _eval_config_name_for_agent(ws_id, agentId)
    try:
        existing = _find_config_by_name(name)
        if existing is not None:
            return success({"configName": name, "status": "ALREADY_EXISTS"})
        create_eval_config_for_agent(ws_id, agentId)
        return success({"configName": name, "status": "CREATED"})
    except ClientError as e:
        logger.exception(
            "enable_agent_evaluations failed",
            extra={
                "workspaceId": wsId,
                "agentId": agentId,
                "error_code": e.response.get("Error", {}).get("Code"),
            },
        )
        return internal_error()


@router.get("/api/workspaces/<wsId>/agents/<agentId>/evaluations/status")
def get_agent_evaluations_status(wsId: str, agentId: str):
    _user_id, ws_id, _member, err = auth_check(
        router.current_event,
        min_role="viewer",
        ws_id=wsId,
    )
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    name = _eval_config_name_for_agent(ws_id, agentId)
    try:
        match = _find_config_by_name(name)
    except ClientError as e:
        logger.exception(
            "get_agent_evaluations_status failed",
            extra={
                "workspaceId": wsId,
                "agentId": agentId,
                "error_code": e.response.get("Error", {}).get("Code"),
            },
        )
        return internal_error()

    if match is None:
        return success(
            {
                "exists": False,
                "configName": name,
                "status": None,
                "executionStatus": None,
            }
        )

    status_val = match.get("status") or match.get("configStatus")
    exec_status = match.get("executionStatus") or match.get("evaluationExecutionStatus")
    return success(
        {
            "exists": True,
            "configName": name,
            "status": status_val,
            "executionStatus": exec_status,
        }
    )


# ---------------------------------------------------------------------------
# Legacy workspace-level endpoints — kept so UIs calling /workspaces/:id/
# evaluations/enable still work. They fan out to all agents in the ws.
# ---------------------------------------------------------------------------


@router.post("/api/workspaces/<wsId>/evaluations/enable")
def enable_workspace_evaluations(wsId: str):
    _user_id, ws_id, _member, err = auth_check(
        router.current_event,
        min_role="editor",
        ws_id=wsId,
    )
    if err:
        return err
    if not EVALUATOR_ROLE_ARN:
        return internal_error("EVALUATOR_ROLE_ARN not configured")
    created = create_eval_config_for_workspace(ws_id)
    return success({"status": "CREATED" if created else "NOOP"})


@router.get("/api/workspaces/<wsId>/evaluations/status")
def get_workspace_evaluations_status(wsId: str):
    """Legacy endpoint — returns aggregate status across all agents.

    Frontend should prefer the per-agent endpoint. Kept for backwards
    compatibility: returns exists=True when at least one agent in the
    workspace has a config.
    """
    _user_id, ws_id, _member, err = auth_check(
        router.current_event,
        min_role="viewer",
        ws_id=wsId,
    )
    if err:
        return err
    table = _get_agents_table()
    try:
        resp = table.query(
            IndexName="workspace-index",
            KeyConditionExpression=Key("workspace_id").eq(ws_id),
            ProjectionExpression="agentId,#s",
            ExpressionAttributeNames={"#s": "status"},
        )
    except ClientError:
        return success({"exists": False, "configName": "", "status": None, "executionStatus": None})
    any_active = False
    any_exists = False
    for item in resp.get("Items", []):
        aid = item.get("agentId")
        if not aid or item.get("status") == "archived":
            continue
        name = _eval_config_name_for_agent(ws_id, aid)
        match = _find_config_by_name(name)
        if match:
            any_exists = True
            if (match.get("status") or "") == "ACTIVE":
                any_active = True
    return success(
        {
            "exists": any_exists,
            "configName": "",
            "status": "ACTIVE" if any_active else ("CREATING" if any_exists else None),
            "executionStatus": "ENABLED" if any_active else None,
        }
    )
