"""Agent schedule CRUD endpoints (EventBridge Scheduler).

Schedules are created in the default group and named
`agent-studio-{agentId}-{suffix}` so that:

1. List operations can filter by name prefix without relying on tags.
2. IAM policies can scope Resource to
   `arn:aws:scheduler:*:*:schedule/default/agent-studio-*` (see infra
   constructs/api.ts).
3. Deletes cannot accidentally target a schedule that doesn't belong to
   the current agent — the prefix is enforced on every mutation.

The target is the agent's AgentCore runtime ARN, invoked with a
`{"prompt": "...", "__schedule_name": "...", "session_id": "..."}`
payload. Injecting the schedule name + a deterministic session_id
(built from the EventBridge Scheduler `<aws.scheduler.scheduled-time>`
context attribute) gives us a way to correlate a specific fire with
the OTEL span stream in `aws/spans`, powering the recent-runs view.

The scheduler target role is reused from the sub-agent readonly tier
(mirrors meta-agent/tools/create_schedule.py); redeploying a dedicated
scheduler role is deferred to infra.
"""
import os
import re
import json
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

_scheduler = None
_agents_table = None
_logs = None

# EventBridge Scheduler expects expressions in one of:
#   at(yyyy-mm-ddThh:mm:ss)   — one-time
#   rate(N units)             — rate-based
#   cron(m h dom mon dow year) — cron (6 fields, AWS-flavoured)
# We accept cron(...) and rate(...). "at(...)" is intentionally excluded
# for now because the UI is cron-focused.
_CRON_RE = re.compile(r"^cron\([^)]+\)$")
_RATE_RE = re.compile(r"^rate\(\s*\d+\s+(minute|minutes|hour|hours|day|days)\s*\)$")

_ACCOUNT_ID = os.environ.get("AGENT_STUDIO_ACCOUNT_ID", "")
_AGENTCORE_REGION = os.environ.get("AGENTCORE_REGION", REGION)
# Target role the scheduler assumes. Must have a trust policy for
# scheduler.amazonaws.com. Falls back to the sub-agent readonly role
# name (matches meta-agent/tools/create_schedule.py). Ops note: if the
# fallback role lacks `scheduler.amazonaws.com` in its trust policy,
# create_schedule will fail at AWS — surfacing as a 500 here.
_SCHEDULER_TARGET_ROLE_ARN = os.environ.get("SCHEDULER_TARGET_ROLE_ARN", "")

# Suffix allowed chars: Scheduler names are [0-9a-zA-Z-_.]{1,64}. We
# restrict to the narrower ID_PATTERN so the combined name stays safe.
_SUFFIX_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_MAX_SUFFIX_LEN = 32  # total name is "agent-studio-" (13) + agentId (<=36) + "-" + suffix


def _get_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = boto3.client("scheduler", region_name=REGION)
    return _scheduler


def _get_agents_table():
    global _agents_table
    if _agents_table is None:
        _agents_table = boto3.resource("dynamodb", region_name=REGION).Table(AGENTS_TABLE)
    return _agents_table


def _get_logs():
    global _logs
    if _logs is None:
        _logs = boto3.client("logs", region_name=REGION)
    return _logs


def _get_agent_item(agent_id: str) -> dict | None:
    resp = _get_agents_table().get_item(Key={"agentId": agent_id}, ConsistentRead=True)
    return resp.get("Item")


def _name_prefix(agent_id: str) -> str:
    return f"agent-studio-{agent_id}-"


def _build_full_name(agent_id: str, suffix: str) -> str:
    return f"{_name_prefix(agent_id)}{suffix}"


def _validate_cron(expr: str) -> str | None:
    """Return error message if invalid, else None."""
    if not expr or len(expr) > 256:
        return "cron expression is required (max 256 chars)"
    if _CRON_RE.match(expr) or _RATE_RE.match(expr):
        return None
    return (
        "Invalid schedule expression: must be cron(...) or "
        "rate(N minute|hour|day...)"
    )


def _validate_suffix(suffix: str) -> str | None:
    if not suffix:
        return "name is required"
    if len(suffix) > _MAX_SUFFIX_LEN:
        return f"name suffix must be {_MAX_SUFFIX_LEN} characters or less"
    if not _SUFFIX_RE.match(suffix):
        return "name must match [a-zA-Z0-9_-]+"
    return None


def _agent_arn(agent_id: str) -> str:
    return f"arn:aws:bedrock-agentcore:{_AGENTCORE_REGION}:{_ACCOUNT_ID}:runtime/{agent_id}"


def _schedule_response(agent_id: str, sched: dict) -> dict:
    """Shape a Scheduler API item for the frontend."""
    name = sched.get("Name", "")
    prefix = _name_prefix(agent_id)
    suffix = name[len(prefix):] if name.startswith(prefix) else name
    return {
        "name": name,
        "suffix": suffix,
        "cron": sched.get("ScheduleExpression", ""),
        "state": sched.get("State", ""),
        "arn": sched.get("Arn", ""),
        "groupName": sched.get("GroupName", "default"),
        "createdAt": _iso(sched.get("CreationDate")),
        "lastModifiedAt": _iso(sched.get("LastModificationDate")),
    }


def _iso(value) -> str:
    if value is None:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


@router.get("/api/workspaces/<wsId>/agents/<agentId>/schedules")
def list_schedules(wsId: str, agentId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    scheduler = _get_scheduler()
    prefix = _name_prefix(agentId)
    items: list[dict] = []
    try:
        paginator = scheduler.get_paginator("list_schedules")
        for page in paginator.paginate(NamePrefix=prefix, GroupName="default"):
            for sched in page.get("Schedules", []):
                if not sched.get("Name", "").startswith(prefix):
                    continue
                items.append(_schedule_response(agentId, sched))
    except ClientError as e:
        logger.exception(
            "list_schedules failed",
            extra={"agentId": agentId, "code": e.response.get("Error", {}).get("Code")},
        )
        return internal_error()

    return success({"schedules": items})


@router.post("/api/workspaces/<wsId>/agents/<agentId>/schedules")
def create_schedule(wsId: str, agentId: str):
    user_id, ws_id, _, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()
    if item.get("status") == "archived":
        return forbidden()

    body = router.current_event.json_body or {}
    raw_name = (body.get("name") or "").strip()
    cron = (body.get("cron") or "").strip()
    prompt = body.get("prompt") or ""

    # Accept either the bare suffix or the full "agent-studio-{id}-{suffix}"
    # name — normalise to suffix for validation.
    prefix = _name_prefix(agentId)
    suffix = raw_name[len(prefix):] if raw_name.startswith(prefix) else raw_name

    suffix_err = _validate_suffix(suffix)
    if suffix_err:
        return bad_request(suffix_err)
    cron_err = _validate_cron(cron)
    if cron_err:
        return bad_request(cron_err)
    if not isinstance(prompt, str) or not prompt.strip():
        return bad_request("prompt is required")
    if len(prompt) > 4000:
        return bad_request("prompt must be 4000 characters or less")

    if not _SCHEDULER_TARGET_ROLE_ARN:
        logger.error("SCHEDULER_TARGET_ROLE_ARN env var not configured")
        return internal_error("Scheduler target role not configured")

    full_name = _build_full_name(agentId, suffix)
    scheduler = _get_scheduler()
    universal_target_arn = "arn:aws:scheduler:::aws-sdk:bedrockagentcore:invokeAgentRuntime"
    runtime_arn = _agent_arn(agentId)
    # EventBridge Scheduler substitutes `<aws.scheduler.scheduled-time>`
    # at dispatch with the fire time in ISO-8601 UTC (e.g.
    # "2026-04-19T10:30:00Z"). We bake it into a deterministic
    # session_id so the recent-runs view can filter spans by session
    # prefix without a separate lookup table. `__schedule_name` is a
    # belt-and-braces tag: the sub-agent can also echo it into spans
    # if we later want server-side filtering.
    session_id = f"sched-{suffix}-<aws.scheduler.scheduled-time>"
    inner_payload = {
        "prompt": prompt,
        "__schedule_name": full_name,
        "session_id": session_id,
    }
    payload = {"AgentRuntimeArn": runtime_arn, "Payload": json.dumps(inner_payload)}
    try:
        resp = scheduler.create_schedule(
            Name=full_name,
            GroupName="default",
            ScheduleExpression=cron,
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": universal_target_arn,
                "RoleArn": _SCHEDULER_TARGET_ROLE_ARN,
                "Input": json.dumps(payload),
            },
            Description=f"Agent Studio schedule for {agentId} (by {user_id})",
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        message = e.response.get("Error", {}).get("Message", code)
        if code == "ConflictException":
            return bad_request("A schedule with that name already exists")
        if code in ("ValidationException",):
            return bad_request(message)
        logger.exception(
            "create_schedule failed", extra={"agentId": agentId, "name": full_name, "code": code}
        )
        return internal_error()

    return success(
        {
            "name": full_name,
            "suffix": suffix,
            "cron": cron,
            "arn": resp.get("ScheduleArn", ""),
            "groupName": "default",
        },
        status_code=201,
    )


@router.delete("/api/workspaces/<wsId>/agents/<agentId>/schedules/<name>")
def delete_schedule(wsId: str, agentId: str, name: str):
    user_id, ws_id, _, err = auth_check(router.current_event, min_role="editor", ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    # Enforce prefix — prevents deleting arbitrary schedules in the
    # account via path-parameter injection.
    prefix = _name_prefix(agentId)
    if not name.startswith(prefix):
        return bad_request(f"schedule name must start with '{prefix}'")
    # Defence-in-depth: Scheduler name charset is [0-9a-zA-Z-_.]{1,64}.
    if not re.fullmatch(r"[0-9a-zA-Z_.\-]{1,64}", name):
        return bad_request("invalid schedule name")

    scheduler = _get_scheduler()
    try:
        scheduler.delete_schedule(Name=name, GroupName="default")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            return not_found()
        logger.exception(
            "delete_schedule failed", extra={"agentId": agentId, "name": name, "code": code}
        )
        return internal_error()

    return success({"deleted": name})


# ---------------------------------------------------------------------------
# Recent executions (Logs Insights against aws/spans)
# ---------------------------------------------------------------------------


_MAX_EXECUTIONS = 50
_EXEC_QUERY_TIMEOUT_S = 10
_EXEC_LOOKBACK_HOURS = 7 * 24


def _field(row: list, name: str):
    for kv in row:
        if kv.get("field") == name:
            return kv.get("value")
    return None


def _status_from_code(code: str | None) -> str:
    """Map OTEL status codes to a UI-friendly bucket."""
    if not code:
        return "success"
    up = code.upper()
    if up in ("ERROR", "FAILED", "FAILURE"):
        return "failure"
    if up in ("UNSET", "RUNNING"):
        return "running"
    return "success"


@router.get("/api/workspaces/<wsId>/agents/<agentId>/schedules/<name>/executions")
def list_schedule_executions(wsId: str, agentId: str, name: str):
    user_id, ws_id, _, err = auth_check(router.current_event, ws_id=wsId)
    if err:
        return err
    id_err = validate_id(agentId, "agentId")
    if id_err:
        return bad_request(id_err)
    item = _get_agent_item(agentId)
    if not item or item.get("workspace_id") != ws_id:
        return forbidden()

    # Enforce prefix + charset to avoid query-string injection and to
    # prevent a caller from listing runs of a schedule that doesn't
    # belong to this agent.
    prefix = _name_prefix(agentId)
    if not name.startswith(prefix):
        return bad_request(f"schedule name must start with '{prefix}'")
    if not re.fullmatch(r"[0-9a-zA-Z_.\-]{1,64}", name):
        return bad_request("invalid schedule name")

    suffix = name[len(prefix):]
    # Sub-agent bakes session_id = sched-<suffix>-<scheduled-time> when
    # the scheduler fires. Fall back to matching `__schedule_name`
    # embedded in the prompt attribute so older records are still
    # surfaced (best-effort — exact fields depend on span shape).
    session_prefix = f"sched-{suffix}-"

    logs = _get_logs()
    now_s = int(time.time())
    q = f"""
fields attributes.session.id as sessionId, attributes.aws.agent.id as agentRuntimeId, name as spanName, status.code as statusCode, startTimeUnixNano, endTimeUnixNano, @message
| filter agentRuntimeId = "{agentId}" and ispresent(sessionId) and strcontains(sessionId, "{session_prefix}")
| stats min(startTimeUnixNano) as firstStartNs, max(endTimeUnixNano) as lastEndNs, count(*) as spanCount, max(statusCode) as worstStatus by sessionId
| sort firstStartNs desc
| limit {_MAX_EXECUTIONS}
""".strip()

    try:
        q_id = logs.start_query(
            logGroupNames=[SPANS_LOG_GROUP],
            startTime=now_s - _EXEC_LOOKBACK_HOURS * 3600,
            endTime=now_s,
            queryString=q,
        )["queryId"]
    except ClientError as e:
        logger.exception(
            "schedule executions start_query failed",
            extra={"agentId": agentId, "name": name,
                   "error_code": e.response.get("Error", {}).get("Code")},
        )
        return internal_error()

    rows: list = []
    deadline = time.time() + _EXEC_QUERY_TIMEOUT_S
    try:
        while time.time() < deadline:
            resp = logs.get_query_results(queryId=q_id)
            status = resp.get("status")
            if status == "Complete":
                rows = resp.get("results", [])
                break
            if status in ("Failed", "Cancelled"):
                logger.warning(
                    "schedule executions query non-complete",
                    extra={"query_status": status, "name": name},
                )
                rows = []
                break
            time.sleep(0.3)
        else:
            try:
                logs.stop_query(queryId=q_id)
            except ClientError:
                pass
    except ClientError as e:
        logger.exception(
            "schedule executions get_query_results failed",
            extra={"agentId": agentId, "name": name,
                   "error_code": e.response.get("Error", {}).get("Code")},
        )
        return internal_error()

    executions: list[dict] = []
    for row in rows:
        sid = _field(row, "sessionId")
        if not sid or not sid.startswith(session_prefix):
            continue
        # sessionId shape: sched-<suffix>-<iso-time>. The trailing
        # segment is the EventBridge Scheduler fire time in ISO-8601,
        # so we lift it straight out for display — much cheaper + more
        # precise than parsing @timestamp.
        scheduled_time = sid[len(session_prefix):] if len(sid) > len(session_prefix) else ""
        try:
            start_ns = int(_field(row, "firstStartNs") or "0")
            end_ns = int(_field(row, "lastEndNs") or start_ns)
        except ValueError:
            start_ns = 0
            end_ns = 0
        duration_ms = max(0, (end_ns - start_ns) // 1_000_000)
        worst_status = _field(row, "worstStatus") or ""
        executions.append({
            "sessionId": sid,
            "scheduledTime": scheduled_time,
            "startMs": start_ns // 1_000_000 if start_ns else None,
            "durationMs": duration_ms,
            "status": _status_from_code(worst_status),
            "statusCode": worst_status or None,
        })

    return success({
        "executions": executions,
        "scheduleName": name,
    })
