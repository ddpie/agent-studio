"""check_agent_logs — Read CloudWatch logs for a sub-agent to diagnose issues."""

import json
import time
from datetime import datetime, timezone

import boto3
from strands import tool

from config import REGION


def _resolve_agent_id(agent_id_or_name: str) -> tuple[str, str]:
    """Resolve agent name to agent_id. Returns (agent_id, log_group)."""
    control = boto3.client("bedrock-agentcore-control", region_name=REGION)

    # If it looks like a full runtime ID (contains dash + random chars), use directly
    if "-" in agent_id_or_name and len(agent_id_or_name) > 20:
        log_group = f"/aws/bedrock-agentcore/runtimes/{agent_id_or_name}-DEFAULT"
        return agent_id_or_name, log_group

    # Otherwise try to find by listing runtimes
    try:
        resp = control.list_agent_runtimes(maxResults=50)
        for rt in resp.get("agentRuntimeSummaries", []):
            if rt["agentRuntimeName"] == agent_id_or_name:
                rid = rt["agentRuntimeId"]
                log_group = f"/aws/bedrock-agentcore/runtimes/{rid}-DEFAULT"
                return rid, log_group
    except Exception:
        pass

    # Fallback: assume it's an agent_id
    log_group = f"/aws/bedrock-agentcore/runtimes/{agent_id_or_name}-DEFAULT"
    return agent_id_or_name, log_group


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
    logs_client = boto3.client("logs", region_name=REGION)

    resolved_id, log_group = _resolve_agent_id(agent_id)

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
