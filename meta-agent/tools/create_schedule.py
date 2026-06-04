"""create_schedule — Create a cron schedule to trigger an agent via EventBridge."""

import json

import boto3
from config import ACCOUNT_ID, REGION, SCHEDULER_TARGET_ROLE_ARN
from strands import tool

from tools._scope import ROLE_EDITOR, ensure_agent_in_workspace

# The scheduler target has to go through a Lambda middleman because
# EventBridge Scheduler's target service list doesn't include
# bedrock-agentcore as of 2026-05 (setting Target.Arn to an AgentCore
# runtime ARN directly returns "bedrock-agentcore is not a supported
# service for a target"). agent-studio-schedule-runner is provisioned
# by CDK (infra/lib/constructs/scheduler.ts) and invokes the runtime
# via InvokeAgentRuntime using the caller payload.
_SCHEDULE_RUNNER_LAMBDA_ARN = f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:agent-studio-schedule-runner"


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
    record, err = ensure_agent_in_workspace(agent_id, min_role=ROLE_EDITOR)
    if err:
        return json.dumps(err)

    scheduler = boto3.client("scheduler", region_name=REGION)
    workspace_id = (record or {}).get("workspace_id") or ""

    agent_arn = f"arn:aws:bedrock-agentcore:{REGION}:{ACCOUNT_ID}:runtime/{agent_id}"

    # Match the Input shape the schedule-runner Lambda expects. See
    # lambda/schedule-runner/handler.mjs — it reads AgentRuntimeArn +
    # RuntimeSessionId and wraps the Payload before calling
    # InvokeAgentRuntime. The substitution `<aws.scheduler.scheduled-time>`
    # gets replaced by EventBridge at trigger time so every run gets a
    # unique session id.
    session_id = f"sched-{schedule_name}-<aws.scheduler.scheduled-time>"
    inner_payload = {
        "prompt": prompt,
        "__schedule_name": schedule_name,
        "session_id": session_id,
        "workspace_id": workspace_id,
    }
    target_input = {
        "AgentRuntimeArn": agent_arn,
        "RuntimeSessionId": session_id,
        "Payload": json.dumps(inner_payload, ensure_ascii=False),
    }

    resp = scheduler.create_schedule(
        Name=schedule_name,
        ScheduleExpression=cron_expression,
        FlexibleTimeWindow={"Mode": "OFF"},
        Target={
            "Arn": _SCHEDULE_RUNNER_LAMBDA_ARN,
            "RoleArn": SCHEDULER_TARGET_ROLE_ARN,
            "Input": json.dumps(target_input, ensure_ascii=False),
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
