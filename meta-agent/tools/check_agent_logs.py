"""check_agent_logs — Read CloudWatch logs for a sub-agent to diagnose issues."""

import json
import time
from datetime import datetime, timezone

import boto3
from strands import tool

from config import REGION
from tools._scope import ensure_agent_in_workspace, list_workspace_agents, ROLE_VIEWER


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
def check_agent_logs(agent_id: str, minutes: int = 30) -> str:
    """Read recent CloudWatch logs for a deployed agent. Useful for diagnosing errors.

    You can pass either the agent runtime ID (e.g., "myAgent-abc123XYZ")
    or just the agent name (e.g., "myAgent") and it will be resolved automatically.

    Args:
        agent_id: The agent runtime ID or agent name.
        minutes: How many minutes of recent logs to fetch. Default 30.

    Returns:
        Recent log entries as text, or error message if no logs found.
    """
    resolved_id = _resolve_agent_id(agent_id)
    if not resolved_id:
        return json.dumps({"error": f"Agent {agent_id} not found in this workspace."})
    _record, err = ensure_agent_in_workspace(resolved_id, min_role=ROLE_VIEWER)
    if err:
        return json.dumps(err)

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
            dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%H:%M:%S")

            # Parse JSON log entries for cleaner output
            try:
                parsed = json.loads(msg)
                level = parsed.get("level", "")
                message = parsed.get("message", "")
                error_type = parsed.get("errorType", "")
                error_msg = parsed.get("errorMessage", "")
                if error_type:
                    log_lines.append(f"[{dt}] {level} {error_type}: {error_msg}")
                else:
                    log_lines.append(f"[{dt}] {level} {message}")
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
