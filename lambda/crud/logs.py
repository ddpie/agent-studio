"""Agent runtime log viewer — inline CloudWatch Logs surface.

Wraps `logs:FilterLogEvents` on the sub-agent runtime log group so users
without AWS Console access can browse/search recent logs from the agent
detail page. Deep-link to CloudWatch is preserved as a power-user escape
hatch in the UI, but the main flow lives in-app.

Endpoint:
    GET /api/workspaces/{wsId}/agents/{agentId}/logs
        ?since=15m|1h|6h|24h
        &level=ALL|ERROR|WARN|INFO
        &search=<free-text>
        &cursor=<opaque>

Response:
    {
      "events": [{timestamp, message, level, logStream}],
      "nextCursor": "<opaque>"   # absent when no more pages
    }
"""
import re

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError

from shared.config import AGENTS_TABLE, REGION
from shared.middleware import auth_check
from shared.response import success, forbidden, bad_request, internal_error
from shared.validators import validate_id

router = Router()
logger = Logger(child=True)

_logs = None
_agents_table = None

# Cap per-page events. Matches the scope doc ("200 events per page").
MAX_EVENTS = 200

# Cap individual message length sent to the browser. 4096 is enough for
# the inline viewer; anything longer is tail-truncated with a marker.
MAX_MESSAGE_CHARS = 4096

# Valid time windows (minutes). Passed in as `since=15m|1h|6h|24h`.
_SINCE_MIN = {
    "15m": 15,
    "1h": 60,
    "6h": 360,
    "24h": 1440,
}

_VALID_LEVELS = {"ALL", "ERROR", "WARN", "INFO"}

# Matches messages that start with `[ERROR]` / `[WARN]` / `[INFO]` /
# `[DEBUG]`. Agents emit this prefix via the stream_utils helpers; if a
# message doesn't match, we leave `level` blank (UI shows a neutral badge).
_LEVEL_PREFIX_RE = re.compile(r"^\s*\[(ERROR|WARN|WARNING|INFO|DEBUG)\]", re.IGNORECASE)

# Search term: keep simple — letters/digits/spaces/basic punctuation. This
# is interpolated into the CloudWatch filter pattern so we reject anything
# exotic. Users who need regex can deep-link to CloudWatch.
_SEARCH_RE = re.compile(r"^[A-Za-z0-9_\-\.\s:/@]{1,200}$")


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


def _parse_level(message: str) -> str:
    """Extract `ERROR` / `WARN` / `INFO` / `DEBUG` from a leading `[LEVEL]`
    prefix. Returns empty string when no prefix is present — the UI renders
    a neutral badge in that case rather than guessing.
    """
    m = _LEVEL_PREFIX_RE.match(message or "")
    if not m:
        return ""
    lvl = m.group(1).upper()
    return "WARN" if lvl == "WARNING" else lvl


def _truncate(message: str) -> str:
    if not message:
        return ""
    if len(message) <= MAX_MESSAGE_CHARS:
        return message
    return message[:MAX_MESSAGE_CHARS] + "\n…[truncated]"


def _build_filter_pattern(level: str, search: str) -> str:
    """CloudWatch filter pattern. Empty string = match everything.

    We use the simple `"quoted text"` form for substring matching. When
    both level and search are supplied we AND them via `?` logic — but
    CloudWatch's basic pattern syntax can't AND literal substrings cleanly,
    so we prefer a single filter (level OR search, not both). When both
    are set, we apply the level filter in CloudWatch and do the search
    in-process over the returned page — keeps this predictable.
    """
    if level and level != "ALL":
        if level == "WARN":
            # Accept both `[WARN]` and `[WARNING]`.
            return '?"[WARN]" ?"[WARNING]"'
        return f'"[{level}]"'
    if search:
        return f'"{search}"'
    return ""


@router.get("/api/workspaces/<wsId>/agents/<agentId>/logs")
def get_agent_logs(wsId: str, agentId: str):
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
    since = (qs.get("since") or "1h").strip()
    if since not in _SINCE_MIN:
        return bad_request("Invalid 'since' — must be one of 15m, 1h, 6h, 24h")

    level = (qs.get("level") or "ALL").strip().upper()
    if level not in _VALID_LEVELS:
        return bad_request("Invalid 'level' — must be one of ALL, ERROR, WARN, INFO")

    search = (qs.get("search") or "").strip()
    if search and not _SEARCH_RE.match(search):
        return bad_request("Invalid 'search' — letters/digits/basic punctuation only")

    cursor = (qs.get("cursor") or "").strip() or None

    # CloudWatch FilterLogEvents uses ms since epoch for start/endTime.
    import time as _time
    now_ms = int(_time.time() * 1000)
    start_ms = now_ms - _SINCE_MIN[since] * 60_000

    log_group = f"/aws/bedrock-agentcore/runtimes/{agentId}-DEFAULT"
    filter_pattern = _build_filter_pattern(level, search)

    logs = _get_logs()
    kwargs = {
        "logGroupName": log_group,
        "startTime": start_ms,
        "endTime": now_ms,
        "limit": MAX_EVENTS,
    }
    if filter_pattern:
        kwargs["filterPattern"] = filter_pattern
    if cursor:
        kwargs["nextToken"] = cursor

    try:
        resp = logs.filter_log_events(**kwargs)
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        # Log group doesn't exist yet — agent hasn't been invoked. Return
        # empty result rather than 404; the UI can show "no logs yet".
        if code == "ResourceNotFoundException":
            return success({"events": []})
        logger.exception("filter_log_events failed", extra={"error_code": code})
        return internal_error("Failed to read logs")

    raw_events = resp.get("events", [])
    events: list[dict] = []
    # If user supplied both level and search, we filtered by level in CW
    # and now filter by substring in-process over the page.
    post_search = search if (level and level != "ALL" and search) else ""
    for ev in raw_events:
        msg = ev.get("message", "") or ""
        if post_search and post_search.lower() not in msg.lower():
            continue
        events.append({
            "timestamp": ev.get("timestamp", 0),
            "message": _truncate(msg),
            "level": _parse_level(msg),
            "logStream": ev.get("logStreamName", ""),
        })

    body: dict = {"events": events}
    next_token = resp.get("nextToken")
    if next_token:
        body["nextCursor"] = next_token
    return success(body)
