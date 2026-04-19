"""Agent schedule CRUD endpoints (EventBridge Scheduler).

Schedules are created in the default group and named
`agent-studio-{agentId}-{suffix}` so that:

1. List operations can filter by name prefix without relying on tags.
2. IAM policies can scope Resource to
   `arn:aws:scheduler:*:*:schedule/default/agent-studio-*` (see infra
   constructs/api.ts).
3. Deletes cannot accidentally target a schedule that doesn't belong to
   the current agent — the prefix is enforced on every mutation.

The target is the agent's AgentCore runtime ARN, invoked with a static
`{"prompt": "..."}` payload. The scheduler target role is reused from
the sub-agent readonly tier (mirrors meta-agent/tools/create_schedule.py);
redeploying a dedicated scheduler role is deferred to infra.
"""
import os
import re
import json

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

_scheduler = None
_agents_table = None

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
    try:
        resp = scheduler.create_schedule(
            Name=full_name,
            GroupName="default",
            ScheduleExpression=cron,
            FlexibleTimeWindow={"Mode": "OFF"},
            Target={
                "Arn": _agent_arn(agentId),
                "RoleArn": _SCHEDULER_TARGET_ROLE_ARN,
                "Input": json.dumps({"prompt": prompt}),
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
