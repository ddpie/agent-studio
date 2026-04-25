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

The scheduler target role is reused from the agent readonly tier
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
# scheduler.amazonaws.com. Falls back to the agent readonly role
# name (matches meta-agent/tools/create_schedule.py). Ops note: if the
# fallback role lacks `scheduler.amazonaws.com` in its trust policy,
# create_schedule will fail at AWS — surfacing as a 500 here.
_SCHEDULER_TARGET_ROLE_ARN = os.environ.get("SCHEDULER_TARGET_ROLE_ARN", "")
_SCHEDULE_RUNNER_LAMBDA_ARN = os.environ.get("SCHEDULE_RUNNER_LAMBDA_ARN", "")

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


def _extract_prompt(sched: dict) -> str:
    """Best-effort extract of the user-authored prompt from a schedule.

    list_schedules doesn't include Target.Input — callers expecting
    `prompt` must first get_schedule(Name=...) and pass that item in.
    On any parse failure we silently return "" so the list endpoint
    never 500s on a malformed record.
    """
    try:
        inp = sched.get("Target", {}).get("Input")
        if not inp:
            return ""
        outer = json.loads(inp)
        inner = json.loads(outer.get("Payload") or "{}")
        return str(inner.get("prompt") or "")
    except (ValueError, TypeError, AttributeError):
        return ""


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
        "prompt": _extract_prompt(sched),
    }


def _iso(value) -> str:
    """Return an ISO-8601 string with a UTC tz suffix so browsers parse correctly.

    AWS SDK returns aware datetimes, but guard against naive ones.
    """
    if value is None:
        return ""
    try:
        import datetime as _dt
        if isinstance(value, _dt.datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=_dt.timezone.utc)
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
                sched_name = sched.get("Name", "")
                if not sched_name.startswith(prefix):
                    continue
                # list_schedules omits Target.Input; re-fetch so the
                # frontend can render/edit the original prompt. Best-
                # effort — on per-item failure fall back to the list row.
                try:
                    full = scheduler.get_schedule(Name=sched_name, GroupName="default")
                    items.append(_schedule_response(agentId, full))
                except ClientError:
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
    target_arn = _SCHEDULE_RUNNER_LAMBDA_ARN
    runtime_arn = _agent_arn(agentId)
    # EventBridge Scheduler substitutes `<aws.scheduler.scheduled-time>`
    # at dispatch with the fire time in ISO-8601 UTC (e.g.
    # "2026-04-19T10:30:00Z"). We bake it into a deterministic
    # session_id so the recent-runs view can filter spans by session
    # prefix without a separate lookup table. `__schedule_name` is a
    # belt-and-braces tag: the agent can also echo it into spans
    # if we later want server-side filtering.
    # AgentCore requires runtimeSessionId >= 33 chars. Using the schedule's
    # full name + fire time keeps it deterministic and well above the bound.
    session_id = f"sched-{suffix}-<aws.scheduler.scheduled-time>"
    inner_payload = {
        "prompt": prompt,
        "__schedule_name": full_name,
        "session_id": session_id,
        "workspace_id": ws_id,
    }
    # Target.Input for the Universal Target `aws-sdk:bedrockagentcore:
    # invokeAgentRuntime` maps top-level PascalCase keys to the API's
    # request shape. `RuntimeSessionId` is therefore a separate top-level
    # field — embedding it only inside `Payload` is NOT enough; without
    # this, the invoke either silently 400s or runs with a service-chosen
    # session id we can't correlate back to the schedule.
    payload = {
        "AgentRuntimeArn": runtime_arn,
        "RuntimeSessionId": session_id,
        "Payload": json.dumps(inner_payload),
    }
    try:
        resp = scheduler.create_schedule(
            Name=full_name,
            GroupName="default",
            ScheduleExpression=cron,
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": target_arn,
                "RoleArn": _SCHEDULER_TARGET_ROLE_ARN,
                "Input": json.dumps(payload),
                "RetryPolicy": {
                    "MaximumRetryAttempts": 0,
                },
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


@router.put("/api/workspaces/<wsId>/agents/<agentId>/schedules/<name>")
def update_schedule(wsId: str, agentId: str, name: str):
    """Partial update — EventBridge UpdateSchedule is full-replace, so
    we read the current schedule first and merge only the fields the
    caller supplied (cron/prompt/state). `Name` is immutable.
    """
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

    prefix = _name_prefix(agentId)
    if not name.startswith(prefix):
        return bad_request(f"schedule name must start with '{prefix}'")
    if not re.fullmatch(r"[0-9a-zA-Z_.\-]{1,64}", name):
        return bad_request("invalid schedule name")

    body = router.current_event.json_body or {}
    cron = body.get("cron")
    prompt = body.get("prompt")
    state = body.get("state")

    if cron is None and prompt is None and state is None:
        return bad_request("at least one of cron, prompt, state must be provided")

    if cron is not None:
        if not isinstance(cron, str):
            return bad_request("cron must be a string")
        cron = cron.strip()
        cron_err = _validate_cron(cron)
        if cron_err:
            return bad_request(cron_err)

    if prompt is not None:
        if not isinstance(prompt, str) or not prompt.strip():
            return bad_request("prompt must be a non-empty string")
        if len(prompt) > 4000:
            return bad_request("prompt must be 4000 characters or less")

    if state is not None:
        if state not in ("ENABLED", "DISABLED"):
            return bad_request("state must be 'ENABLED' or 'DISABLED'")

    scheduler = _get_scheduler()
    try:
        existing = scheduler.get_schedule(Name=name, GroupName="default")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            return not_found()
        logger.exception(
            "update_schedule get failed",
            extra={"agentId": agentId, "name": name, "code": code},
        )
        return internal_error()

    suffix = name[len(prefix):]
    existing_target = existing.get("Target", {}) or {}
    existing_input_raw = existing_target.get("Input") or "{}"
    try:
        existing_payload = json.loads(existing_input_raw)
    except ValueError:
        existing_payload = {}
    try:
        inner_payload = json.loads(existing_payload.get("Payload") or "{}")
    except ValueError:
        inner_payload = {}

    if prompt is not None:
        inner_payload["prompt"] = prompt
    # Re-derive name + session_id to keep parity with create_schedule
    # (a previous bad edit could have drifted these).
    inner_payload["__schedule_name"] = name
    inner_payload["session_id"] = f"sched-{suffix}-<aws.scheduler.scheduled-time>"
    inner_payload["workspace_id"] = ws_id

    runtime_arn = existing_payload.get("AgentRuntimeArn") or _agent_arn(agentId)
    new_input = json.dumps({
        "AgentRuntimeArn": runtime_arn,
        "RuntimeSessionId": inner_payload["session_id"],
        "Payload": json.dumps(inner_payload),
    })

    new_target = {
        "Arn": _SCHEDULE_RUNNER_LAMBDA_ARN or existing_target.get("Arn", ""),
        "RoleArn": existing_target.get("RoleArn") or _SCHEDULER_TARGET_ROLE_ARN,
        "Input": new_input,
        "RetryPolicy": {
            "MaximumRetryAttempts": 0,
        },
    }

    new_cron = cron if cron is not None else existing.get("ScheduleExpression", "")
    new_state = state if state is not None else existing.get("State", "ENABLED")
    flexible = existing.get("FlexibleTimeWindow") or {"Mode": "OFF"}

    update_kwargs: dict = {
        "Name": name,
        "GroupName": "default",
        "ScheduleExpression": new_cron,
        "FlexibleTimeWindow": flexible,
        "Target": new_target,
        "State": new_state,
    }
    description = existing.get("Description")
    if description:
        update_kwargs["Description"] = description

    try:
        scheduler.update_schedule(**update_kwargs)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        message = e.response.get("Error", {}).get("Message", code)
        if code == "ResourceNotFoundException":
            return not_found()
        if code == "ValidationException":
            return bad_request(message)
        logger.exception(
            "update_schedule failed",
            extra={"agentId": agentId, "name": name, "code": code},
        )
        return internal_error()

    try:
        refreshed = scheduler.get_schedule(Name=name, GroupName="default")
    except ClientError:
        refreshed = {**existing, "ScheduleExpression": new_cron, "State": new_state, "Target": new_target}

    return success(_schedule_response(agentId, refreshed))


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


@router.post("/api/workspaces/<wsId>/agents/<agentId>/schedules/<name>/run-now")
def run_schedule_now(wsId: str, agentId: str, name: str):
    """Debug trigger — fire the schedule immediately via a one-time
    EventBridge schedule (at-expression ~5s in the future). This mirrors
    the real cron path: same scheduler role, same universal target, same
    payload shape — so the Recent Runs view picks it up without special
    cases, and the API call returns in <1s (not blocked on agent runtime).
    """
    from datetime import datetime, timedelta, timezone
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

    prefix = _name_prefix(agentId)
    if not name.startswith(prefix):
        return bad_request(f"schedule name must start with '{prefix}'")
    if not re.fullmatch(r"[0-9a-zA-Z_.\-]{1,64}", name):
        return bad_request("invalid schedule name")

    if not _SCHEDULER_TARGET_ROLE_ARN:
        logger.error("SCHEDULER_TARGET_ROLE_ARN env var not configured")
        return internal_error("Scheduler target role not configured")

    scheduler = _get_scheduler()
    try:
        existing = scheduler.get_schedule(Name=name, GroupName="default")
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code == "ResourceNotFoundException":
            return not_found()
        logger.exception("get_schedule before run-now failed",
                         extra={"agentId": agentId, "name": name, "code": code})
        return internal_error()

    target = existing.get("Target", {}) or {}
    try:
        outer = json.loads(target.get("Input") or "{}")
        inner = json.loads(outer.get("Payload") or "{}")
    except (ValueError, TypeError):
        inner = {}
    prompt = inner.get("prompt") or ""
    if not prompt:
        return bad_request("schedule has no prompt to run")

    suffix = name[len(prefix):]
    ts = int(time.time())
    fire_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    fire_iso = fire_at.replace(microsecond=0).isoformat().replace("+00:00", "")
    session_id = f"sched-{suffix}-manual-{ts}"[:100]

    runtime_arn = _agent_arn(agentId)
    inner_payload = {
        "prompt": prompt,
        "__schedule_name": name,
        "__manual_trigger": True,
        "session_id": session_id,
        "workspace_id": ws_id,
    }
    target_input = {
        "AgentRuntimeArn": runtime_arn,
        "RuntimeSessionId": session_id,
        "Payload": json.dumps(inner_payload),
    }
    one_shot_name = f"{name[:45]}-run-{ts}"[:64]

    try:
        scheduler.create_schedule(
            Name=one_shot_name,
            GroupName="default",
            ScheduleExpression=f"at({fire_iso})",
            FlexibleTimeWindow={"Mode": "OFF"},
            ActionAfterCompletion="DELETE",
            Target={
                "Arn": _SCHEDULE_RUNNER_LAMBDA_ARN,
                "RoleArn": _SCHEDULER_TARGET_ROLE_ARN,
                "Input": json.dumps(target_input),
            },
            Description=f"Manual run of {name} (by {user_id})",
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        message = e.response.get("Error", {}).get("Message", code)
        logger.exception("run-now create_schedule failed",
                         extra={"agentId": agentId, "name": name, "code": code})
        if code == "ConflictException":
            return bad_request("another manual run is already queued")
        return internal_error(message or "run-now failed")

    return success({
        "sessionId": session_id,
        "invokedAt": ts,
        "scheduledFor": fire_iso,
        "oneShotName": one_shot_name,
    }, status_code=202)


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
    # Agent bakes session_id = sched-<suffix>-<scheduled-time> when
    # the scheduler fires. Fall back to matching `__schedule_name`
    # embedded in the prompt attribute so older records are still
    # surfaced (best-effort — exact fields depend on span shape).
    session_prefix = f"sched-{suffix}-"

    logs = _get_logs()
    now_s = int(time.time())
    start_s = now_s - _EXEC_LOOKBACK_HOURS * 3600

    def _run_query(query_string: str) -> list:
        """Run a Logs Insights query and return rows. Empty on timeout/failure."""
        try:
            q_id = logs.start_query(
                logGroupNames=[SPANS_LOG_GROUP],
                startTime=start_s,
                endTime=now_s,
                queryString=query_string,
            )["queryId"]
        except ClientError as e:
            logger.exception(
                "schedule executions start_query failed",
                extra={"agentId": agentId, "name": name,
                       "error_code": e.response.get("Error", {}).get("Code")},
            )
            return []
        deadline = time.time() + _EXEC_QUERY_TIMEOUT_S
        while time.time() < deadline:
            try:
                resp = logs.get_query_results(queryId=q_id)
            except ClientError as e:
                logger.exception(
                    "schedule executions get_query_results failed",
                    extra={"agentId": agentId, "name": name,
                           "error_code": e.response.get("Error", {}).get("Code")},
                )
                return []
            status = resp.get("status")
            if status == "Complete":
                return resp.get("results", []) or []
            if status in ("Failed", "Cancelled"):
                logger.warning(
                    "schedule executions query non-complete",
                    extra={"query_status": status, "name": name},
                )
                return []
            time.sleep(0.3)
        try:
            logs.stop_query(queryId=q_id)
        except ClientError:
            pass
        return []

    # Prefer the agent-tagged `agent_studio.session_id` (correct
    # across warm-container reuse) with fallback to AgentCore's managed
    # `attributes.session.id` so older runs still show up.
    primary_q = f"""
fields coalesce(attributes.agent_studio.session_id, attributes.session.id) as sessionId, resource.attributes.service.name as svc, name as spanName, status.code as statusCode, startTimeUnixNano, endTimeUnixNano, @message
| filter svc = "{agentId}" and ispresent(sessionId) and strcontains(sessionId, "{session_prefix}")
| stats min(startTimeUnixNano) as firstStartNs, max(endTimeUnixNano) as lastEndNs, count(*) as spanCount, max(statusCode) as worstStatus by sessionId
| sort firstStartNs desc
| limit {_MAX_EXECUTIONS}
""".strip()

    rows = _run_query(primary_q)

    # Fallback: schedules created before the deterministic session_id
    # wiring was baked in won't be caught by the prefix filter. Re-query
    # by `__schedule_name` (embedded in the payload and echoed in spans)
    # and merge — dedupe by sessionId.
    if not rows:
        fallback_q = f"""
fields coalesce(attributes.agent_studio.session_id, attributes.session.id) as sessionId, resource.attributes.service.name as svc, name as spanName, status.code as statusCode, startTimeUnixNano, endTimeUnixNano, @message
| filter svc = "{agentId}" and ispresent(sessionId) and strcontains(@message, "{name}")
| stats min(startTimeUnixNano) as firstStartNs, max(endTimeUnixNano) as lastEndNs, count(*) as spanCount, max(statusCode) as worstStatus by sessionId
| sort firstStartNs desc
| limit {_MAX_EXECUTIONS}
""".strip()
        rows = _run_query(fallback_q)

    executions: list[dict] = []
    seen_sids: set[str] = set()
    for row in rows:
        sid = _field(row, "sessionId")
        if not sid or sid in seen_sids:
            continue
        seen_sids.add(sid)
        # sessionId shape: sched-<suffix>-<iso-time>. The trailing
        # segment is the EventBridge Scheduler fire time in ISO-8601,
        # so we lift it straight out for display — much cheaper + more
        # precise than parsing @timestamp. Fallback rows may not match
        # the prefix, leave scheduled_time empty in that case.
        if sid.startswith(session_prefix) and len(sid) > len(session_prefix):
            scheduled_time = sid[len(session_prefix):]
        else:
            scheduled_time = ""
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
