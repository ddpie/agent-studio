"""Evaluator (LLM-as-Judge) — workspace-scoped online config + read endpoints."""
import re

import boto3
from aws_lambda_powertools import Logger
from aws_lambda_powertools.event_handler.api_gateway import Router
from botocore.exceptions import ClientError

from shared.config import EVALUATOR_ROLE_ARN, REGION, SPANS_LOG_GROUP

router = Router()
logger = Logger(child=True)

_control = None

# Keep small + opinionated for MVP. Users don't pick which evaluators fire;
# we bake in a reasonable default. Sprint 3 can surface per-workspace
# customization.
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


def _config_name_for(workspace_id: str) -> str:
    """Deterministic per-workspace config name.

    API regex: [a-zA-Z][a-zA-Z0-9_]{0,47}. Must start with a letter,
    only alphanumeric + underscore, max 48 chars. Workspace UUIDs use
    hyphens which are invalid — strip them.
    """
    safe = re.sub(r"[^A-Za-z0-9]", "", workspace_id)[:32]
    return f"agentstudio_ws_{safe}"


def create_eval_config_for_workspace(workspace_id: str) -> str:
    """Fire-and-forget: create OnlineEvaluationConfig for a workspace.

    Returns the config name (also the idempotency handle). Safe to call
    repeatedly — if the config already exists we swallow ConflictException
    and return the name.

    Synchronous to the API (returns in <1s) but AgentCore creates the
    resource asynchronously (status=CREATING ~minutes). Caller must not
    block on READY.
    """
    if not EVALUATOR_ROLE_ARN:
        logger.warning("EVALUATOR_ROLE_ARN not configured; skipping eval config creation")
        return ""

    name = _config_name_for(workspace_id)
    try:
        _get_control().create_online_evaluation_config(
            onlineEvaluationConfigName=name,
            description=f"Online evaluation for Agent Studio workspace {workspace_id}",
            rule={
                "samplingConfig": {"samplingPercentage": 100.0},
                "filters": [],
            },
            dataSourceConfig={
                "cloudWatchLogs": {
                    "logGroupNames": [SPANS_LOG_GROUP],
                    "serviceNames": ["bedrock-agentcore"],
                },
            },
            evaluators=DEFAULT_EVALUATORS,
            evaluationExecutionRoleArn=EVALUATOR_ROLE_ARN,
            enableOnCreate=True,
        )
        logger.info("created online eval config", extra={"config_name": name, "workspace_id": workspace_id})
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code")
        if code == "ConflictException":
            logger.info("eval config already exists", extra={"config_name": name})
        else:
            logger.exception("create_online_evaluation_config failed",
                             extra={"config_name": name, "error_code": code})
            raise
    return name
