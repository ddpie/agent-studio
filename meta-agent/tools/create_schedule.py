"""create_schedule — Create a cron schedule to trigger an agent via EventBridge."""

import json

import boto3
from strands import tool

from config import REGION, ACCOUNT_ID, SCHEDULER_TARGET_ROLE_ARN
from tools._scope import ensure_agent_in_workspace, ROLE_EDITOR


@tool
def create_schedule(
    schedule_name: str,
    agent_id: str,
    cron_expression: str,
    prompt: str,
) -> str:
    """Create a scheduled trigger for an agent using EventBridge Scheduler.

    Args:
        schedule_name: Name for the schedule (alphanumeric and hyphens).
        agent_id: The ID of the agent to invoke on schedule.
        cron_expression: Cron expression, e.g. "cron(0 9 * * ? *)" for daily 9am UTC.
        prompt: The prompt to send to the agent on each trigger.

    Returns:
        JSON with schedule_arn and status.
    """
    _record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_EDITOR)
    if err:
        return json.dumps(err)

    scheduler = boto3.client("scheduler", region_name=REGION)

    agent_arn = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/{agent_id}"

    resp = scheduler.create_schedule(
        Name=schedule_name,
        ScheduleExpression=cron_expression,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": agent_arn,
            "RoleArn": SCHEDULER_TARGET_ROLE_ARN,
            "Input": json.dumps({"prompt": prompt}),
        },
    )

    result = {
        "schedule_name": schedule_name,
        "schedule_arn": resp["ScheduleArn"],
        "cron": cron_expression,
        "target_agent": agent_id,
        "status": "created",
    }

    return json.dumps(result, indent=2)
