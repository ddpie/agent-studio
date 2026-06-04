"""check_agent_logs — Read CloudWatch logs for a agent to diagnose issues."""

import json
import time
from datetime import datetime, timezone

import boto3
from config import REGION
from strands import tool

from tools._scope import ROLE_VIEWER, ensure_agent_in_workspace, list_workspace_agents

try:
    # Python 3.9+ stdlib. Available on the AgentCore runtime image.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — only matters pre-3.9
    ZoneInfo = None  # type: ignore

# AgentCore / OTEL log records use varying field names. This is the
# fallback chain for the human-readable body and severity label so we
# don't render `[HH:MM:SS]  ` with an empty payload.
_MESSAGE_KEYS = ("message", "body", "msg", "Message", "Body")
_LEVEL_KEYS = ("level", "severityText", "levelname", "Severity")


def _extract_text(parsed: dict) -> tuple[str, str]:
    """Pull a (level, message) pair out of a parsed JSON log record.

    Returns empty strings if nothing usable was found — callers decide
    whether to fall back to the raw string.
    """
    level = ""
    for k in _LEVEL_KEYS:
        v = parsed.get(k)
        if isinstance(v, str) and v:
            level = v
            break
    message = ""
    for k in _MESSAGE_KEYS:
        v = parsed.get(k)
        if isinstance(v, str) and v:
            message = v
            break
    # OTEL log bodies sometimes nest under {"body": {"stringValue": "..."}}.
    if not message:
        body = parsed.get("body")
        if isinstance(body, dict):
            sv = body.get("stringValue")
            if isinstance(sv, str):
                message = sv
    return level, message


def _resolve_agent_id(agent_id_or_name: str) -> str | None:
    """Resolve a possibly-a-name to a concrete agent_id, scoped to the
    caller's workspace.

    Returns the agent_id, or None if no match was found (the caller
    reports the 'not found' error — this helper doesn't distinguish
    wrong-name from wrong-workspace, since the listing it searches is
    already workspace-scoped).
    """
    if "-" in agent_id_or_name and len(agent_id_or_name) > 20:
        return agent_id_or_name
    for record in list_workspace_agents():
        aid = record.get("agentId", "")
        if aid == agent_id_or_name:
            return aid
        if record.get("name") == agent_id_or_name or record.get("agentName") == agent_id_or_name:
            return aid
    return None


@tool
def check_agent_logs(agent_id: str, minutes: int = 30, tz: str = "UTC") -> str:
    """Read recent CloudWatch logs for a deployed agent. Useful for diagnosing errors.

    You can pass either the agent runtime ID (e.g., "myAgent-abc123XYZ")
    or just the agent name (e.g., "myAgent") and it will be resolved automatically.

    Args:
        agent_id: The agent runtime ID or agent name.
        minutes: How many minutes of recent logs to fetch. Default 30.
        tz: IANA timezone name (e.g. "Asia/Shanghai", "America/Los_Angeles")
            for formatting timestamps in the output. Defaults to "UTC".
            Use the user's local zone when they ask about "just now" /
            "刚才" to avoid mental math; keep UTC for batch analysis.

    Returns:
        Recent log entries as text, or error message if no logs found.
    """
    resolved_id = _resolve_agent_id(agent_id)
    if not resolved_id:
        return json.dumps({"error": f"Agent {agent_id} not found in this workspace."})
    _record, err = ensure_agent_in_workspace(resolved_id, min_role=ROLE_VIEWER)
    if err:
        return json.dumps(err)

    # Resolve the requested timezone. Fall back to UTC if the name is
    # unknown rather than erroring the tool call — the Meta-Agent shouldn't
    # have to retry a log pull over a typo.
    tz_label = tz or "UTC"
    try:
        display_tz = ZoneInfo(tz_label) if ZoneInfo and tz_label != "UTC" else timezone.utc
        if display_tz is timezone.utc:
            tz_label = "UTC"
    except Exception:
        display_tz = timezone.utc
        tz_label = "UTC"

    log_group = f"/aws/bedrock-agentcore/runtimes/{resolved_id}-DEFAULT"
    logs_client = boto3.client("logs", region_name=REGION)

    end_time = int(time.time() * 1000)
    start_time = end_time - (minutes * 60 * 1000)

    try:
        resp = logs_client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time,
            endTime=end_time,
            limit=50,
        )

        events = resp.get("events", [])
        if not events:
            return json.dumps({
                "message": f"No logs found in the last {minutes} minutes.",
                "log_group": log_group,
                "resolved_agent_id": resolved_id,
            })

        log_lines = []
        for event in events:
            ts = event.get("timestamp", 0)
            msg = event.get("message", "").strip()
            # Full date + timezone suffix so humans can cross-reference
            # with CloudWatch without reading "is that UTC or local?".
            dt = datetime.fromtimestamp(ts / 1000, tz=display_tz).strftime(
                f"%Y-%m-%d %H:%M:%S {tz_label}"
            )

            # Parse JSON log entries for cleaner output. Multiple field
            # conventions coexist: stdlib `logging` uses message/level,
            # powertools uses message/level, AgentCore OTEL export uses
            # body/severityText. _extract_text covers them all.
            try:
                parsed = json.loads(msg)
                level, message = _extract_text(parsed)
                error_type = parsed.get("errorType") or ""
                error_msg = parsed.get("errorMessage") or ""
                if error_type:
                    log_lines.append(f"[{dt}] {level} {error_type}: {error_msg}".rstrip())
                elif message:
                    log_lines.append(f"[{dt}] {level} {message}".rstrip())
                else:
                    # Fallback: show the raw record (trimmed) so empty
                    # lines don't slip through when the field shape is
                    # something we don't recognize yet.
                    snippet = msg[:400] + ("..." if len(msg) > 400 else "")
                    log_lines.append(f"[{dt}] {snippet}")
            except (json.JSONDecodeError, TypeError):
                if msg and "Invalid HTTP request" not in msg:
                    log_lines.append(f"[{dt}] {msg}")

        return "\n".join(log_lines) if log_lines else json.dumps({
            "message": "Only noise entries found (no meaningful logs).",
            "log_group": log_group,
        })

    except logs_client.exceptions.ResourceNotFoundException:
        return json.dumps({
            "error": f"Log group not found: {log_group}",
            "hint": "The agent may not have been invoked yet, or the name/ID is incorrect.",
            "resolved_agent_id": resolved_id,
        })
    except Exception as e:
        return json.dumps({"error": str(e), "log_group": log_group})
